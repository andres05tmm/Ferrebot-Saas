"""Servicio de pedidos a proveedor: el cronómetro de lead time y la recepción transaccional.

Orquesta dominio puro sobre los servicios existentes (máxima reutilización, cero SQL aquí):
  - la COMPRA real (ENTRADA de inventario + costo promedio) la registra `ComprasService.registrar`
    con la key natural `pedido-recibo:{id}` (recibir dos veces = replay, invariante #7 intacto);
  - la CUENTA POR PAGAR la crea el repo de proveedores (crédito, o anticipado con remanente); el
    anticipo ya entregado nace como ABONO automático de esa factura (contabilidad completa);
  - el PAGO de contado/remanente egresa de la caja vía `CajaService.registrar_movimiento` (key
    `pedido-recibo:{id}`, exige caja abierta) — NO es un gasto: la mercancía es inventario, no
    gasto operativo (no infla gastos vs CMV);
  - el CUADRE de inventario progresivo (cantidad_fisica) reusa `InventarioService.contar`
    (set-to-absolute, key `pedido-cuadre:{id}:{producto}`), que sella `inventario.cuadrado_at`.

Todo ocurre en la MISMA sesión del tenant: un fallo (p. ej. caja cerrada) revierte compra, factura
y cuadres — sin efectos parciales. Hora Colombia (now_co) siempre.
"""
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal

from core.config.timezone import now_co, today_co
from core.money import cuantizar
from modules.caja.service import CajaService
from modules.compras.repository import SqlComprasRepository
from modules.compras.schemas import CompraCrear, CompraItemCrear
from modules.compras.schemas import ProveedorRef as CompraProveedorRef
from modules.compras.service import ComprasService
from modules.inventario.service import InventarioService
from modules.pedidos_proveedor.errors import (
    IdempotenciaConflicto,
    PedidoInexistente,
    PedidoNoEditable,
    RecepcionInvalida,
)
from modules.pedidos_proveedor.models import PedidoProveedor
from modules.pedidos_proveedor.repository import SqlPedidosProveedorRepository
from modules.pedidos_proveedor.schemas import (
    CuadreLinea,
    MetricasProveedor,
    PedidoCrear,
    PedidoEditar,
    PedidoLeer,
    RecepcionLeer,
    RecibirPedido,
)


@dataclass(frozen=True, slots=True)
class ResultadoPedido:
    pedido: PedidoLeer
    replay: bool


def _horas(desde: datetime, hasta: datetime) -> float:
    return round((hasta - desde).total_seconds() / 3600.0, 2)


# --- Cron de pedidos demorados (F6, molde `pagar.procesar_avisos`) -----------------------------

# Cadencia del dedup: un pedido demorado no se re-avisa dentro de esta ventana (el cron corre
# diario; la cadencia protege además contra corridas dobles).
CADENCIA_AVISO_HORAS = 24


@dataclass(frozen=True, slots=True)
class PedidoDemorado:
    """Un pedido en camino que ya se pasó de su expectativa de llegada."""

    pedido_id: int
    proveedor_id: int
    proveedor_nombre: str | None
    horas_transcurridas: float
    promedio_proveedor_horas: float | None
    fecha_estimada: date | None
    motivo: str                        # 'estimada' (pasó la fecha prometida) | 'promedio'


@dataclass(frozen=True, slots=True)
class AvisoPedidosDemorados:
    """El resumen que recibe el callback de envío: qué pedidos van tarde HOY."""

    pedidos: tuple[PedidoDemorado, ...]
    generado_en: datetime


# Callback que entrega el aviso al dueño (el worker lo cablea a SSE). True = envío exitoso: solo
# entonces se sella el dedup — un fallo de red se reintenta en la próxima corrida (patrón pagar).
EnviarAvisoDemorados = Callable[[AvisoPedidosDemorados], Awaitable[bool]]


async def procesar_avisos_demorados(
    repo: SqlPedidosProveedorRepository,
    *,
    ahora: datetime,
    enviar: EnviarAvisoDemorados,
    cadencia_horas: int = CADENCIA_AVISO_HORAS,
) -> int:
    """Una corrida determinista del cron sobre la base del tenant. Devuelve cuántos pedidos avisó.

    Un pedido en estado `pedido` está DEMORADO cuando ya pasó su expectativa de llegada:
      - con `fecha_estimada` (promesa explícita del proveedor): hoy > esa fecha — la promesa gana
        sobre cualquier promedio;
      - sin fecha: edad > promedio histórico del proveedor (`promedio_lead_time_horas`);
      - sin fecha NI historial no hay vara contra qué medir → no se avisa (cero falsas alarmas).

    Dedup por `ultimo_aviso_at` + cadencia. Se arma UN resumen; solo un `enviar` exitoso sella el
    dedup de TODOS los incluidos. No toma deps de compras/caja: el motor solo lee y sella.
    """
    pendientes = await repo.listar(estado="pedido")
    if not pendientes:
        return 0

    nombres = await repo.nombres_proveedores(list({p.proveedor_id for p in pendientes}))
    promedios: dict[int, float | None] = {}
    cadencia = timedelta(hours=cadencia_horas)
    demorados: list[PedidoDemorado] = []
    for p in pendientes:
        if p.ultimo_aviso_at is not None and ahora - p.ultimo_aviso_at < cadencia:
            continue                          # cadencia: ya se avisó de este pedido hace poco
        if p.proveedor_id not in promedios:
            promedios[p.proveedor_id] = await repo.promedio_lead_time_horas(p.proveedor_id)
        promedio = promedios[p.proveedor_id]
        edad = _horas(p.fecha_pedido, ahora)
        if p.fecha_estimada is not None:
            if ahora.date() <= p.fecha_estimada:
                continue
            motivo = "estimada"
        elif promedio is not None and edad > promedio:
            motivo = "promedio"
        else:
            continue                          # sin promesa ni historial: no hay vara → sin aviso
        demorados.append(PedidoDemorado(
            pedido_id=p.id, proveedor_id=p.proveedor_id,
            proveedor_nombre=nombres.get(p.proveedor_id),
            horas_transcurridas=edad, promedio_proveedor_horas=promedio,
            fecha_estimada=p.fecha_estimada, motivo=motivo,
        ))

    if not demorados:
        return 0
    aviso = AvisoPedidosDemorados(pedidos=tuple(demorados), generado_en=ahora)
    if not await enviar(aviso):
        return 0                              # envío fallido: no se sella (se reintenta luego)
    await repo.sellar_avisos([d.pedido_id for d in demorados], cuando=ahora)
    return len(demorados)


class PedidosProveedorService:
    def __init__(
        self,
        repo: SqlPedidosProveedorRepository,
        *,
        compras: ComprasService,
        compras_repo: SqlComprasRepository,
        proveedores,   # SqlProveedoresRepository (estructural: existe/crear_factura/crear_abono)
        caja: CajaService,
        inventario: InventarioService,
    ) -> None:
        self._repo = repo
        self._compras = compras
        self._compras_repo = compras_repo
        self._proveedores = proveedores
        self._caja = caja
        self._inventario = inventario

    # --- Alta (arranca el cronómetro) ----------------------------------------

    async def crear(
        self, datos: PedidoCrear, *, usuario_id: int | None, modo_empresa: bool = False
    ) -> ResultadoPedido:
        if datos.idempotency_key:
            previo = await self._repo.por_key(datos.idempotency_key)
            if previo is not None:
                if not self._misma_alta(previo, datos):
                    raise IdempotenciaConflicto(datos.idempotency_key)
                return ResultadoPedido(await self._leer(previo), replay=True)

        proveedor_id = await self._compras_repo.get_or_create_proveedor(
            proveedor_id=datos.proveedor.id, nombre=datos.proveedor.nombre, nit=datos.proveedor.nit,
        )
        pedido = await self._repo.crear(
            proveedor_id=proveedor_id, fecha_pedido=now_co(), fecha_estimada=datos.fecha_estimada,
            descripcion=datos.descripcion, monto_estimado=datos.monto_estimado,
            anticipo=datos.anticipo,
            condicion_pago="anticipado" if datos.anticipo else None,
            usuario_id=usuario_id, notas=datos.notas, idempotency_key=datos.idempotency_key,
            lineas=[(ln.producto_id, ln.descripcion, ln.cantidad, ln.costo_estimado)
                    for ln in datos.lineas],
        )
        if datos.anticipo and datos.anticipo_desde_caja:
            # El anticipo sale del cajón AHORA (exige caja abierta). Key natural por pedido: un solo
            # egreso aunque el alta se reintente (además el alta misma ya es idempotente por su key).
            res = await self._caja.registrar_movimiento(
                usuario_id=usuario_id, tipo="egreso", monto=datos.anticipo,
                concepto=f"Anticipo pedido proveedor #{pedido.id}",
                idempotency_key=f"pedido-anticipo:{pedido.id}", modo_empresa=modo_empresa,
            )
            await self._repo.set_anticipo_movimiento(pedido, res.movimiento.id)
        return ResultadoPedido(await self._leer(pedido), replay=False)

    @staticmethod
    def _misma_alta(previo: PedidoProveedor, datos: PedidoCrear) -> bool:
        """Sustancia del alta: descripción, monto estimado y anticipo (no re-resuelve proveedor)."""
        return (
            (previo.descripcion or None) == (datos.descripcion or None)
            and previo.monto_estimado == datos.monto_estimado
            and previo.anticipo == datos.anticipo
        )

    # --- Edición / cancelación (solo en camino) -------------------------------

    async def editar(self, pedido_id: int, datos: PedidoEditar) -> PedidoLeer:
        pedido = await self._repo.obtener(pedido_id, lock=True)
        if pedido is None:
            raise PedidoInexistente(pedido_id)
        if pedido.estado != "pedido":
            raise PedidoNoEditable(pedido_id, pedido.estado)
        if datos.descripcion is not None:
            pedido.descripcion = datos.descripcion
        if datos.monto_estimado is not None:
            pedido.monto_estimado = datos.monto_estimado
        if datos.fecha_estimada is not None:
            pedido.fecha_estimada = datos.fecha_estimada
        if datos.notas is not None:
            pedido.notas = datos.notas
        if datos.lineas is not None:
            await self._repo.reemplazar_lineas(
                pedido,
                [(ln.producto_id, ln.descripcion, ln.cantidad, ln.costo_estimado)
                 for ln in datos.lineas],
            )
        return await self._leer(pedido)

    async def cancelar(self, pedido_id: int, *, usuario_id: int | None) -> PedidoLeer:
        pedido = await self._repo.obtener(pedido_id, lock=True)
        if pedido is None:
            raise PedidoInexistente(pedido_id)
        if pedido.estado != "pedido":
            raise PedidoNoEditable(pedido_id, pedido.estado)
        # El anticipo entregado NO se revierte automático (el dinero ya está en manos del proveedor);
        # queda la nota visible para gestionarlo con él (devolución o abono a otro pedido).
        nota = None
        if pedido.anticipo:
            nota = (f"CANCELADO con anticipo de {pedido.anticipo} ya entregado — "
                    "gestionar devolución con el proveedor.")
        return await self._leer(await self._repo.marcar_cancelado(pedido, nota=nota))

    # --- Recepción (para el cronómetro; mueve stock/dinero) -------------------

    async def recibir(
        self,
        pedido_id: int,
        datos: RecibirPedido,
        *,
        usuario_id: int | None,
        modo_empresa: bool = False,
    ) -> RecepcionLeer:
        pedido = await self._repo.obtener(pedido_id, lock=True)
        if pedido is None:
            raise PedidoInexistente(pedido_id)
        if pedido.estado == "cancelado":
            raise PedidoNoEditable(pedido_id, "cancelado")

        total = cuantizar(sum((ln.cantidad * ln.costo for ln in datos.lineas), Decimal("0")))
        key_natural = f"pedido-recibo:{pedido.id}"

        if pedido.estado == "recibido":
            # Reintento (doble clic / retry de red): misma sustancia → replay; distinta → conflicto.
            return await self._replay_recepcion(pedido, datos, key_natural)

        anticipo = pedido.anticipo or Decimal("0")
        remanente = cuantizar(total - anticipo) if anticipo > 0 else total
        if datos.condicion_pago == "anticipado":
            if anticipo <= 0:
                raise RecepcionInvalida(
                    f"El pedido {pedido.id} no registró anticipo: usa contado o crédito"
                )
            if remanente > 0 and not datos.pago_desde_caja and not datos.numero_factura:
                raise RecepcionInvalida(
                    f"La mercancía costó {total} y el anticipo fue {anticipo}: indica cómo se paga "
                    "el remanente (desde caja o a crédito con número de factura)"
                )

        # 1) La compra real: ENTRADA de inventario + costo promedio (ComprasService, key natural).
        # stock previo por línea ANTES de la entrada (para el reporte de cuadre).
        stock_previo = {
            ln.producto_id: await self._repo.stock_actual(ln.producto_id) for ln in datos.lineas
        }
        res_compra = await self._compras.registrar(
            CompraCrear(
                proveedor=CompraProveedorRef(id=pedido.proveedor_id),
                items=[CompraItemCrear(producto_id=ln.producto_id, cantidad=ln.cantidad, costo=ln.costo)
                       for ln in datos.lineas],
                idempotency_key=key_natural,
            ),
            usuario_id=usuario_id,
        )

        # 2) La deuda: crédito (total) o anticipado con remanente a crédito. El anticipo ya entregado
        # nace como abono automático → pendiente = lo que de verdad se debe.
        factura_id: str | None = None
        crea_factura = datos.condicion_pago == "credito" or (
            datos.condicion_pago == "anticipado" and datos.numero_factura and remanente > 0
        )
        if crea_factura:
            factura_id = datos.numero_factura or f"PED-{pedido.id}"
            if await self._proveedores.existe(factura_id):
                raise RecepcionInvalida(f"La factura {factura_id!r} ya existe en cuentas por pagar")
            nombre = await self._repo.nombre_proveedor(pedido.proveedor_id) or "Proveedor"
            await self._proveedores.crear_factura(
                factura_id=factura_id, proveedor=nombre,
                descripcion=f"Pedido proveedor #{pedido.id}",
                total=total, fecha=today_co(), fecha_vencimiento=datos.fecha_vencimiento,
                usuario_id=usuario_id,
            )
            if anticipo > 0:
                await self._proveedores.crear_abono_y_recalcular(
                    factura_id=factura_id, monto=min(anticipo, total), fecha=today_co(),
                )

        # 3) El pago desde el cajón: contado (total) o remanente del anticipado. Exige caja abierta;
        # si no la hay, la excepción revierte TODO (compra incluida) — sin efectos parciales.
        egreso = None
        if datos.condicion_pago == "contado" and datos.pago_desde_caja:
            egreso = total
        elif datos.condicion_pago == "anticipado" and datos.pago_desde_caja and remanente > 0:
            egreso = remanente
        if egreso is not None and egreso > 0:
            await self._caja.registrar_movimiento(
                usuario_id=usuario_id, tipo="egreso", monto=egreso,
                concepto=f"Pedido proveedor #{pedido.id} — pago mercancía",
                idempotency_key=key_natural, modo_empresa=modo_empresa,
            )

        # 4) Cuadre de inventario progresivo: fija el stock al físico contado (sella cuadrado_at).
        for ln in datos.lineas:
            if ln.cantidad_fisica is not None:
                await self._inventario.contar(
                    producto_id=ln.producto_id, cantidad_contada=ln.cantidad_fisica,
                    motivo=f"Cuadre al recibir pedido proveedor #{pedido.id}",
                    usuario_id=usuario_id,
                    idempotency_key=f"pedido-cuadre:{pedido.id}:{ln.producto_id}",
                )

        # 5) El pedido queda recibido: para el cronómetro.
        pedido = await self._repo.marcar_recibido(
            pedido, fecha_recepcion=now_co(), compra_id=res_compra.compra.id,
            factura_proveedor_id=factura_id, condicion_pago=datos.condicion_pago,
            notas=datos.notas,
        )
        lineas = [
            CuadreLinea(
                producto_id=ln.producto_id,
                stock_previo=stock_previo[ln.producto_id],
                stock_resultante=await self._repo.stock_actual(ln.producto_id),
                cuadrado=ln.cantidad_fisica is not None,
            )
            for ln in datos.lineas
        ]
        return RecepcionLeer(
            pedido=await self._leer(pedido), compra_id=res_compra.compra.id,
            factura_proveedor_id=factura_id, lineas=lineas, replay=False,
        )

    async def _replay_recepcion(
        self, pedido: PedidoProveedor, datos: RecibirPedido, key_natural: str
    ) -> RecepcionLeer:
        """Pedido YA recibido: misma sustancia (líneas de la compra anclada) → replay sin efectos;
        sustancia distinta → PedidoNoEditable (no hay 'recibir otra vez con otros números')."""
        existente = await self._compras_repo.buscar_por_idempotency(key_natural)
        if existente is None or pedido.compra_id is None:
            raise PedidoNoEditable(pedido.id, "recibido")

        def _clave(t: tuple) -> tuple:
            return (t[0] if t[0] is not None else -1, t[1], t[2])

        entrantes = sorted(((ln.producto_id, ln.cantidad, ln.costo) for ln in datos.lineas), key=_clave)
        previos = sorted(existente.items, key=_clave)
        if entrantes != previos:
            raise PedidoNoEditable(pedido.id, "recibido")
        return RecepcionLeer(
            pedido=await self._leer(pedido), compra_id=pedido.compra_id,
            factura_proveedor_id=pedido.factura_proveedor_id, lineas=[], replay=True,
        )

    # --- Lecturas -------------------------------------------------------------

    async def listar(self, *, estado: str | None = None) -> list[PedidoLeer]:
        pedidos = await self._repo.listar(estado=estado)
        nombres = await self._repo.nombres_proveedores(list({p.proveedor_id for p in pedidos}))
        promedios: dict[int, float | None] = {}
        salida = []
        for p in pedidos:
            if p.proveedor_id not in promedios:
                promedios[p.proveedor_id] = await self._repo.promedio_lead_time_horas(p.proveedor_id)
            salida.append(self._a_leer(
                p, proveedor_nombre=nombres.get(p.proveedor_id),
                promedio=promedios[p.proveedor_id],
            ))
        return salida

    async def metricas(self) -> list[MetricasProveedor]:
        ahora = now_co()
        filas = await self._repo.metricas_por_proveedor()
        return [
            MetricasProveedor(
                proveedor_id=f.proveedor_id, proveedor_nombre=f.proveedor_nombre,
                pedidos_recibidos=f.pedidos_recibidos,
                lead_time_promedio_horas=(
                    round(f.lead_time_promedio_horas, 2)
                    if f.lead_time_promedio_horas is not None else None
                ),
                ultima_entrega=f.ultima_entrega, pedidos_en_camino=f.pedidos_en_camino,
                mas_viejo_en_camino_horas=(
                    _horas(f.mas_viejo_en_camino, ahora)
                    if f.mas_viejo_en_camino is not None else None
                ),
            )
            for f in filas
        ]

    async def _leer(self, pedido: PedidoProveedor) -> PedidoLeer:
        nombre = await self._repo.nombre_proveedor(pedido.proveedor_id)
        promedio = await self._repo.promedio_lead_time_horas(pedido.proveedor_id)
        return self._a_leer(pedido, proveedor_nombre=nombre, promedio=promedio)

    @staticmethod
    def _a_leer(
        pedido: PedidoProveedor, *, proveedor_nombre: str | None, promedio: float | None
    ) -> PedidoLeer:
        base = PedidoLeer.model_validate(pedido)
        ahora = now_co()
        return base.model_copy(update={
            "proveedor_nombre": proveedor_nombre,
            "promedio_proveedor_horas": round(promedio, 2) if promedio is not None else None,
            "horas_transcurridas": (
                _horas(pedido.fecha_pedido, ahora) if pedido.estado == "pedido" else None
            ),
            "lead_time_horas": (
                _horas(pedido.fecha_pedido, pedido.fecha_recepcion)
                if pedido.fecha_recepcion is not None else None
            ),
        })
