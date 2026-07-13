"""Repositorio de obras y reportes diarios: único lugar con SQL del módulo (regla no negociable #2).

Calca `modules.clientes.repository`. El soft delete (`eliminado_en`) oculta la obra: `obtener`/`listar`
filtran las borradas (para el API son 404 / no aparecen). El conteo de operación (`contar_operacion`)
son tres COUNT baratos apoyados en los índices `obra_id` de las tablas asociadas. La sesión del tenant ES
la transacción; aquí no se hace commit.

Fase 3 suma el corazón del vertical: el GASTO REAL de la obra (`agregados_gasto`) se calcula AGREGANDO en
SQL cada componente (no cargando miles de filas — regla de performance), la LIQUIDACIÓN (`crear_liquidacion`)
congela un snapshot inmutable, y el CONSUMO de inventario (`crear_consumo`) se persiste aquí mientras el
service dispara el movimiento por `modules.inventario` (invariante "nada mueve inventario sin movimiento").
Los modelos de otros módulos (gastos, compras, prorrateo, horas, productos) se IMPORTAN sólo para leer/
agregar por consulta — no se editan (disciplina de propiedad de archivos por fase).
"""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.config.timezone import now_co
from modules.caja.models import Gasto
from modules.clientes.models import Cliente
from modules.compras.models import Compra
from modules.facturacion.models import FacturaElectronica
from modules.facturacion.repository import FacturaLeer
from modules.inventario.models import Inventario, Producto
from modules.maquinaria.models import AsignacionMaquinaObra, Maquina, RegistroHorasMaquina
from modules.nomina.models import ProrrateoNominaObra
from modules.obra.models import (
    ConsumoInventario,
    CotizacionObra,
    ItemCotizacionObra,
    LiquidacionObra,
    Obra,
    ReporteDiarioObra,
)
from modules.obra.schemas import ObraCrear, ReporteDiarioCrear
from modules.trabajadores.models import AsignacionTrabajadorObra


@dataclass(frozen=True, slots=True)
class ConteosOperacion:
    maquinas_asignadas: int
    trabajadores_asignados: int
    reportes_diarios: int


@dataclass(frozen=True, slots=True)
class AgregadosGastoObra:
    """Los 5 componentes del gasto real ya SUMADOS en SQL (sin cuantizar: el redondeo va en la función pura).

    Mapean 1-1 a los parámetros de `services.calculations.obra.calcular_gasto_real_obra`:
    `total_horas_maquina` ya viene COSTEADO por máquina (Σ horas_facturables × costo_operacion_hora de cada
    máquina), porque la función pura costea todas las horas a una tarifa única y aquí cada máquina tiene la
    suya. `total_consumos_inventario` = Σ(cantidad × costo_unitario). Todo NUMERIC exacto (no float)."""

    total_gastos: Decimal
    total_compras: Decimal
    total_prorrateo_nomina: Decimal
    total_horas_maquina: Decimal
    total_consumos_inventario: Decimal


class SqlObrasRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def obtener(self, obra_id: int) -> Obra | None:
        """Obra vigente por id (las soft-deleted se tratan como inexistentes)."""
        return (
            await self._s.execute(
                select(Obra).where(Obra.id == obra_id, Obra.eliminado_en.is_(None))
            )
        ).scalar_one_or_none()

    async def listar(
        self, *, cliente_id: int | None = None, estado: str | None = None
    ) -> list[Obra]:
        """Obras vigentes (más recientes primero); filtra por cliente y por estado."""
        stmt = select(Obra).where(Obra.eliminado_en.is_(None))
        if cliente_id is not None:
            stmt = stmt.where(Obra.cliente_id == cliente_id)
        if estado is not None:
            stmt = stmt.where(Obra.estado == estado)
        stmt = stmt.order_by(Obra.creado_en.desc(), Obra.id.desc())
        return list((await self._s.execute(stmt)).scalars().all())

    async def crear(self, datos: ObraCrear) -> Obra:
        obra = Obra(**datos.model_dump())
        self._s.add(obra)
        await self._s.flush()  # asigna obra.id
        return obra

    async def nombres_clientes(self, ids: list[int]) -> dict[int, str]:
        """`cliente_id → nombre` de los clientes pedidos (batch, sin N+1). Alimenta `cliente_nombre` del
        portafolio y del listado de obras (repara la referencia muerta del dashboard). `ids` vacío no
        consulta. Se resuelve por el ORM de `clientes` (acceso a datos sólo por el repo, regla #2)."""
        if not ids:
            return {}
        filas = (
            await self._s.execute(
                select(Cliente.id, Cliente.nombre).where(Cliente.id.in_(ids))
            )
        ).all()
        return {int(cid): nombre for cid, nombre in filas}

    async def obtener_por_cotizacion(self, cotizacion_id: int) -> Obra | None:
        """Obra ligada a una cotización (1-1). No filtra `eliminado_en`: la UNIQUE de `cotizacion_id`
        cubre también las archivadas, así que devolver la existente hace idempotente la conversión."""
        return (
            await self._s.execute(
                select(Obra).where(Obra.cotizacion_id == cotizacion_id)
            )
        ).scalar_one_or_none()

    async def factura_de_obra(self, obra_id: int) -> FacturaLeer | None:
        """Factura electrónica ya ligada a la obra (rastro `obra_id`, Fase 7 DIAN), o None.

        Sostiene la IDEMPOTENCIA de "facturar desde /obras/{id}": si la obra ya tiene un documento
        (cualquier estado), facturar de nuevo devuelve ESE, sin emitir un segundo CUFE. Lectura
        cross-módulo a `facturas_electronicas` (SQL solo en el repo, regla #2), como `tiene_factura_viva`
        en ventas. Si un histórico dejó varias, gana la más reciente (`id` desc): la reemisión tras
        rechazo/anulación queda [DEFINIR] fuera de v1 (una obra factura una vez por este endpoint)."""
        orm = (
            await self._s.execute(
                select(FacturaElectronica)
                .where(FacturaElectronica.obra_id == obra_id)
                .order_by(FacturaElectronica.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        return FacturaLeer.model_validate(orm) if orm is not None else None

    async def crear_desde_cotizacion(self, cotizacion: CotizacionObra) -> Obra:
        """Inserta la Obra 1-1 que nace de una cotización GANADA, poblando `cotizacion_id` (la FK que
        `ObraCrear` no acepta). Arranca PLANIFICADA (default de la base).

        Idempotente ante CARRERA: el servicio pre-chequea `obtener_por_cotizacion`, pero dos
        conversiones concurrentes de la MISMA cotización pueden pasar ambas ese chequeo (None) y llegar
        aquí; la UNIQUE(cotizacion_id) es la frontera última. El flush va en un SAVEPOINT
        (`begin_nested`): si choca, se revierte SOLO el savepoint (sin envenenar la transacción del
        tenant), se re-lee la obra ya committeada por la ganadora y se devuelve esa (misma id), en vez
        de propagar un 500. Espeja el patrón de traducción de IntegrityError de
        `SqlCotizacionObraRepository.crear`."""
        obra = Obra(
            cotizacion_id=cotizacion.id,
            cliente_id=cotizacion.cliente_id,
            nombre=cotizacion.nombre_obra,
            ubicacion=cotizacion.ubicacion,
        )
        try:
            async with self._s.begin_nested():   # SAVEPOINT: aísla el flush de la carrera
                self._s.add(obra)                 # dentro del savepoint → el rollback lo expulsa
                await self._s.flush()  # asigna obra.id (y dispara la UNIQUE de cotizacion_id)
        except IntegrityError:
            existente = await self.obtener_por_cotizacion(cotizacion.id)
            if existente is None:   # la colisión no fue por cotizacion_id: no la tragues
                raise
            return existente
        return obra

    async def actualizar(self, obra: Obra, cambios: dict) -> Obra:
        """Aplica un parche parcial sobre una obra ya cargada (solo las claves presentes)."""
        for campo, valor in cambios.items():
            setattr(obra, campo, valor)
        await self._s.flush()
        await self._s.refresh(obra)   # ver nota async abajo
        return obra

    async def cambiar_estado(self, obra: Obra, nuevo_estado: str) -> Obra:
        """Persiste el nuevo estado (la validación de la transición la hace el servicio).

        Nota async: `actualizado_en` tiene `onupdate=func.now()`; tras el UPDATE queda EXPIRADO (su valor lo
        computa el servidor). Si el router serializa la obra (`ObraLeer.model_validate`) sin repoblarlo, el
        acceso perezoso a ese atributo dispararía IO fuera del contexto greenlet → `MissingGreenlet` (500).
        Se `refresh` aquí, dentro del await, para devolver la fila completa y que la serialización sea pura."""
        obra.estado = nuevo_estado
        await self._s.flush()
        await self._s.refresh(obra)
        return obra

    async def soft_delete(self, obra: Obra) -> None:
        """Marca la baja lógica (`eliminado_en = ahora` en hora Colombia); no borra la fila."""
        obra.eliminado_en = now_co()
        await self._s.flush()

    async def contar_operacion(self, obra_id: int) -> ConteosOperacion:
        """Tres COUNT baratos (máquinas/trabajadores/reportes) por sus índices `obra_id`."""
        maquinas = (
            await self._s.execute(
                select(func.count()).select_from(AsignacionMaquinaObra).where(
                    AsignacionMaquinaObra.obra_id == obra_id
                )
            )
        ).scalar_one()
        trabajadores = (
            await self._s.execute(
                select(func.count()).select_from(AsignacionTrabajadorObra).where(
                    AsignacionTrabajadorObra.obra_id == obra_id
                )
            )
        ).scalar_one()
        reportes = (
            await self._s.execute(
                select(func.count()).select_from(ReporteDiarioObra).where(
                    ReporteDiarioObra.obra_id == obra_id
                )
            )
        ).scalar_one()
        return ConteosOperacion(
            maquinas_asignadas=int(maquinas),
            trabajadores_asignados=int(trabajadores),
            reportes_diarios=int(reportes),
        )

    async def crear_reporte(
        self, obra_id: int, datos: ReporteDiarioCrear
    ) -> ReporteDiarioObra:
        """Inserta un reporte diario de avance ligado a la obra (la `fecha` ya viene resuelta)."""
        reporte = ReporteDiarioObra(obra_id=obra_id, **datos.model_dump())
        self._s.add(reporte)
        await self._s.flush()  # asigna reporte.id
        return reporte

    async def listar_reportes(
        self, obra_id: int, *, limite: int = 100, offset: int = 0
    ) -> list[ReporteDiarioObra]:
        """Reportes diarios de una obra, más recientes primero.

        Paginado (el bot escribe un reporte por día; una obra de años acumula cientos de filas
        con texto + arrays de fotos): calca el kárdex de horas de máquina.
        """
        stmt = (
            select(ReporteDiarioObra)
            .where(ReporteDiarioObra.obra_id == obra_id)
            .order_by(ReporteDiarioObra.fecha.desc(), ReporteDiarioObra.id.desc())
            .limit(limite)
            .offset(offset)
        )
        return list((await self._s.execute(stmt)).scalars().all())

    # ---- Gasto real (Fase 3): agregación de componentes + presupuesto de la cotización --------------
    async def agregados_gasto(self, obra_id: int) -> AgregadosGastoObra:
        """Suma en SQL los 5 componentes del gasto real de la obra (una consulta por componente).

        Agrega en la base (no carga las filas): una obra de meses acumula miles de gastos/horas/consumos, y
        la regla de performance prohíbe consultas sin límite. `func.coalesce(sum, 0)` deja 0 donde no hay
        filas. Las horas se costean con la tarifa interna de CADA máquina (`costo_operacion_hora`, NULL→0,
        [DEFINIR] si el cliente no rastrea rentabilidad neta). El resultado va SIN cuantizar a la función
        pura, que redondea sólo al final (money-safe)."""
        total_gastos = (
            await self._s.execute(
                select(func.coalesce(func.sum(Gasto.monto), 0)).where(
                    Gasto.obra_id == obra_id, Gasto.anulado_en.is_(None)   # rechazados no cuentan (0056)
                )
            )
        ).scalar_one()
        total_compras = (
            await self._s.execute(
                select(func.coalesce(func.sum(Compra.total), 0)).where(Compra.obra_id == obra_id)
            )
        ).scalar_one()
        total_prorrateo = (
            await self._s.execute(
                select(func.coalesce(func.sum(ProrrateoNominaObra.costo_imputado), 0)).where(
                    ProrrateoNominaObra.obra_id == obra_id
                )
            )
        ).scalar_one()
        total_horas = (
            await self._s.execute(
                select(
                    func.coalesce(
                        func.sum(
                            RegistroHorasMaquina.horas_facturables
                            * func.coalesce(Maquina.costo_operacion_hora, 0)
                        ),
                        0,
                    )
                )
                .select_from(RegistroHorasMaquina)
                .join(Maquina, Maquina.id == RegistroHorasMaquina.maquina_id)
                .where(RegistroHorasMaquina.obra_id == obra_id)
            )
        ).scalar_one()
        total_consumos = (
            await self._s.execute(
                select(
                    func.coalesce(
                        func.sum(ConsumoInventario.cantidad * ConsumoInventario.costo_unitario), 0
                    )
                ).where(ConsumoInventario.obra_id == obra_id)
            )
        ).scalar_one()
        return AgregadosGastoObra(
            total_gastos=Decimal(total_gastos),
            total_compras=Decimal(total_compras),
            total_prorrateo_nomina=Decimal(total_prorrateo),
            total_horas_maquina=Decimal(total_horas),
            total_consumos_inventario=Decimal(total_consumos),
        )

    async def agregados_gasto_batch(
        self, obra_ids: list[int]
    ) -> dict[int, AgregadosGastoObra]:
        """Como `agregados_gasto` pero para MUCHAS obras a la vez (panel/home, Fase 8): 5 consultas
        AGRUPADAS por `obra_id` en vez de 5 por obra. Evita el N+1 del panel (regla de performance).

        Devuelve un dict `obra_id → AgregadosGastoObra`; una obra sin filas en un componente simplemente no
        aparece en ese GROUP BY y cae a 0 al componer (el service usa un cero por defecto). Con `obra_ids`
        vacío no consulta nada."""
        if not obra_ids:
            return {}
        gastos = dict(
            (
                await self._s.execute(
                    select(Gasto.obra_id, func.coalesce(func.sum(Gasto.monto), 0))
                    .where(Gasto.obra_id.in_(obra_ids), Gasto.anulado_en.is_(None))   # sin rechazados (0056)
                    .group_by(Gasto.obra_id)
                )
            ).all()
        )
        compras = dict(
            (
                await self._s.execute(
                    select(Compra.obra_id, func.coalesce(func.sum(Compra.total), 0))
                    .where(Compra.obra_id.in_(obra_ids))
                    .group_by(Compra.obra_id)
                )
            ).all()
        )
        prorrateo = dict(
            (
                await self._s.execute(
                    select(
                        ProrrateoNominaObra.obra_id,
                        func.coalesce(func.sum(ProrrateoNominaObra.costo_imputado), 0),
                    )
                    .where(ProrrateoNominaObra.obra_id.in_(obra_ids))
                    .group_by(ProrrateoNominaObra.obra_id)
                )
            ).all()
        )
        horas = dict(
            (
                await self._s.execute(
                    select(
                        RegistroHorasMaquina.obra_id,
                        func.coalesce(
                            func.sum(
                                RegistroHorasMaquina.horas_facturables
                                * func.coalesce(Maquina.costo_operacion_hora, 0)
                            ),
                            0,
                        ),
                    )
                    .select_from(RegistroHorasMaquina)
                    .join(Maquina, Maquina.id == RegistroHorasMaquina.maquina_id)
                    .where(RegistroHorasMaquina.obra_id.in_(obra_ids))
                    .group_by(RegistroHorasMaquina.obra_id)
                )
            ).all()
        )
        consumos = dict(
            (
                await self._s.execute(
                    select(
                        ConsumoInventario.obra_id,
                        func.coalesce(
                            func.sum(ConsumoInventario.cantidad * ConsumoInventario.costo_unitario), 0
                        ),
                    )
                    .where(ConsumoInventario.obra_id.in_(obra_ids))
                    .group_by(ConsumoInventario.obra_id)
                )
            ).all()
        )
        return {
            oid: AgregadosGastoObra(
                total_gastos=Decimal(gastos.get(oid, 0)),
                total_compras=Decimal(compras.get(oid, 0)),
                total_prorrateo_nomina=Decimal(prorrateo.get(oid, 0)),
                total_horas_maquina=Decimal(horas.get(oid, 0)),
                total_consumos_inventario=Decimal(consumos.get(oid, 0)),
            )
            for oid in obra_ids
        }

    async def cotizaciones_de_obras(
        self, obras: list[Obra]
    ) -> dict[int, tuple[CotizacionObra, list[ItemCotizacionObra]]]:
        """Cotización GANADA + ítems de VARIAS obras a la vez (panel/home): 2 consultas, no 2 por obra.

        Devuelve `obra_id → (cotizacion, items)` SOLO para las obras que nacieron de una cotización (las
        sueltas se omiten y el service las trata como sin presupuesto). Evita el N+1 del panel."""
        pares = [(o.id, o.cotizacion_id) for o in obras if o.cotizacion_id is not None]
        if not pares:
            return {}
        coti_ids = [cid for _oid, cid in pares]
        cotizaciones = {
            c.id: c
            for c in (
                await self._s.execute(
                    select(CotizacionObra).where(CotizacionObra.id.in_(coti_ids))
                )
            ).scalars().all()
        }
        items_por_coti: dict[int, list[ItemCotizacionObra]] = {}
        for it in (
            await self._s.execute(
                select(ItemCotizacionObra)
                .where(ItemCotizacionObra.cotizacion_id.in_(coti_ids))
                .order_by(ItemCotizacionObra.orden, ItemCotizacionObra.id)
            )
        ).scalars().all():
            items_por_coti.setdefault(it.cotizacion_id, []).append(it)
        resultado: dict[int, tuple[CotizacionObra, list[ItemCotizacionObra]]] = {}
        for oid, cid in pares:
            cotizacion = cotizaciones.get(cid)
            if cotizacion is not None:
                resultado[oid] = (cotizacion, items_por_coti.get(cid, []))
        return resultado

    async def cotizacion_de_obra(
        self, obra: Obra
    ) -> tuple[CotizacionObra, list[ItemCotizacionObra]] | None:
        """Cotización GANADA que originó la obra + sus ítems (para el presupuesto). None si es obra suelta.

        `CotizacionObra`/`ItemCotizacionObra` viven en `modules.obra.models` (propios), así que el
        presupuesto se resuelve sin tocar `modules.cotizacion_obra` (cuyo service es de otra fase)."""
        if obra.cotizacion_id is None:
            return None
        cotizacion = (
            await self._s.execute(
                select(CotizacionObra).where(CotizacionObra.id == obra.cotizacion_id)
            )
        ).scalar_one_or_none()
        if cotizacion is None:
            return None
        items = list(
            (
                await self._s.execute(
                    select(ItemCotizacionObra)
                    .where(ItemCotizacionObra.cotizacion_id == cotizacion.id)
                    .order_by(ItemCotizacionObra.orden, ItemCotizacionObra.id)
                )
            ).scalars().all()
        )
        return cotizacion, items

    # ---- Consumo de inventario (Fase 3): la fila; el movimiento lo dispara el service --------------
    async def costo_producto(
        self, producto_id: int
    ) -> tuple[Decimal | None, Decimal | None] | None:
        """(costo_promedio, precio_compra) del producto, o None si el producto NO existe.

        Se seleccionan sólo las dos columnas de costo (sin disparar el selectin de fracciones/proveedor).
        `None` (producto inexistente) lo traduce el service a `ProductoInexistente` ANTES de insertar el
        consumo, cuya FK a `productos` exigiría existencia igualmente. Un producto sin ningún costo conocido
        devuelve `(None, None)`: el service cae a 0 (material de costo desconocido cuesta 0 en la obra)."""
        fila = (
            await self._s.execute(
                select(Producto.costo_promedio, Producto.precio_compra).where(
                    Producto.id == producto_id
                )
            )
        ).first()
        if fila is None:
            return None
        return fila[0], fila[1]

    async def consumo_por_key(self, idempotency_key: str) -> ConsumoInventario | None:
        """Consumo ya asentado con esta `idempotency_key` (o None). Sostiene el replay del bot (M2).

        El índice ÚNICO PARCIAL `uq_consumos_inventario_idempotency_key` WHERE IS NOT NULL garantiza a
        lo sumo una fila por key."""
        return (
            await self._s.execute(
                select(ConsumoInventario).where(
                    ConsumoInventario.idempotency_key == idempotency_key
                )
            )
        ).scalar_one_or_none()

    async def crear_consumo(
        self,
        *,
        obra_id: int,
        producto_id: int,
        fecha: date,
        cantidad: Decimal,
        costo_unitario: Decimal,
        responsable: str | None,
        observaciones: str | None,
        idempotency_key: str | None = None,
    ) -> ConsumoInventario:
        """Inserta el `ConsumoInventario` (imputación a obra). El MOVIMIENTO de inventario lo emite el
        service por `modules.inventario` en la misma transacción (no aquí).

        Con `idempotency_key` (M2, escritura del bot) el flush va en un SAVEPOINT (`begin_nested`) y, ante
        una carrera que esquive el pre-chequeo del service, la UNIQUE PARCIAL de `idempotency_key` es la
        frontera última: se re-lee y se devuelve el consumo ya committeado por la ganadora (misma fila),
        en vez de propagar un 500 (espeja `crear_desde_cotizacion`/`crear_liquidacion`). Sin key (alta de
        dashboard) el índice parcial no aplica y se inserta directo (se permiten consumos repetidos)."""
        consumo = ConsumoInventario(
            obra_id=obra_id,
            producto_id=producto_id,
            fecha=fecha,
            cantidad=cantidad,
            costo_unitario=costo_unitario,
            responsable=responsable,
            observaciones=observaciones,
            idempotency_key=idempotency_key,
        )
        if idempotency_key is None:
            self._s.add(consumo)
            await self._s.flush()  # asigna consumo.id (ancla la idempotencia del movimiento)
            return consumo
        try:
            async with self._s.begin_nested():   # SAVEPOINT: aísla el flush de la carrera
                self._s.add(consumo)
                await self._s.flush()  # dispara la UNIQUE PARCIAL de idempotency_key
        except IntegrityError:
            existente = await self.consumo_por_key(idempotency_key)
            if existente is None:   # la colisión no fue por idempotency_key: no la tragues
                raise
            return existente
        return consumo

    # ---- Calendario de obra (commit 2): lecturas de OPERACIÓN por rango [desde, hasta], sin dinero ---
    # Una consulta por origen para todo el rango (N+1-free); el service agrega en Python. Nombres por
    # JOIN. Filtros opcionales como WHERE condicional (identificadores fijos; valores por bind param).
    async def reportes_calendario(
        self, desde: date, hasta: date, *, obra_id: int | None = None
    ) -> list[dict]:
        """Reportes diarios de avance en el rango + nombre de obra."""
        params: dict = {"desde": desde, "hasta": hasta}
        extra = ""
        if obra_id is not None:
            extra += " AND r.obra_id = :obra_id"
            params["obra_id"] = obra_id
        sql = (
            "SELECT r.id, r.obra_id, o.nombre AS obra, r.reportado_por, r.avance_descripcion, "
            "  r.m2_ejecutados, r.m3_ejecutados, r.incidentes, r.foto_urls, r.fecha "
            "FROM reportes_diarios_obra r JOIN obras o ON o.id = r.obra_id "
            "WHERE r.fecha BETWEEN :desde AND :hasta" + extra + " ORDER BY r.fecha, r.id"
        )
        return [dict(f._mapping) for f in (await self._s.execute(text(sql), params)).all()]

    async def consumos_calendario(
        self, desde: date, hasta: date, *, obra_id: int | None = None
    ) -> list[dict]:
        """Consumos de material en el rango + nombre de producto y obra. Sin costo unitario."""
        params: dict = {"desde": desde, "hasta": hasta}
        extra = ""
        if obra_id is not None:
            extra += " AND c.obra_id = :obra_id"
            params["obra_id"] = obra_id
        sql = (
            "SELECT c.id, c.obra_id, o.nombre AS obra, c.producto_id, p.nombre AS producto, "
            "  c.cantidad, c.fecha "
            "FROM consumos_inventario c "
            "JOIN obras o ON o.id = c.obra_id "
            "JOIN productos p ON p.id = c.producto_id "
            "WHERE c.fecha BETWEEN :desde AND :hasta" + extra + " ORDER BY c.fecha, c.id"
        )
        return [dict(f._mapping) for f in (await self._s.execute(text(sql), params)).all()]

    async def hitos_calendario(self, desde: date, hasta: date) -> list[dict]:
        """Hitos de obra que caen en el rango: UNION ALL de inicio / fin_estimada / fin_real (obras vivas).

        La fecha del evento es la fecha del hito; `estado` es el estado actual de la obra. El filtro por
        `obra_id` lo aplica el service en Python (el mismo UNION sirve a todas las obras)."""
        sql = (
            "SELECT h.obra_id, h.obra, h.hito, h.estado, h.fecha FROM ("
            "  SELECT o.id AS obra_id, o.nombre AS obra, 'inicio' AS hito, o.estado, "
            "    o.fecha_inicio AS fecha FROM obras o "
            "  WHERE o.eliminado_en IS NULL AND o.fecha_inicio IS NOT NULL "
            "  UNION ALL "
            "  SELECT o.id, o.nombre, 'fin_estimada', o.estado, o.fecha_fin_estimada FROM obras o "
            "  WHERE o.eliminado_en IS NULL AND o.fecha_fin_estimada IS NOT NULL "
            "  UNION ALL "
            "  SELECT o.id, o.nombre, 'fin_real', o.estado, o.fecha_fin_real FROM obras o "
            "  WHERE o.eliminado_en IS NULL AND o.fecha_fin_real IS NOT NULL"
            ") h WHERE h.fecha BETWEEN :desde AND :hasta ORDER BY h.fecha, h.obra_id"
        )
        return [
            dict(f._mapping)
            for f in (await self._s.execute(text(sql), {"desde": desde, "hasta": hasta})).all()
        ]

    # ---- Liquidación (Fase 3): snapshot inmutable, idempotente por UNIQUE(obra_id) -----------------
    async def obtener_liquidacion(self, obra_id: int) -> LiquidacionObra | None:
        """Liquidación ya existente de la obra (o None). Sostiene la idempotencia del liquidar."""
        return (
            await self._s.execute(
                select(LiquidacionObra).where(LiquidacionObra.obra_id == obra_id)
            )
        ).scalar_one_or_none()

    async def crear_liquidacion(
        self, obra_id: int, valores: dict, snapshot_json: dict
    ) -> LiquidacionObra:
        """Inserta el snapshot inmutable de liquidación. Idempotente ante CARRERA: la UNIQUE(obra_id) es la
        frontera última; el flush va en un SAVEPOINT (`begin_nested`) y, si choca, se re-lee y devuelve la
        liquidación ya committeada por la ganadora (misma fila) en vez de propagar un 500 (espeja el patrón
        de `crear_desde_cotizacion`)."""
        liquidacion = LiquidacionObra(obra_id=obra_id, snapshot_json=snapshot_json, **valores)
        try:
            async with self._s.begin_nested():   # SAVEPOINT: aísla el flush de la carrera
                self._s.add(liquidacion)
                await self._s.flush()  # dispara la UNIQUE de obra_id
        except IntegrityError:
            existente = await self.obtener_liquidacion(obra_id)
            if existente is None:   # la colisión no fue por obra_id: no la tragues
                raise
            return existente
        return liquidacion
