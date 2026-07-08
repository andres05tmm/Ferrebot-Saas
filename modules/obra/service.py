"""Servicio de obras: validación de dominio sobre el repositorio (sin SQL).

El corazón del módulo es el CICLO DE VIDA de la obra: las transiciones de estado son EXPLÍCITAS y se
validan contra `_TRANSICIONES` (nada de estados imposibles). Una transición no contemplada →
`TransicionEstadoInvalida` (409). Operar sobre una obra inexistente o dada de baja → `ObraInexistente`
(404). Los reportes diarios exigen que la obra exista.

Ciclo de vida (v1): PLANIFICADA arranca la obra; entra en ejecución o se suspende antes de empezar; una
obra en ejecución se suspende o finaliza; una suspendida se reanuda o finaliza; una FINALIZADA se liquida
(cierre). LIQUIDADA es terminal (no admite más transiciones). El servicio depende del puerto `ObrasRepo`
(lo implementa `SqlObrasRepository`; los tests lo falsean).
"""
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from types import SimpleNamespace
from typing import Protocol

from core.config.timezone import now_co, today_co
from core.money import cuantizar
from modules.facturacion.repository import FacturaLeer
from modules.obra.errors import (
    ConsumoEnObraLiquidada,
    ObraInexistente,
    ObraNoFinalizada,
    ObraSinCliente,
    ObraSinCotizacion,
    TransicionEstadoInvalida,
)
from modules.obra.models import (
    ConsumoInventario,
    CotizacionObra,
    ItemCotizacionObra,
    LiquidacionObra,
    Obra,
    ReporteDiarioObra,
)
from modules.obra.repository import AgregadosGastoObra, ConteosOperacion
from modules.obra.schemas import (
    ConsumoInventarioCrear,
    ObraActualizar,
    ObraCrear,
    ReporteDiarioCrear,
)
from modules.ventas.schemas import VentaLeer
from modules.ventas.service import LineaResuelta, VentaHeader, calcular_totales
from services.calculations.aiu import calcular_totales_cotizacion
from services.calculations.obra import DesgloseGasto, calcular_gasto_real_obra

# Cantidad de la venta = NUMERIC(12,3); precio/dinero = NUMERIC(12,2). La cotización de obra vive en
# MONEY4 (18,4): al armar la venta se ENCUADRA a la precisión del POS (redondeo money-safe).
_CANTIDAD_VENTA = Decimal("0.001")

# Medio de pago de la venta que respalda la factura de obra. Neutral (transferencia): una obra se
# factura para cobro por transferencia. [DEFINIR contador]: forma/medio de pago real del contrato
# (contado vs. crédito cambia `payment_method_id`); no se inventa una regla tributaria aquí.
_METODO_PAGO_OBRA = "transferencia"

# Umbral de la alerta de margen (plan §4): avisa cuando el margen restante baja del 50% de la utilidad
# presupuestada — la alarma temprana antes de comerse toda la utilidad.
_UMBRAL_ALERTA_MARGEN = Decimal("0.5")

# Transiciones permitidas del ciclo de vida de una obra (destinos válidos por estado actual).
_TRANSICIONES: dict[str, frozenset[str]] = {
    "PLANIFICADA": frozenset({"EN_EJECUCION", "SUSPENDIDA"}),
    "EN_EJECUCION": frozenset({"SUSPENDIDA", "FINALIZADA"}),
    "SUSPENDIDA": frozenset({"EN_EJECUCION", "FINALIZADA"}),
    "FINALIZADA": frozenset({"LIQUIDADA"}),
    "LIQUIDADA": frozenset(),  # terminal
}


class ObrasRepo(Protocol):
    """Puerto de datos de obras (lo implementa SqlObrasRepository; los tests lo falsean)."""

    async def obtener(self, obra_id: int) -> Obra | None: ...
    async def listar(
        self, *, cliente_id: int | None = None, estado: str | None = None
    ) -> list[Obra]: ...
    async def crear(self, datos: ObraCrear) -> Obra: ...
    async def obtener_por_cotizacion(self, cotizacion_id: int) -> Obra | None: ...
    async def factura_de_obra(self, obra_id: int) -> FacturaLeer | None: ...
    async def crear_desde_cotizacion(self, cotizacion: CotizacionObra) -> Obra: ...
    async def actualizar(self, obra: Obra, cambios: dict) -> Obra: ...
    async def cambiar_estado(self, obra: Obra, nuevo_estado: str) -> Obra: ...
    async def soft_delete(self, obra: Obra) -> None: ...
    async def contar_operacion(self, obra_id: int) -> ConteosOperacion: ...
    async def crear_reporte(
        self, obra_id: int, datos: ReporteDiarioCrear
    ) -> ReporteDiarioObra: ...
    async def listar_reportes(
        self, obra_id: int, *, limite: int = 100, offset: int = 0
    ) -> list[ReporteDiarioObra]: ...
    async def agregados_gasto(self, obra_id: int) -> AgregadosGastoObra: ...
    async def cotizacion_de_obra(self, obra: Obra): ...
    async def costo_producto(
        self, producto_id: int
    ) -> tuple[Decimal | None, Decimal | None] | None: ...
    async def consumo_por_key(self, idempotency_key: str) -> ConsumoInventario | None: ...
    async def crear_consumo(
        self,
        *,
        obra_id: int,
        producto_id: int,
        fecha,
        cantidad: Decimal,
        costo_unitario: Decimal,
        responsable: str | None,
        observaciones: str | None,
        idempotency_key: str | None = None,
    ) -> ConsumoInventario: ...
    async def obtener_liquidacion(self, obra_id: int) -> LiquidacionObra | None: ...
    async def crear_liquidacion(
        self, obra_id: int, valores: dict, snapshot_json: dict
    ) -> LiquidacionObra: ...


class ResultadoAjuste(Protocol):
    """Lo que devuelve `MovedorInventario.ajustar` (lo satisface `inventario.service.AjusteResultado`)."""

    movimiento_id: int | None
    stock_actual: Decimal


class MovedorInventario(Protocol):
    """Puerto de salida hacia inventario: lo satisface `modules.inventario.service.InventarioService`.

    El consumo de una obra DEBE mover stock por aquí (invariante "nada mueve inventario sin movimiento"):
    `ajustar` con delta negativo asienta el movimiento + baja el stock en la MISMA transacción del tenant,
    con guarda de stock negativo e idempotencia por `idempotency_key` incluidas."""

    async def ajustar(
        self,
        *,
        producto_id: int,
        delta: Decimal,
        motivo: str,
        usuario_id: int | None,
        idempotency_key: str | None = None,
    ) -> ResultadoAjuste: ...


class CreadorVentaObra(Protocol):
    """Puerto de salida hacia ventas: lo cumple `SqlVentasRepository`. Persiste la venta interna que
    respalda la factura de obra (reuso del pipeline de venta→FE, ADR 0014). `crear_venta` NO mueve stock
    aquí: las líneas de obra van sin `producto_id` y con `descontar_stock=False`."""

    async def siguiente_consecutivo(self) -> int: ...
    async def crear_venta(self, header: VentaHeader) -> VentaLeer: ...


class FacturadorFE(Protocol):
    """Puerto de facturación FE: lo cumple `FacturacionService`. Reusa `crear_pendiente_fe` TAL CUAL
    (idempotente por `fe:{venta_id}`, reserva consecutivo, arma el CUFE al emitir) — no se reimplementa
    la máquina de estados ni el número fiscal."""

    async def crear_pendiente_fe(self, venta_id: int) -> tuple[FacturaLeer, bool]: ...


class EstampadorObraFactura(Protocol):
    """Puerto de estampado del rastro obra→documento: lo cumple `SqlFacturacionRepository`."""

    async def estampar_obra_id(self, factura_id: int, obra_id: int) -> None: ...


@dataclass(frozen=True, slots=True)
class ResultadoFacturaObra:
    """Desenlace de facturar una obra: el documento FE (pendiente o el ya existente) + si nació ahora.

    `creada=True` → documento NUEVO (el caller encola la emisión); `creada=False` → la obra ya estaba
    facturada y se devuelve el documento existente (idempotencia dura, no emite un segundo CUFE)."""

    factura: FacturaLeer
    creada: bool


@dataclass(frozen=True, slots=True)
class GastoRealResultado:
    """Gasto real de una obra + presupuesto + semáforo + alerta (lo que consume la capa HTTP/dashboard/bot).

    `desglose` es el `DesgloseGasto` de la función pura (5 componentes + total + semáforo, ya cuantizados).
    `utilidad_real = ingreso_presupuestado − desglose.total` (cuantizada). `tiene_presupuesto=False` para la
    obra sin cotización (no hay contra qué medir). `alerta_margen`: el margen restante bajó del 50% de la U
    presupuestada."""

    obra_id: int
    ingreso_presupuestado: Decimal
    utilidad_presupuestada: Decimal
    tiene_presupuesto: bool
    desglose: DesgloseGasto
    utilidad_real: Decimal
    alerta_margen: bool


class ObrasService:
    def __init__(
        self,
        repo: ObrasRepo,
        inventario: MovedorInventario | None = None,
        *,
        ventas: CreadorVentaObra | None = None,
        facturacion: FacturadorFE | None = None,
        estampador: EstampadorObraFactura | None = None,
    ) -> None:
        self._repo = repo
        # Opcional: sólo el flujo de CONSUMO lo necesita. Los callers que no consumen (p. ej. la conversión
        # GANADA→Obra de la Fase 2) construyen el service sin inventario; `registrar_consumo` exige tenerlo.
        self._inventario = inventario
        # Opcionales: sólo `facturar` (Fase 7 DIAN) los necesita. El cableado FE los inyecta con la
        # `ConfigFiscal` del tenant ya cargada; el resto de endpoints construye el service sin ellos.
        self._ventas = ventas
        self._facturacion = facturacion
        self._estampador = estampador

    async def crear(self, datos: ObraCrear) -> Obra:
        """Da de alta una obra suelta (arranca PLANIFICADA por el default de la base)."""
        return await self._repo.crear(datos)

    async def crear_desde_cotizacion(self, cotizacion: CotizacionObra) -> Obra:
        """Crea la Obra 1-1 que nace de una cotización GANADA (método ADITIVO, lo llama la Fase 2).

        IDEMPOTENTE: `obras.cotizacion_id` es UNIQUE, así que una cotización ya convertida NO genera una
        segunda obra. Se resuelve mirando primero si ya existe una obra para esa cotización y, de ser
        así, devolviéndola; la UNIQUE de la base es el respaldo último ante una carrera. La `Obra`
        arranca PLANIFICADA (default de la base) y hereda cliente/nombre/ubicación de la cotización.
        """
        existente = await self._repo.obtener_por_cotizacion(cotizacion.id)
        if existente is not None:
            return existente
        return await self._repo.crear_desde_cotizacion(cotizacion)

    async def obtener(self, obra_id: int) -> Obra:
        obra = await self._repo.obtener(obra_id)
        if obra is None:
            raise ObraInexistente(obra_id)
        return obra

    async def resumen(self, obra_id: int) -> tuple[Obra, ConteosOperacion]:
        """Obra + conteos de operación (para el detalle). 404 si no existe."""
        obra = await self.obtener(obra_id)
        conteos = await self._repo.contar_operacion(obra_id)
        return obra, conteos

    async def listar(
        self, *, cliente_id: int | None = None, estado: str | None = None
    ) -> list[Obra]:
        return await self._repo.listar(cliente_id=cliente_id, estado=estado)

    async def actualizar(self, obra_id: int, datos: ObraActualizar) -> Obra:
        """Parche parcial de metadatos. 404 si no existe. No toca `estado`."""
        obra = await self.obtener(obra_id)
        cambios = datos.model_dump(exclude_unset=True)
        return await self._repo.actualizar(obra, cambios)

    async def cambiar_estado(self, obra_id: int, nuevo_estado: str) -> Obra:
        """Aplica una transición de estado VÁLIDA. 404 si no existe; 409 si la transición no se permite."""
        obra = await self.obtener(obra_id)
        if nuevo_estado not in _TRANSICIONES.get(obra.estado, frozenset()):
            raise TransicionEstadoInvalida(obra.estado, nuevo_estado)
        return await self._repo.cambiar_estado(obra, nuevo_estado)

    async def eliminar(self, obra_id: int) -> None:
        """Baja lógica (soft delete). 404 si no existe o ya estaba dada de baja."""
        obra = await self.obtener(obra_id)
        await self._repo.soft_delete(obra)

    async def crear_reporte(
        self, obra_id: int, datos: ReporteDiarioCrear
    ) -> ReporteDiarioObra:
        """Registra un reporte diario de avance. 404 si la obra no existe.

        La `fecha` por defecto es hoy en hora Colombia (regla #4); se resuelve aquí antes de persistir.
        """
        await self.obtener(obra_id)  # valida existencia (404 si no)
        datos = datos.model_copy(update={"fecha": datos.fecha or today_co()})
        return await self._repo.crear_reporte(obra_id, datos)

    async def listar_reportes(
        self, obra_id: int, *, limite: int = 100, offset: int = 0
    ) -> list[ReporteDiarioObra]:
        """Reportes diarios de una obra (más recientes primero). 404 si la obra no existe."""
        await self.obtener(obra_id)  # valida existencia (404 si no)
        return await self._repo.listar_reportes(obra_id, limite=limite, offset=offset)

    # ---- Gasto real + semáforo + alerta (Fase 3, el diferenciador) ---------------------------------
    async def _presupuesto(self, obra: Obra) -> tuple[Decimal, Decimal, bool]:
        """(ingreso_presupuestado, utilidad_presupuestada, tiene_presupuesto) desde la cotización GANADA.

        `ingreso_presupuestado = subtotal + A + I + U` (SIN el IVA, que no es ingreso sino impuesto que se
        traslada a la DIAN) y `utilidad_presupuestada = U`, ambos por la función pura AIU (una sola verdad,
        nunca recalculada a mano). Obra suelta (sin cotización) → (0, 0, False)."""
        datos = await self._repo.cotizacion_de_obra(obra)
        if datos is None:
            return Decimal("0"), Decimal("0"), False
        cotizacion, items = datos
        totales = calcular_totales_cotizacion(
            items,
            administracion_pct=cotizacion.administracion_pct,
            imprevistos_pct=cotizacion.imprevistos_pct,
            utilidad_pct=cotizacion.utilidad_pct,
            iva_sobre_utilidad_pct=cotizacion.iva_sobre_utilidad_pct,
        )
        ingreso = (
            totales.subtotal + totales.administracion + totales.imprevistos + totales.utilidad
        )
        return ingreso, totales.utilidad, True

    async def _calcular_gasto_real(self, obra: Obra) -> GastoRealResultado:
        """Corazón del vertical: agrega los 5 componentes y llama a `calcular_gasto_real_obra`.

        La agregación por componente la hace el repo en SQL (money-safe, sin cargar miles de filas). Cada
        suma agregada se pasa a la función pura como un ÚNICO objeto adaptador (la función re-suma trivial y
        aporta el TOTAL, el semáforo y la cuantización — su verdadero valor). Las horas ya vienen costeadas
        por máquina, así que se pasan como dinero con `costo_op_hora=1`; los consumos igual (dinero en
        `cantidad`, `costo_unitario=1`)."""
        ingreso, utilidad_pres, tiene_presupuesto = await self._presupuesto(obra)
        agg = await self._repo.agregados_gasto(obra.id)
        desglose = calcular_gasto_real_obra(
            gastos=[SimpleNamespace(monto=agg.total_gastos)],
            compras=[SimpleNamespace(costo_total=agg.total_compras)],
            prorrateos=[SimpleNamespace(costo_imputado=agg.total_prorrateo_nomina)],
            horas_maquina=[SimpleNamespace(horas=agg.total_horas_maquina)],
            costo_op_hora=Decimal("1"),
            consumos=[
                SimpleNamespace(cantidad=agg.total_consumos_inventario, costo_unitario=Decimal("1"))
            ],
            ingreso_presupuestado=ingreso,
            utilidad_presupuestada=utilidad_pres,
        )
        utilidad_real = cuantizar(ingreso - desglose.total)
        # Alerta temprana: el margen restante (utilidad_real) baja del 50% de la U presupuestada. Sólo
        # tiene sentido con presupuesto (>0); sin él no hay umbral y no se alerta (el semáforo ya cae a rojo).
        alerta_margen = utilidad_pres > 0 and utilidad_real < utilidad_pres * _UMBRAL_ALERTA_MARGEN
        return GastoRealResultado(
            obra_id=obra.id,
            ingreso_presupuestado=cuantizar(ingreso),
            utilidad_presupuestada=cuantizar(utilidad_pres),
            tiene_presupuesto=tiene_presupuesto,
            desglose=desglose,
            utilidad_real=utilidad_real,
            alerta_margen=alerta_margen,
        )

    async def gasto_real(self, obra_id: int) -> GastoRealResultado:
        """Gasto real de la obra en tiempo real (presupuesto vs. real + semáforo + alerta). 404 si no existe."""
        obra = await self.obtener(obra_id)
        return await self._calcular_gasto_real(obra)

    # ---- Consumo de inventario (INVARIANTE: nada mueve inventario sin movimiento) -------------------
    async def registrar_consumo(
        self, obra_id: int, datos: ConsumoInventarioCrear, *, usuario_id: int | None = None
    ) -> tuple[ConsumoInventario, ResultadoAjuste]:
        """Imputa material a la obra y baja el stock EN LA MISMA TRANSACCIÓN (invariante crítico).

        Persiste el `ConsumoInventario` y, acto seguido, dispara la salida de stock por `modules.inventario`
        (`ajustar` con delta negativo): asienta el movimiento de inventario y baja el stock. Si la salida
        deja stock negativo, `ajustar` levanta su error y toda la transacción del tenant se revierte (el
        consumo no queda huérfano). El movimiento lleva `idempotency_key` anclada al id del consumo, para
        que un reintento del ajuste no lo duplique. 404 si la obra no existe; 409 si está LIQUIDADA;
        `ProductoInexistente` si el producto no existe (lo traduce el router).

        M2 (cierre): si `datos.idempotency_key` YA generó un consumo (el bot reintentó), se hace REPLAY —se
        devuelve ese consumo y se re-emite el ajuste con su misma key (idempotente en inventario): ni un
        segundo consumo ni un segundo movimiento. Sin key (alta de dashboard) el flujo es el de siempre."""
        if self._inventario is None:   # error de wiring, no de dominio
            raise RuntimeError("ObrasService sin MovedorInventario: no puede registrar consumos")

        # Replay del bot: un consumo ya asentado con esta key no se duplica (reintento inocuo). No re-valida
        # el estado de la obra: la operación ya ocurrió; replicarla es idempotente (patrón de `liquidar`).
        if datos.idempotency_key is not None:
            existente = await self._repo.consumo_por_key(datos.idempotency_key)
            if existente is not None:
                return existente, await self._salida_stock(existente, usuario_id=usuario_id)

        obra = await self.obtener(obra_id)   # 404 si no existe
        if obra.estado == "LIQUIDADA":
            raise ConsumoEnObraLiquidada(obra_id)

        costo = await self._resolver_costo(datos)   # valida existencia del producto
        fecha = datos.fecha or today_co()
        consumo = await self._repo.crear_consumo(
            obra_id=obra_id,
            producto_id=datos.producto_id,
            fecha=fecha,
            cantidad=datos.cantidad,
            costo_unitario=costo,
            responsable=datos.responsable,
            observaciones=datos.observaciones,
            idempotency_key=datos.idempotency_key,
        )
        return consumo, await self._salida_stock(consumo, usuario_id=usuario_id)

    async def _salida_stock(
        self, consumo: ConsumoInventario, *, usuario_id: int | None
    ) -> ResultadoAjuste:
        """Emite la salida de stock del consumo por `modules.inventario` (delta negativo), idempotente por
        `consumo:{id}`. Reutilizada por el alta y por el REPLAY (M2): en el replay, `ajustar` encuentra el
        movimiento por su key y devuelve replay sin re-aplicar —ni un segundo movimiento."""
        return await self._inventario.ajustar(
            producto_id=consumo.producto_id,
            delta=-consumo.cantidad,   # salida: baja el stock
            motivo=f"Consumo obra {consumo.obra_id} (consumo {consumo.id})",
            usuario_id=usuario_id,
            idempotency_key=f"consumo:{consumo.id}",
        )

    async def _resolver_costo(self, datos: ConsumoInventarioCrear) -> Decimal:
        """Costo unitario a valorar el consumo: el explícito, si no el del producto (promedio→compra→0).

        Valida de paso que el producto EXISTA (la FK del consumo lo exige): `ProductoInexistente` si no."""
        from modules.inventario.errors import ProductoInexistente

        costos = await self._repo.costo_producto(datos.producto_id)
        if costos is None:
            raise ProductoInexistente(datos.producto_id)
        if datos.costo_unitario is not None:
            return datos.costo_unitario
        costo_promedio, precio_compra = costos
        if costo_promedio is not None:
            return costo_promedio
        if precio_compra is not None:
            return precio_compra
        return Decimal("0")

    # ---- Liquidación: snapshot inmutable + idempotente (Fase 3, cierre de obra) --------------------
    async def obtener_liquidacion(self, obra_id: int) -> LiquidacionObra:
        """Liquidación (snapshot) de la obra. 404 si la obra no existe o aún no se ha liquidado."""
        await self.obtener(obra_id)   # 404 si la obra no existe
        liquidacion = await self._repo.obtener_liquidacion(obra_id)
        if liquidacion is None:
            raise ObraInexistente(obra_id)   # sin liquidación → 404 (el router lo mapea)
        return liquidacion

    async def liquidar(self, obra_id: int) -> LiquidacionObra:
        """Cierra la obra: congela el gasto real definitivo y la transiciona a LIQUIDADA. IDEMPOTENTE.

        Si la obra YA tiene liquidación (UNIQUE obra_id), la devuelve TAL CUAL, sin recalcular ni crear otra
        (re-liquidar es inocuo). Si no, exige que la obra esté FINALIZADA (el único origen válido de la
        transición → LIQUIDADA), calcula el gasto real, escribe el snapshot inmutable (los 5 componentes +
        total + presupuesto + utilidad real + semáforo + `snapshot_json` con el detalle) y pasa la obra a
        LIQUIDADA en la misma transacción. 404 si la obra no existe; 409 si no está FINALIZADA."""
        obra = await self.obtener(obra_id)   # 404 si no existe
        existente = await self._repo.obtener_liquidacion(obra_id)
        if existente is not None:
            return existente   # idempotencia: no recalcula ni duplica
        if obra.estado != "FINALIZADA":
            raise ObraNoFinalizada(obra_id, obra.estado)

        resultado = await self._calcular_gasto_real(obra)
        d = resultado.desglose
        valores = {
            "ingreso_presupuestado": resultado.ingreso_presupuestado,
            "utilidad_presupuestada": resultado.utilidad_presupuestada,
            "gasto_total": d.total,
            "total_gastos": d.total_gastos,
            "total_compras": d.total_compras,
            "total_prorrateo_nomina": d.total_prorrateo_nomina,
            "total_horas_maquina": d.total_horas_maquina,
            "total_consumos_inventario": d.total_consumos_inventario,
            "utilidad_real": resultado.utilidad_real,
            "semaforo": d.semaforo.value,   # 'verde'/'amarillo'/'rojo' (el enum de la BD es minúsculo)
        }
        snapshot_json = {
            "version": 1,
            "tiene_presupuesto": resultado.tiene_presupuesto,
            "ingreso_presupuestado": str(resultado.ingreso_presupuestado),
            "utilidad_presupuestada": str(resultado.utilidad_presupuestada),
            "componentes": {
                "gastos": str(d.total_gastos),
                "compras": str(d.total_compras),
                "prorrateo_nomina": str(d.total_prorrateo_nomina),
                "horas_maquina": str(d.total_horas_maquina),
                "consumos_inventario": str(d.total_consumos_inventario),
            },
            "gasto_total": str(d.total),
            "utilidad_real": str(resultado.utilidad_real),
            "semaforo": d.semaforo.value,
            "alerta_margen": resultado.alerta_margen,
            "calculado_en": now_co().isoformat(),
        }
        liquidacion = await self._repo.crear_liquidacion(obra_id, valores, snapshot_json)
        # La obra estaba FINALIZADA (validado arriba): transiciona a LIQUIDADA en la misma transacción.
        # Aunque una carrera haya devuelto la liquidación existente, fijar LIQUIDADA es idempotente (mismo
        # estado terminal). El chequeo `existente` de arriba ya cortó la re-liquidación secuencial.
        await self._repo.cambiar_estado(obra, "LIQUIDADA")
        return liquidacion

    # ---- Facturar desde obra (Fase 7 DIAN): reusa FacturacionService, NO reimplementa el CUFE ---------
    async def facturar_obra(self, obra_id: int, *, vendedor_id: int) -> ResultadoFacturaObra:
        """Emite la factura electrónica de una obra a partir de su cotización GANADA. IDEMPOTENTE.

        Reusa el pipeline venta→FE (ADR 0014) SIN tocar la máquina de estados ni el número fiscal:
        (1) idempotencia dura — si la obra YA tiene documento (`factura_de_obra`), lo devuelve tal cual
            (`creada=False`), sin armar una segunda venta ni un segundo CUFE;
        (2) arma una venta INTERNA desde los ítems de la cotización con el AIU (IVA SOLO sobre la
            utilidad, `services.calculations.aiu`) — líneas sin `producto_id` y sin descontar stock;
        (3) `crear_pendiente_fe(venta_id)` crea el documento `pendiente` (reserva consecutivo, idempotente
            por `fe:{venta_id}`);
        (4) estampa `obra_id` en la fila (rastro obra→documento, migración 0050).

        El caller (router) COMMITEA y encola la emisión SOLO si `creada` (el worker arma el CUFE contra
        MATIAS). 404 si la obra no existe; `ObraSinCotizacion`/`ObraSinCliente` (→409) si no es facturable.

        [DEFINIR contador]: documento soporte (DS) para obras a NO obligados a facturar — cuándo aplica DS
        en vez de FE es una regla tributaria del contador; no se decide aquí (v1 emite siempre FE).
        """
        if self._ventas is None or self._facturacion is None or self._estampador is None:
            raise RuntimeError("ObrasService sin colaboradores de facturación: no puede facturar la obra")

        obra = await self.obtener(obra_id)   # 404 si no existe
        # (1) Idempotencia dura: la obra ya tiene documento → se devuelve ese (no un segundo CUFE).
        existente = await self._repo.factura_de_obra(obra_id)
        if existente is not None:
            return ResultadoFacturaObra(factura=existente, creada=False)

        if obra.cliente_id is None:
            raise ObraSinCliente(obra_id)
        datos = await self._repo.cotizacion_de_obra(obra)
        if datos is None:
            raise ObraSinCotizacion(obra_id)   # obra suelta / cotización borrada
        cotizacion, items = datos
        if cotizacion.estado != "GANADA" or not items:
            raise ObraSinCotizacion(obra_id)   # sin cotización GANADA con ítems no hay qué facturar

        # (2) Venta interna money-safe desde la cotización (IVA solo sobre la utilidad).
        lineas = _lineas_venta_desde_cotizacion(cotizacion, items)
        subtotal, impuestos, total = calcular_totales(lineas)
        consecutivo = await self._ventas.siguiente_consecutivo()
        header = VentaHeader(
            consecutivo=consecutivo,
            cliente_id=obra.cliente_id,
            vendedor_id=vendedor_id,
            subtotal=subtotal,
            impuestos=impuestos,
            total=total,
            metodo_pago=_METODO_PAGO_OBRA,
            origen="web",
            # Backstop de concurrencia: la UNIQUE(ventas.idempotency_key) impide dos ventas para la
            # misma obra si dos requests corren la vez (la perdedora choca antes de un segundo documento).
            idempotency_key=f"obra-fe:{obra_id}",
            lineas=lineas,
        )
        venta = await self._ventas.crear_venta(header)

        # (3) Documento FE `pendiente` reusando la máquina de estados; (4) estampa el rastro obra→documento.
        factura, creada = await self._facturacion.crear_pendiente_fe(venta.id)
        if creada:
            await self._estampador.estampar_obra_id(factura.id, obra_id)
        return ResultadoFacturaObra(factura=factura, creada=creada)


def _linea_obra(
    descripcion: str, cantidad: Decimal, precio_con_iva: Decimal, iva_pct: int
) -> LineaResuelta:
    """Una línea de la venta interna de obra (PURA). Sin `producto_id` ni descuento de stock: es una
    imputación fiscal de un renglón de cotización, no una salida de mercancía.

    `precio_con_iva` es el precio unitario CON IVA incluido (estándar retail Colombia, como toda línea
    de venta); `total_linea` se cuantiza a centavos con los MISMOS valores que se persisten, para que la
    cabecera de la venta y el payload DIAN no difieran ni un centavo (`descomponer_iva` base-primero)."""
    return LineaResuelta(
        producto_id=None,
        descripcion=descripcion,
        cantidad=cantidad,
        precio_unitario=precio_con_iva,
        iva=iva_pct,
        total_linea=cuantizar(precio_con_iva * cantidad),
        descontar_stock=False,
        costo_unitario=None,
    )


def _lineas_venta_desde_cotizacion(
    cotizacion: CotizacionObra, items: list[ItemCotizacionObra]
) -> list[LineaResuelta]:
    """Traduce una cotización AIU GANADA a las líneas de la venta que respalda la factura (PURA).

    Modelo fiscal AIU (spec 15 §1): el IVA (19%) grava SOLO la utilidad, nunca el subtotal ni la
    administración/imprevistos (`services.calculations.aiu`, única fuente de verdad de los totales). Se
    materializa como líneas de venta a IVA por línea:
      - cada ítem de obra → línea a IVA 0% (base = cantidad × valor_unitario, sin gravar);
      - Administración e Imprevistos → una línea cada uno a IVA 0% (montos AIU, sin gravar);
      - Utilidad → UNA línea a 19% cuyo precio CON IVA = utilidad + iva_utilidad, de modo que la
        descomposición base-primero devuelva base≈utilidad e IVA=iva_utilidad (el único IVA del documento).

    Los componentes de valor 0 se omiten (no ensuciar el documento con líneas vacías). La suma de bases
    por porcentaje de las líneas cuadra con la cabecera por construcción → el pre-check FAU04 pasa.
    """
    totales = calcular_totales_cotizacion(
        items,
        administracion_pct=cotizacion.administracion_pct,
        imprevistos_pct=cotizacion.imprevistos_pct,
        utilidad_pct=cotizacion.utilidad_pct,
        iva_sobre_utilidad_pct=cotizacion.iva_sobre_utilidad_pct,
    )
    lineas: list[LineaResuelta] = [
        _linea_obra(
            it.descripcion,
            it.cantidad.quantize(_CANTIDAD_VENTA, rounding=ROUND_HALF_UP),
            cuantizar(it.valor_unitario),
            0,
        )
        for it in items
    ]
    if totales.administracion > 0:
        lineas.append(_linea_obra("Administración (AIU)", Decimal("1"), totales.administracion, 0))
    if totales.imprevistos > 0:
        lineas.append(_linea_obra("Imprevistos (AIU)", Decimal("1"), totales.imprevistos, 0))
    if totales.utilidad > 0:
        # % IVA entero para la línea de venta (Colombia: 19/5/0). El precio CON IVA lleva el impuesto de
        # la utilidad ya sumado, así el único IVA del documento recae sobre la utilidad.
        iva_pct = int((cotizacion.iva_sobre_utilidad_pct * 100).to_integral_value(rounding=ROUND_HALF_UP))
        precio_utilidad_con_iva = cuantizar(totales.utilidad + totales.iva_utilidad)
        lineas.append(_linea_obra("Utilidad (AIU)", Decimal("1"), precio_utilidad_con_iva, iva_pct))
    return lineas
