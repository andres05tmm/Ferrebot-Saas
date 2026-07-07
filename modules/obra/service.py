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
from decimal import Decimal
from types import SimpleNamespace
from typing import Protocol

from core.config.timezone import now_co, today_co
from core.money import cuantizar
from modules.obra.errors import (
    ConsumoEnObraLiquidada,
    ObraInexistente,
    ObraNoFinalizada,
    TransicionEstadoInvalida,
)
from modules.obra.models import (
    ConsumoInventario,
    CotizacionObra,
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
from services.calculations.aiu import calcular_totales_cotizacion
from services.calculations.obra import DesgloseGasto, calcular_gasto_real_obra

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
    def __init__(self, repo: ObrasRepo, inventario: MovedorInventario | None = None) -> None:
        self._repo = repo
        # Opcional: sólo el flujo de CONSUMO lo necesita. Los callers que no consumen (p. ej. la conversión
        # GANADA→Obra de la Fase 2) construyen el service sin inventario; `registrar_consumo` exige tenerlo.
        self._inventario = inventario

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
