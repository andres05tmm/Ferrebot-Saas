"""Repositorio de maquinaria: único lugar con SQL del módulo (regla no negociable #2).

Calca `modules/inventario/repository.py`: la sesión del tenant ES la transacción; el aislamiento lo da
la base (sin `empresa_id`). Soft delete por `eliminado_en` (NULL = viva): las lecturas ocultan las
eliminadas y `codigo_existe` mira TODAS las filas (incluidas las borradas) porque el UNIQUE de la BD no
distingue soft delete —así el 409 se anticipa en vez de reventar como IntegrityError al hacer flush.
"""
from datetime import date
from decimal import Decimal

from sqlalchemy import BigInteger, Date, and_, column, func, or_, select, text, values
from sqlalchemy.ext.asyncio import AsyncSession

from core.config.timezone import now_co
from modules.maquinaria.models import (
    AsignacionMaquinaObra,
    Mantenimiento,
    Maquina,
    RegistroHorasMaquina,
)
from modules.maquinaria.schemas import MaquinaCrear


class SqlMaquinasRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def listar(self, *, estado: str | None = None, q: str | None = None) -> list[Maquina]:
        """Máquinas vivas (no eliminadas) ordenadas por código; filtra por `estado` y/o `q`
        (código o nombre, ILIKE)."""
        stmt = select(Maquina).where(Maquina.eliminado_en.is_(None))
        if estado is not None:
            stmt = stmt.where(Maquina.estado == estado)
        if q:
            patron = f"%{q}%"
            stmt = stmt.where(or_(Maquina.codigo.ilike(patron), Maquina.nombre.ilike(patron)))
        stmt = stmt.order_by(Maquina.codigo)
        return list((await self._s.execute(stmt)).scalars().all())

    async def obtener(self, maquina_id: int) -> Maquina | None:
        """Máquina viva por id (una eliminada se trata como inexistente → 404)."""
        return (
            await self._s.execute(
                select(Maquina).where(
                    Maquina.id == maquina_id, Maquina.eliminado_en.is_(None)
                )
            )
        ).scalar_one_or_none()

    async def codigo_existe(self, codigo: str, *, excluir_id: int | None = None) -> bool:
        """¿Otra máquina ya usa este código? Mira TODAS las filas (el UNIQUE de la BD incluye las
        soft-deleted); `excluir_id` se ignora a sí mismo al editar."""
        stmt = select(Maquina.id).where(Maquina.codigo == codigo)
        if excluir_id is not None:
            stmt = stmt.where(Maquina.id != excluir_id)
        return (await self._s.execute(stmt.limit(1))).first() is not None

    async def crear(self, datos: MaquinaCrear) -> Maquina:
        maquina = Maquina(**datos.model_dump())
        self._s.add(maquina)
        await self._s.flush()  # asigna maquina.id
        return maquina

    async def actualizar(self, maquina: Maquina, cambios: dict) -> Maquina:
        """Aplica `cambios` (dict campo→valor ya validado) sobre la máquina cargada.

        `actualizado_en` (onupdate=func.now()) queda EXPIRADO tras el UPDATE (lo computa el servidor). Se
        `refresh` dentro del await para repoblarlo: así el router (`MaquinaLeer.model_validate`) serializa una
        fila completa sin disparar IO perezosa fuera del greenlet (`MissingGreenlet` → 500)."""
        for campo, valor in cambios.items():
            setattr(maquina, campo, valor)
        await self._s.flush()
        await self._s.refresh(maquina)
        return maquina

    async def soft_delete(self, maquina_id: int) -> bool:
        """Marca la máquina como eliminada (`eliminado_en = ahora Colombia`); nunca hard-delete
        (la referencian asignaciones/horas/mantenimientos). Devuelve False si no existe o ya estaba
        eliminada."""
        maquina = await self.obtener(maquina_id)
        if maquina is None:
            return False
        maquina.eliminado_en = now_co()
        await self._s.flush()
        return True

    # ---- Lecturas de operación (solo lectura; el registro es de Fase 3) ------
    async def listar_asignaciones(self, maquina_id: int) -> list[AsignacionMaquinaObra]:
        """Asignaciones a obra de una máquina, la más reciente primero."""
        stmt = (
            select(AsignacionMaquinaObra)
            .where(AsignacionMaquinaObra.maquina_id == maquina_id)
            .order_by(AsignacionMaquinaObra.fecha_inicio.desc(), AsignacionMaquinaObra.id.desc())
        )
        return list((await self._s.execute(stmt)).scalars().all())

    async def listar_horas(
        self, maquina_id: int, *, limite: int = 100, offset: int = 0
    ) -> list[RegistroHorasMaquina]:
        """Partes de horas de una máquina (kárdex de operación), el más reciente primero."""
        stmt = (
            select(RegistroHorasMaquina)
            .where(RegistroHorasMaquina.maquina_id == maquina_id)
            .order_by(RegistroHorasMaquina.fecha.desc(), RegistroHorasMaquina.id.desc())
            .limit(limite)
            .offset(offset)
        )
        return list((await self._s.execute(stmt)).scalars().all())

    # ---- Registro de horas (WRITE, Fase 3) ----------------------------------
    async def asignacion_activa(
        self, maquina_id: int, obra_id: int, fecha: date, *, bloquear: bool = False
    ) -> AsignacionMaquinaObra | None:
        """Asignación ACTIVA de (máquina, obra) que cubre `fecha` (aporta precio y mínimo pactados).

        Cubrir = `activa` AND `fecha_inicio <= fecha <= fecha_fin` (con `fecha_fin` NULL = sin cierre). Si
        hubiera varias, gana la de arranque más reciente. `bloquear=True` la toma `FOR UPDATE` para
        SERIALIZAR partes concurrentes del mismo día (mismo patrón de lock-de-ancla que `modules/fiados`).
        """
        stmt = (
            select(AsignacionMaquinaObra)
            .where(
                AsignacionMaquinaObra.maquina_id == maquina_id,
                AsignacionMaquinaObra.obra_id == obra_id,
                AsignacionMaquinaObra.activa.is_(True),
                AsignacionMaquinaObra.fecha_inicio <= fecha,
                or_(
                    AsignacionMaquinaObra.fecha_fin.is_(None),
                    AsignacionMaquinaObra.fecha_fin >= fecha,
                ),
            )
            .order_by(AsignacionMaquinaObra.fecha_inicio.desc(), AsignacionMaquinaObra.id.desc())
            .limit(1)
        )
        if bloquear:
            stmt = stmt.with_for_update()
        return (await self._s.execute(stmt)).scalar_one_or_none()

    async def registro_del_dia(
        self, maquina_id: int, obra_id: int, fecha: date
    ) -> RegistroHorasMaquina | None:
        """Parte YA registrado para la clave natural `(maquina, obra, fecha)` (ancla de idempotencia:
        un parte por máquina por día)."""
        stmt = select(RegistroHorasMaquina).where(
            RegistroHorasMaquina.maquina_id == maquina_id,
            RegistroHorasMaquina.obra_id == obra_id,
            RegistroHorasMaquina.fecha == fecha,
        ).limit(1)
        return (await self._s.execute(stmt)).scalar_one_or_none()

    async def crear_registro_horas(
        self,
        *,
        maquina_id: int,
        obra_id: int,
        fecha: date,
        horas_trabajadas: Decimal,
        horas_facturables: Decimal,
        operador_id: int | None,
        observaciones: str | None,
        origen_registro: str,
    ) -> RegistroHorasMaquina:
        """Inserta el parte de horas y hace flush (asigna `id`). La sesión del tenant ES la transacción;
        aquí no se hace commit (el registro y el consumo de cartera de Fase 5 commitean juntos)."""
        registro = RegistroHorasMaquina(
            maquina_id=maquina_id,
            obra_id=obra_id,
            fecha=fecha,
            horas_trabajadas=horas_trabajadas,
            horas_facturables=horas_facturables,
            operador_id=operador_id,
            observaciones=observaciones,
            origen_registro=origen_registro,
        )
        self._s.add(registro)
        await self._s.flush()  # asigna registro.id
        return registro

    # ---- Mantenimientos (Fase 1 del cockpit): CRUD sobre la tabla de la migración 0045 -------------
    async def listar_mantenimientos(
        self, maquina_id: int, *, limite: int = 100, offset: int = 0
    ) -> list[Mantenimiento]:
        """Mantenimientos de una máquina, el más reciente primero (fecha DESC, id como desempate).

        Paginado (una máquina de años acumula decenas de servicios): calca el kárdex de horas."""
        stmt = (
            select(Mantenimiento)
            .where(Mantenimiento.maquina_id == maquina_id)
            .order_by(Mantenimiento.fecha.desc(), Mantenimiento.id.desc())
            .limit(limite)
            .offset(offset)
        )
        return list((await self._s.execute(stmt)).scalars().all())

    async def obtener_mantenimiento(
        self, maquina_id: int, mantenimiento_id: int
    ) -> Mantenimiento | None:
        """Mantenimiento por id ACOTADO a su máquina: uno de otra máquina se trata como inexistente
        para ésta (no se toca por la ruta de una máquina ajena)."""
        return (
            await self._s.execute(
                select(Mantenimiento).where(
                    Mantenimiento.id == mantenimiento_id,
                    Mantenimiento.maquina_id == maquina_id,
                )
            )
        ).scalar_one_or_none()

    async def crear_mantenimiento(self, maquina_id: int, datos: dict) -> Mantenimiento:
        """Inserta el mantenimiento (la `fecha` ya viene resuelta a hoy Colombia por el service). El
        server_default de `creado_en` se repuebla por RETURNING en el flush (serialización pura)."""
        mant = Mantenimiento(maquina_id=maquina_id, **datos)
        self._s.add(mant)
        await self._s.flush()  # asigna mant.id
        return mant

    async def actualizar_mantenimiento(
        self, mant: Mantenimiento, cambios: dict
    ) -> Mantenimiento:
        """Aplica un parche parcial (solo claves presentes). La tabla no tiene `actualizado_en`
        (`onupdate`), así que no hace falta `refresh` para la serialización."""
        for campo, valor in cambios.items():
            setattr(mant, campo, valor)
        await self._s.flush()
        return mant

    async def eliminar_mantenimiento(self, mant: Mantenimiento) -> None:
        """DELETE DURO: la tabla `mantenimientos` NO tiene columna `eliminado_en` (soft delete) y no se
        crean migraciones nuevas en esta fase, así que borrar un mantenimiento elimina la fila de verdad.
        Es un registro de bitácora auxiliar (no mueve stock ni caja): no arrastra invariantes al borrarse."""
        await self._s.delete(mant)
        await self._s.flush()

    # ---- Agregados que consume el dashboard de obra (Fase 2), batcheados (sin N+1) ----------------
    async def ultimo_mantenimiento_por_maquina(self) -> dict[int, Mantenimiento]:
        """Último mantenimiento (por fecha, id como desempate) de CADA máquina, en UNA consulta.

        `DISTINCT ON (maquina_id)` de Postgres: ordena por `(maquina_id, fecha DESC, id DESC)` y toma la
        primera fila de cada grupo. Devuelve `maquina_id → Mantenimiento`; el dashboard deriva de aquí las
        alertas de mantenimiento vencido/próximo (por fecha o por horómetro)."""
        stmt = (
            select(Mantenimiento)
            .order_by(
                Mantenimiento.maquina_id,
                Mantenimiento.fecha.desc(),
                Mantenimiento.id.desc(),
            )
            .distinct(Mantenimiento.maquina_id)
        )
        filas = (await self._s.execute(stmt)).scalars().all()
        return {m.maquina_id: m for m in filas}

    async def horas_desde(self, pares: list[tuple[int, date]]) -> dict[int, Decimal]:
        """Σ `horas_facturables` de cada máquina en los partes POSTERIORES a una fecha de corte.

        `pares` = [(maquina_id, corte)], donde `corte` es la fecha del último mantenimiento de esa máquina.
        Devuelve `maquina_id → horas acumuladas` (contra `proximo_en_horas` para la alerta por horómetro).
        Batcheado con un `VALUES` inline (una sola consulta, N+1-free); el LEFT JOIN deja en 0 la máquina
        sin partes tras el corte. El umbral es ESTRICTO (`fecha > corte`): el día del mantenimiento no
        cuenta como uso acumulado posterior."""
        if not pares:
            return {}
        cortes = values(
            column("maquina_id", BigInteger),
            column("corte", Date),
            name="cortes_mantenimiento",
        ).data([(int(mid), corte) for mid, corte in pares])
        stmt = (
            select(
                cortes.c.maquina_id,
                func.coalesce(func.sum(RegistroHorasMaquina.horas_facturables), 0),
            )
            .select_from(cortes)
            .join(
                RegistroHorasMaquina,
                and_(
                    RegistroHorasMaquina.maquina_id == cortes.c.maquina_id,
                    RegistroHorasMaquina.fecha > cortes.c.corte,
                ),
                isouter=True,
            )
            .group_by(cortes.c.maquina_id)
        )
        filas = (await self._s.execute(stmt)).all()
        return {int(mid): Decimal(total) for mid, total in filas}

    # LATERAL que resuelve, para un parte, su asignación VIGENTE en la fecha del parte y devuelve el precio
    # pactado. Misma resolución que `asignacion_activa` (repository.py): activa, fecha_inicio ≤ fecha ≤
    # fecha_fin/NULL, gana la de arranque más reciente (desempata solapes por fecha_inicio DESC, id DESC).
    _LATERAL_PRECIO = (
        "JOIN LATERAL ("
        "  SELECT amo.precio_hora, amo.obra_id, amo.operador_id, amo.id "
        "  FROM asignaciones_maquina_obra amo "
        "  WHERE amo.maquina_id = rh.maquina_id AND amo.obra_id = rh.obra_id AND amo.activa "
        "    AND amo.fecha_inicio <= rh.fecha AND (amo.fecha_fin IS NULL OR amo.fecha_fin >= rh.fecha) "
        "  ORDER BY amo.fecha_inicio DESC, amo.id DESC LIMIT 1"
        ") a ON true "
    )

    async def ingreso_alquiler_mes(self, *, desde: date, hasta: date) -> Decimal:
        """Σ (horas_facturables × precio_hora pactado) de los partes del mes [desde, hasta] (columnas DATE).

        Por cada parte, un LATERAL toma su asignación vigente en la fecha (precio PACTADO, no el default de
        la máquina); INNER porque un parte sin asignación que lo cubra no factura. N+1-free."""
        total = (
            await self._s.execute(
                text(
                    "SELECT COALESCE(SUM(rh.horas_facturables * a.precio_hora), 0) "
                    "FROM registros_horas_maquina rh " + self._LATERAL_PRECIO +
                    "WHERE rh.fecha BETWEEN :desde AND :hasta"
                ),
                {"desde": desde, "hasta": hasta},
            )
        ).scalar_one()
        return Decimal(total)

    async def top_maquinas_mes(
        self, *, desde: date, hasta: date, limite: int = 5
    ) -> list[dict]:
        """Top `limite` máquinas por horas facturadas del mes: (maquina_id, maquina, horas, ingreso).

        Mismo LATERAL de precio pactado; agrega por máquina y ordena por horas (ingreso como desempate)."""
        filas = (
            await self._s.execute(
                text(
                    "SELECT rh.maquina_id, m.nombre AS maquina, "
                    "       SUM(rh.horas_facturables) AS horas, "
                    "       SUM(rh.horas_facturables * a.precio_hora) AS ingreso "
                    "FROM registros_horas_maquina rh "
                    "JOIN maquinas m ON m.id = rh.maquina_id " + self._LATERAL_PRECIO +
                    "WHERE rh.fecha BETWEEN :desde AND :hasta "
                    "GROUP BY rh.maquina_id, m.nombre "
                    "ORDER BY horas DESC, ingreso DESC, rh.maquina_id LIMIT :limite"
                ),
                {"desde": desde, "hasta": hasta, "limite": limite},
            )
        ).all()
        return [dict(f._mapping) for f in filas]

    async def ocupadas_hoy(self, *, hoy: date) -> list[dict]:
        """Máquinas OCUPADAS con asignación activa hoy + horas/ingreso del día + nombres de obra y operador.

        LATERAL toma la asignación vigente de la máquina hoy (obra/operador/precio); LEFT JOIN a los partes
        de hoy (una máquina puede estar OCUPADA sin parte cargado aún → horas/ingreso 0). Operador = nombres
        + apellidos del trabajador. Una sola consulta (sin N+1)."""
        filas = (
            await self._s.execute(
                text(
                    "SELECT m.id AS maquina_id, m.nombre AS maquina, o.nombre AS obra_nombre, "
                    "  NULLIF(TRIM(COALESCE(t.nombres,'') || ' ' || COALESCE(t.apellidos,'')), '') "
                    "    AS operador_nombre, "
                    "  COALESCE(rh.horas_facturables, 0) AS horas_hoy, "
                    "  COALESCE(rh.horas_facturables * a.precio_hora, 0) AS ingreso_hoy "
                    "FROM maquinas m "
                    "JOIN LATERAL ("
                    "  SELECT amo.obra_id, amo.operador_id, amo.precio_hora "
                    "  FROM asignaciones_maquina_obra amo "
                    "  WHERE amo.maquina_id = m.id AND amo.activa AND amo.fecha_inicio <= :hoy "
                    "    AND (amo.fecha_fin IS NULL OR amo.fecha_fin >= :hoy) "
                    "  ORDER BY amo.fecha_inicio DESC, amo.id DESC LIMIT 1"
                    ") a ON true "
                    "LEFT JOIN obras o ON o.id = a.obra_id "
                    "LEFT JOIN trabajadores t ON t.id = a.operador_id "
                    "LEFT JOIN registros_horas_maquina rh "
                    "  ON rh.maquina_id = m.id AND rh.obra_id = a.obra_id AND rh.fecha = :hoy "
                    "WHERE m.estado = 'OCUPADA' AND m.eliminado_en IS NULL "
                    "ORDER BY ingreso_hoy DESC, m.codigo"
                ),
                {"hoy": hoy},
            )
        ).all()
        return [dict(f._mapping) for f in filas]
