"""Servicio de compras: orquesta get-or-create de proveedor, cálculo de total y registro.

Lógica de dominio (sin SQL): resuelve el proveedor, calcula el total en el SERVIDOR (Σ cantidad×costo)
y delega el registro transaccional (stock + costo + eventos) en el repositorio. La fecha y el rango
default usan hora Colombia (regla #4).
"""
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Protocol

from core.config.timezone import COLOMBIA_TZ, now_co, rango_dia_co, today_co
from core.money import cuantizar
from modules.compras.errors import (
    CompraInexistente,
    CompraNoCorregible,
    CorreccionInvalida,
    IdempotenciaConflicto,
)
from modules.inventario.precios import convertir_a_subunidad
from modules.compras.repository import (
    AnalisisPrecioRow,
    CompraIdempotente,
    ItemCompra,
    SqlComprasRepository,
    recalcular_promedio,
)
from modules.compras.schemas import (
    AjusteLinea,
    AnalisisPrecioProveedor,
    CompraCorregir,
    CompraCrear,
    CompraLeer,
    CorreccionLeer,
)
from services.calculations.resbalos import Resbalo, calcular_resbalo

# Ventana por defecto del análisis de precios de proveedor (mismo semestre de la alerta de precio).
_VENTANA_ANALISIS = timedelta(days=182)

# Ventana del historial de precios del proveedor (spec 10: "promedio de los últimos 6 meses").
_VENTANA_PRECIO_PROVEEDOR = timedelta(days=182)
# Umbral de la alerta de precio: un costo unitario > 15% sobre el promedio histórico dispara la señal.
_UMBRAL_PRECIO_PROVEEDOR = Decimal("1.15")


@dataclass(frozen=True, slots=True)
class ResultadoCompra:
    """Compra registrada + si fue un replay idempotente (la key ya existía con el mismo payload)."""

    compra: CompraLeer
    replay: bool


def _mismo_payload(
    existente: CompraIdempotente,
    items: list[ItemCompra],
    total: Decimal,
    proveedor_id: int | None,
    *,
    obra_id: int | None,
    es_viaje_material: bool,
) -> bool:
    """True si la compra previa (misma key) coincide con el payload entrante (líneas + total + prov. +
    imputación de obra).

    Compara la sustancia económica (cada línea producto/cantidad/costo y el total) Y la IMPUTACIÓN de la
    compra: `obra_id` y `es_viaje_material` cambian qué es la compra (si mueve stock o se imputa a una obra,
    y con qué margen), así que reusar una key con esos campos distintos es un conflicto, no un replay. El
    proveedor solo entra si el entrante lo dio por `id` explícito (resolver nombre/nit aquí crearía un
    proveedor antes de saber si es replay). Decimales comparan por valor: 10.000 == 10."""
    if total != existente.total:
        return False
    # Orden estable aun con `producto_id` NULL (viajes de material / imputadas a obra): None → -1 para no
    # comparar None con int al ordenar.
    def _clave(t: tuple) -> tuple:
        return (t[0] if t[0] is not None else -1, t[1], t[2])

    actuales = sorted(((it.producto_id, it.cantidad, it.costo) for it in items), key=_clave)
    previos = sorted(existente.items, key=_clave)
    if actuales != previos:
        return False
    if proveedor_id is not None and proveedor_id != existente.compra.proveedor_id:
        return False
    # Imputación: obra distinta o cambiar el marcador de viaje de material NO es el mismo payload.
    if obra_id != existente.compra.obra_id:
        return False
    if es_viaje_material != existente.compra.es_viaje_material:
        return False
    return True


def _enriquecer_resbalo(compra: CompraLeer) -> CompraLeer:
    """Completa `resbalo`/`resbalo_pct`/`resbalo_alerta` de una compra de viaje con `calcular_resbalo`.

    El costo del viaje ES `compra.total` (Σ cantidad×costo); recalcular desde la función pura mantiene UNA
    sola verdad del margen (el monto persistido y el % del reporte no divergen)."""
    if compra.precio_venta_cliente is None:
        return compra
    r = calcular_resbalo(compra.precio_venta_cliente, compra.total)
    return compra.model_copy(update={
        # Conserva el monto PERSISTIDO (MONEY4, 4 decimales); solo deriva % y alerta desde la función pura.
        "resbalo": compra.resbalo if compra.resbalo is not None else r.monto,
        "resbalo_pct": r.porcentaje,
        "resbalo_alerta": r.alerta,
    })


def _analisis_a_schema(f: AnalisisPrecioRow) -> AnalisisPrecioProveedor:
    """Deriva variación % y alerta de una fila del análisis (misma verdad que la alerta de precio 15%).

    `variacion_pct` = cuánto por encima del promedio ponderado quedó el costo máximo del período; `alerta`
    se dispara cuando ese máximo supera el umbral (>15%). Sin promedio (>0) no hay base: variación 0, sin
    alerta (default seguro)."""
    prom = f.costo_unitario_promedio
    if prom > 0:
        variacion = ((f.costo_unitario_max - prom) / prom * 100).quantize(Decimal("0.01"), ROUND_HALF_UP)
        alerta = f.costo_unitario_max > prom * _UMBRAL_PRECIO_PROVEEDOR
    else:
        variacion = Decimal("0.00")
        alerta = False
    return AnalisisPrecioProveedor(
        proveedor_id=f.proveedor_id,
        proveedor_nombre=f.proveedor_nombre,
        categoria=f.categoria,
        n_compras=f.n_compras,
        cantidad_total=f.cantidad_total,
        costo_unitario_promedio=cuantizar(prom),
        costo_unitario_min=cuantizar(f.costo_unitario_min),
        costo_unitario_max=cuantizar(f.costo_unitario_max),
        variacion_pct=variacion,
        alerta=alerta,
    )


def _fecha_compra(fecha: date | None) -> datetime:
    """Fecha de la compra como datetime aware Colombia: la dada (mediodía) o ahora."""
    if fecha is None:
        return now_co()
    return datetime.combine(fecha, time(12, 0), tzinfo=COLOMBIA_TZ)


def _rango_o_mes(desde: date | None, hasta: date | None) -> tuple[datetime, datetime]:
    """Ventana [inicio, fin] aware: rango dado o, si falta, el mes en curso (día 1 → hoy Colombia)."""
    hoy = today_co()
    return rango_dia_co(desde or hoy.replace(day=1), hasta or hoy)


class RetencionesAplicador(Protocol):
    """Puerto del motor de retenciones (lo cumple RetencionesService). Estructural, opcional.

    En la compra NOSOTROS somos agente retenedor: al registrarla se calculan/persisten las retenciones
    practicadas (ADR 0027) inline, en la MISMA transacción (`commit=False`).
    """

    async def aplicar_a_compra(self, compra_id: int, *, commit: bool = ...) -> object | None: ...


class ComprasService:
    def __init__(
        self,
        repo: SqlComprasRepository,
        *,
        retenciones: RetencionesAplicador | None = None,
        proveedores=None,   # SqlProveedoresRepository (puente compra→CxP); None = puente apagado
        inventario=None,    # InventarioService (corrección: ajustes de stock); None = corrección off
        caja=None,          # CajaService (corrección: diferencia de plata); None = sin ajuste de caja
    ) -> None:
        self._repo = repo
        # Solo el router los cablea (misma sesión): la corrección de una compra necesita mover
        # inventario (AJUSTE) y, si se pide, la caja.
        self._inventario = inventario
        self._caja = caja
        # Motor de retenciones inline (opt-in, ADR 0027): solo se inyecta con la feature `retenciones`.
        self._retenciones = retenciones
        # Puente a cuentas por pagar (reforma dashboard F2): una compra `a_credito` da de alta su
        # deuda en `facturas_proveedores` en la misma transacción. Solo el router/las rutas que lo
        # cablean lo tienen (el bot y los tests viejos siguen igual).
        self._proveedores = proveedores

    async def registrar(self, datos: CompraCrear, *, usuario_id: int | None) -> ResultadoCompra:
        """Registra la compra: resuelve proveedor, calcula total y persiste (stock + costo + eventos).

        Idempotente (ai-tools.md §4): si `idempotency_key` ya existe con el MISMO payload, devuelve la
        compra original sin re-registrar (replay) y SIN resolver proveedor (no crea un proveedor en el
        camino de replay); con payload distinto → `IdempotenciaConflicto`. El índice UNIQUE parcial
        (0025) es el respaldo estructural ante una carrera.
        """
        # Captura en paquetes (la caja con que se le compra al proveedor) → sub-unidad del stock:
        # sin esto, "10 cajas de puntilla" sumaría 10 gramos y el costo quedaría en $/caja.
        unidades = await self._repo.unidades_medida([it.producto_id for it in datos.items])
        items = []
        for it in datos.items:
            unidad_medida, contenido = unidades.get(it.producto_id, (None, None))
            cantidad, costo = convertir_a_subunidad(
                it.cantidad, it.costo, unidad=it.unidad, unidad_medida=unidad_medida,
                contenido_paquete=contenido,
            )
            items.append(ItemCompra(producto_id=it.producto_id, cantidad=cantidad, costo=costo))
        total = cuantizar(sum((it.cantidad * it.costo for it in items), Decimal("0")))

        if datos.idempotency_key:
            existente = await self._repo.buscar_por_idempotency(datos.idempotency_key)
            if existente is not None:
                if not _mismo_payload(
                    existente, items, total, datos.proveedor.id,
                    obra_id=datos.obra_id, es_viaje_material=datos.es_viaje_material,
                ):
                    raise IdempotenciaConflicto(datos.idempotency_key)
                return ResultadoCompra(compra=existente.compra, replay=True)

        fecha = _fecha_compra(datos.fecha)
        proveedor_id = await self._repo.get_or_create_proveedor(
            proveedor_id=datos.proveedor.id, nombre=datos.proveedor.nombre, nit=datos.proveedor.nit,
        )
        # Resbalo del viaje de material (spec 11): solo cuando aplica. `total` ES el costo del viaje.
        resbalo: Resbalo | None = None
        if datos.es_viaje_material and datos.precio_venta_cliente is not None:
            resbalo = calcular_resbalo(datos.precio_venta_cliente, total)
        # Alerta de precio de proveedor (spec 10): se calcula ANTES de insertar la compra nueva (que no
        # entra a su propia ventana histórica).
        alerta_precio = await self._alerta_precio_proveedor(
            proveedor_id, items=items, total=total, categoria=datos.categoria, hasta=fecha,
        )
        compra = await self._repo.crear_compra(
            proveedor_id=proveedor_id, fecha=fecha,
            items=items, total=total, usuario_id=usuario_id,
            idempotency_key=datos.idempotency_key,
            obra_id=datos.obra_id, categoria=datos.categoria,
            es_viaje_material=datos.es_viaje_material,
            precio_venta_cliente=datos.precio_venta_cliente,
            resbalo=resbalo.monto if resbalo is not None else None,
            factura_url=datos.factura_url,
        )
        if self._retenciones is not None:
            # Retenciones inline (ADR 0027): calcula/persiste los renglones en la MISMA transacción
            # (commit=False), atómico con la compra. Sin config activa no crea renglones (opt-in).
            await self._retenciones.aplicar_a_compra(compra.id, commit=False)
        if datos.a_credito:
            # Puente compra→CxP (F2): la deuda nace con la compra, misma tx. Solo camino NO-replay
            # (el replay de arriba devuelve la compra original sin duplicar la factura).
            if self._proveedores is None:
                raise RuntimeError("una compra a crédito requiere el repo de proveedores cableado")
            factura_id = datos.numero_factura or f"COMPRA-{compra.id}"
            if await self._proveedores.existe(factura_id):
                from modules.proveedores.errors import FacturaProveedorDuplicada

                raise FacturaProveedorDuplicada(factura_id)
            await self._proveedores.crear_factura(
                factura_id=factura_id,
                proveedor=compra.proveedor_nombre or "Proveedor",
                proveedor_id=compra.proveedor_id,
                descripcion=f"Compra #{compra.id}",
                total=total, fecha=today_co(), fecha_vencimiento=datos.fecha_vencimiento,
                usuario_id=usuario_id,
            )
            # Deja el vínculo compra→CxP (0067): corregir la compra después necesita saber qué deuda
            # mover, y adivinarla por el id 'COMPRA-{id}' fallaba con un número de factura propio.
            await self._repo.set_factura_proveedor(compra.id, factura_id)
        # Derivados de salida (no persistidos): % y alertas para que el cliente/bot avise al dueño.
        compra = compra.model_copy(update={
            "resbalo_pct": resbalo.porcentaje if resbalo is not None else None,
            "resbalo_alerta": resbalo.alerta if resbalo is not None else False,
            "alerta_precio_proveedor": alerta_precio,
        })
        return ResultadoCompra(compra=compra, replay=False)

    async def _alerta_precio_proveedor(
        self,
        proveedor_id: int,
        *,
        items: list[ItemCompra],
        total: Decimal,
        categoria: str | None,
        hasta: datetime,
    ) -> bool:
        """True si el costo unitario de esta compra supera en >15% el promedio de 6 meses del proveedor.

        El costo unitario de la compra se toma ponderado (total / Σ cantidad). Sin cantidad o sin
        historial no hay señal (default seguro: no alarmar sin base de comparación)."""
        cantidad_total = sum((it.cantidad for it in items), Decimal("0"))
        if cantidad_total <= 0:
            return False
        costo_unitario = total / cantidad_total
        promedio = await self._repo.promedio_costo_unitario_proveedor(
            proveedor_id, desde=hasta - _VENTANA_PRECIO_PROVEEDOR, hasta=hasta, categoria=categoria,
        )
        if promedio is None or promedio <= 0:
            return False
        return costo_unitario > promedio * _UMBRAL_PRECIO_PROVEEDOR

    # --- Corrección de una compra ya registrada -------------------------------

    async def corregir(
        self,
        compra_id: int,
        datos: CompraCorregir,
        *,
        usuario_id: int | None,
        modo_empresa: bool = False,
    ) -> CorreccionLeer:
        """Corrige cantidades/costos de una compra recibida aplicando SOLO las diferencias.

        Cada cambio de cantidad deja su movimiento AJUSTE (regla #7: nada mueve stock sin movimiento)
        con key natural `compra-correccion:{id}:{n}:{producto}` — el contador `n` evita que la
        segunda corrección haga replay de la primera. El costo del producto se re-pondera revirtiendo
        la línea vieja y re-aplicando la nueva (ADR 0025; el COGS ya grabado en ventas NO se toca).
        La plata se concilia: la cuenta por pagar se recalcula sola y, con `ajustar_pago`, la
        diferencia sale o entra de la caja.
        """
        if self._inventario is None:
            raise RuntimeError("corregir una compra requiere el servicio de inventario cableado")

        compra = await self._repo.obtener_para_corregir(compra_id)
        if compra is None:
            raise CompraInexistente(compra_id)
        if not compra.mueve_stock:
            raise CompraNoCorregible(
                compra_id, "está imputada a una obra o es un viaje de material (nunca movió stock)"
            )
        if await self._repo.tiene_retenciones(compra_id):
            raise CompraNoCorregible(
                compra_id, "ya tiene retenciones practicadas: corrígela por nota de ajuste fiscal"
            )

        unidades = await self._repo.unidades_medida([ln.producto_id for ln in datos.lineas])
        nuevas = {}
        for ln in datos.lineas:
            unidad_medida, contenido = unidades.get(ln.producto_id, (None, None))
            cantidad, costo = convertir_a_subunidad(
                ln.cantidad, ln.costo, unidad=ln.unidad, unidad_medida=unidad_medida,
                contenido_paquete=contenido,
            )
            nuevas[ln.producto_id] = (cantidad, costo)
        viejas = {pid: (cant, costo) for pid, cant, costo in compra.lineas}

        if datos.idempotency_key and datos.idempotency_key == compra.ultima_correccion_key:
            # Doble clic / retry: misma sustancia → replay sin efectos; otra sustancia → conflicto.
            if nuevas == viejas:
                return CorreccionLeer(
                    compra=await self._repo.leer(compra_id), delta_total=Decimal("0"), replay=True
                )
            raise IdempotenciaConflicto(datos.idempotency_key)

        n = compra.correcciones + 1
        ajustes: list[AjusteLinea] = []
        for producto_id in sorted(nuevas.keys() | viejas.keys()):
            cant_nueva, costo_nuevo = nuevas.get(producto_id, (Decimal("0"), Decimal("0")))
            cant_vieja, costo_viejo = viejas.get(producto_id, (Decimal("0"), Decimal("0")))
            delta = cant_nueva - cant_vieja
            movimiento_id = None
            if delta != 0:
                res = await self._inventario.ajustar(
                    producto_id=producto_id, delta=delta,
                    motivo=f"Corrección compra #{compra_id}: {datos.motivo}",
                    usuario_id=usuario_id,
                    idempotency_key=f"compra-correccion:{compra_id}:{n}:{producto_id}",
                )
                movimiento_id = res.movimiento_id
            if (delta, costo_nuevo) != (Decimal("0"), costo_viejo) and cant_nueva > 0:
                await self._recalcular_costos(
                    producto_id, compra_id,
                    vieja=(cant_vieja, costo_viejo), nueva=(cant_nueva, costo_nuevo),
                )
            ajustes.append(AjusteLinea(
                producto_id=producto_id, delta_cantidad=delta,
                stock_resultante=await self._repo.stock_actual(producto_id),
                movimiento_id=movimiento_id,
            ))

        total_nuevo = cuantizar(sum((c * v for c, v in nuevas.values()), Decimal("0")))
        delta_total = cuantizar(total_nuevo - compra.total)

        factura_id = await self._conciliar_cxp(compra_id, total_nuevo)
        movimiento_caja_id = await self._conciliar_caja(
            compra_id, delta_total, datos=datos, n=n, usuario_id=usuario_id,
            modo_empresa=modo_empresa,
        )

        await self._repo.reemplazar_detalle(
            compra_id, [(pid, c, v) for pid, (c, v) in nuevas.items()]
        )
        await self._repo.sellar_correccion(
            compra_id, total=total_nuevo, motivo=datos.motivo, cuando=now_co(),
            key=datos.idempotency_key,
        )
        return CorreccionLeer(
            compra=await self._repo.leer(compra_id), delta_total=delta_total, lineas=ajustes,
            factura_proveedor_id=factura_id, movimiento_caja_id=movimiento_caja_id, replay=False,
        )

    async def _recalcular_costos(
        self,
        producto_id: int,
        compra_id: int,
        *,
        vieja: tuple[Decimal, Decimal],
        nueva: tuple[Decimal, Decimal],
    ) -> None:
        """Re-pondera el costo del producto tras corregir su línea (ADR 0025).

        `precio_compra` (el ÚLTIMO costo de compra) solo se toca si esta es la compra más reciente
        del producto: si ya hubo otra después, la de después manda."""
        stock = await self._repo.stock_actual(producto_id)
        promedio = await self._repo.costo_promedio_actual(producto_id)
        nuevo_promedio = recalcular_promedio(stock, promedio, vieja, nueva)
        manda_precio = await self._repo.es_ultima_compra_del_producto(producto_id, compra_id)
        await self._repo.set_costos_producto(
            producto_id, precio_compra=nueva[1] if manda_precio else None,
            costo_promedio=nuevo_promedio,
        )

    async def _conciliar_cxp(self, compra_id: int, total_nuevo: Decimal) -> str | None:
        """La deuda con el proveedor sigue al nuevo total. Si ya se abonó MÁS que ese total, la
        corrección se rechaza: no se inventa un saldo a favor."""
        if self._proveedores is None:
            return None
        factura_id = await self._repo.factura_de_compra(compra_id)
        if factura_id is None:
            return None
        factura = await self._proveedores.obtener(factura_id)
        if factura is None:
            return None
        if factura.pagado > total_nuevo:
            raise CorreccionInvalida(
                f"La factura {factura_id} ya tiene abonos por {factura.pagado}: corrige o anula los "
                f"abonos antes de bajar la compra a {total_nuevo}"
            )
        await self._proveedores.actualizar_total(factura_id, total=total_nuevo)
        return factura_id

    async def _conciliar_caja(
        self,
        compra_id: int,
        delta_total: Decimal,
        *,
        datos: CompraCorregir,
        n: int,
        usuario_id: int | None,
        modo_empresa: bool,
    ) -> int | None:
        """La diferencia de plata, cuando el dueño pide ajustarla: pagó de menos → egreso; pagó de
        más → ingreso (el proveedor devuelve). Siempre con su movimiento de caja trazable."""
        if not datos.ajustar_pago or delta_total == 0 or self._caja is None:
            return None
        tipo = "egreso" if delta_total > 0 else "ingreso"
        res = await self._caja.registrar_movimiento(
            usuario_id=usuario_id, tipo=tipo, monto=abs(delta_total),
            concepto=f"Corrección compra #{compra_id}: {datos.motivo}",
            referencia=f"compra:{compra_id}",
            idempotency_key=f"compra-correccion:{compra_id}:{n}",
            modo_empresa=modo_empresa,
        )
        return res.movimiento.id

    async def listar(self, *, desde: date | None, hasta: date | None) -> list[CompraLeer]:
        """Compras del rango (default mes en curso, hora Colombia)."""
        inicio, fin = _rango_o_mes(desde, hasta)
        return await self._repo.listar(inicio=inicio, fin=fin)

    async def reporte_resbalos(self, *, desde: date | None, hasta: date | None) -> list[CompraLeer]:
        """Reporte de resbalos (spec 11): viajes de material del rango con margen $ y % + alerta."""
        inicio, fin = _rango_o_mes(desde, hasta)
        compras = await self._repo.listar_resbalos(inicio=inicio, fin=fin)
        return [_enriquecer_resbalo(c) for c in compras]

    async def analisis_precios(
        self,
        *,
        desde: date | None,
        hasta: date | None,
        proveedor_id: int | None = None,
        categoria: str | None = None,
    ) -> list[AnalisisPrecioProveedor]:
        """Análisis de precios de proveedor (Fase 8, spec 10): costo unitario ponderado por (proveedor,
        categoría) en el período, con su rango y la alerta de sobreprecio. Default: los últimos 6 meses
        (hora Colombia), la misma ventana de la alerta de precio."""
        hoy = today_co()
        inicio, fin = rango_dia_co(desde or (hoy - _VENTANA_ANALISIS), hasta or hoy)
        filas = await self._repo.analisis_precios_proveedor(
            desde=inicio, hasta=fin, proveedor_id=proveedor_id, categoria=categoria,
        )
        return [_analisis_a_schema(f) for f in filas]
