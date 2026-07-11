"""Repositorio de maquinaria: único lugar con SQL del módulo (regla no negociable #2).

Calca `modules/inventario/repository.py`: la sesión del tenant ES la transacción; el aislamiento lo da
la base (sin `empresa_id`). Soft delete por `eliminado_en` (NULL = viva): las lecturas ocultan las
eliminadas y `codigo_existe` mira TODAS las filas (incluidas las borradas) porque el UNIQUE de la BD no
distingue soft delete —así el 409 se anticipa en vez de reventar como IntegrityError al hacer flush.
"""
from calendar import monthrange
from datetime import date, time
from decimal import Decimal

from sqlalchemy import BigInteger, Date, and_, column, func, or_, select, text, values
from sqlalchemy.ext.asyncio import AsyncSession

from core.config.timezone import now_co
from core.events import publish
from modules.maquinaria.models import (
    AsignacionMaquinaObra,
    Mantenimiento,
    Maquina,
    RegistroHorasMaquina,
    TurnoHorasMaquina,
)
from modules.maquinaria.schemas import MaquinaCrear
# Lecturas SOLO de existencia/estado (patrón Ola A: se importa el modelo congelado de otro paquete sin
# acoplarse por relationship). No se escriben desde este repo.
from modules.obra.models import Obra
from modules.trabajadores.models import Trabajador


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

    # ---- CRUD de asignaciones máquina→obra (Calendario de obra) ---------------------------------
    async def obtener_asignacion(
        self, maquina_id: int, asignacion_id: int
    ) -> AsignacionMaquinaObra | None:
        """Asignación por id ACOTADA a su máquina: una de otra máquina se trata como inexistente
        para ésta (no se toca por la ruta de una máquina ajena; calca `obtener_mantenimiento`)."""
        return (
            await self._s.execute(
                select(AsignacionMaquinaObra).where(
                    AsignacionMaquinaObra.id == asignacion_id,
                    AsignacionMaquinaObra.maquina_id == maquina_id,
                )
            )
        ).scalar_one_or_none()

    async def asignacion_solapada(
        self,
        maquina_id: int,
        fecha_inicio: date,
        fecha_fin: date | None,
        *,
        excluir_id: int | None = None,
    ) -> bool:
        """¿Hay otra asignación ACTIVA de la máquina cuyo rango se cruza con [fecha_inicio, fecha_fin]?

        Intervalos con `fecha_fin` NULL = abiertos (infinito). Dos rangos se solapan cuando
        `nueva.inicio <= existente.fin` Y `nueva.fin >= existente.inicio` (con los NULL tratados como
        +infinito vía `or_(fecha_fin IS NULL, ...)`, el patrón del módulo). Solo mira filas `activa=true`;
        `excluir_id` se ignora a sí mismo al editar."""
        stmt = select(AsignacionMaquinaObra.id).where(
            AsignacionMaquinaObra.maquina_id == maquina_id,
            AsignacionMaquinaObra.activa.is_(True),
            # nueva.inicio <= existente.fin (o existente.fin es NULL = infinito)
            or_(
                AsignacionMaquinaObra.fecha_fin.is_(None),
                AsignacionMaquinaObra.fecha_fin >= fecha_inicio,
            ),
        )
        if fecha_fin is not None:
            # nueva.fin >= existente.inicio (si nueva.fin es NULL = infinito, siempre se cumple)
            stmt = stmt.where(AsignacionMaquinaObra.fecha_inicio <= fecha_fin)
        if excluir_id is not None:
            stmt = stmt.where(AsignacionMaquinaObra.id != excluir_id)
        return (await self._s.execute(stmt.limit(1))).first() is not None

    async def otra_asignacion_vigente(
        self, maquina_id: int, fecha: date, *, excluir_id: int
    ) -> bool:
        """¿Queda OTRA asignación activa de la máquina que cubra `fecha` (además de `excluir_id`)?

        Cubrir = `activa` AND `fecha_inicio <= fecha <= fecha_fin`/NULL. Sirve para decidir si al cerrar
        una asignación la máquina puede volver a DISPONIBLE (no si otra la sigue teniendo ocupada hoy)."""
        stmt = (
            select(AsignacionMaquinaObra.id)
            .where(
                AsignacionMaquinaObra.maquina_id == maquina_id,
                AsignacionMaquinaObra.activa.is_(True),
                AsignacionMaquinaObra.id != excluir_id,
                AsignacionMaquinaObra.fecha_inicio <= fecha,
                or_(
                    AsignacionMaquinaObra.fecha_fin.is_(None),
                    AsignacionMaquinaObra.fecha_fin >= fecha,
                ),
            )
            .limit(1)
        )
        return (await self._s.execute(stmt)).first() is not None

    async def obra_asignable(self, obra_id: int) -> str | None:
        """Estado de la obra viva por id, o None si no existe / está soft-deleted.

        El service usa el estado devuelto para distinguir el mapeo HTTP: None → 404 (inexistente),
        `"LIQUIDADA"` → 409 (cerrada). Lectura de solo existencia sobre `obras` (no se escribe)."""
        return (
            await self._s.execute(
                select(Obra.estado).where(Obra.id == obra_id, Obra.eliminado_en.is_(None))
            )
        ).scalar_one_or_none()

    async def operador_valido(self, operador_id: int) -> bool:
        """¿El operador existe como trabajador ACTIVO y no eliminado? Lectura de solo existencia."""
        stmt = select(Trabajador.id).where(
            Trabajador.id == operador_id,
            Trabajador.activo.is_(True),
            Trabajador.eliminado_en.is_(None),
        )
        return (await self._s.execute(stmt.limit(1))).first() is not None

    async def crear_asignacion(
        self,
        *,
        maquina_id: int,
        obra_id: int,
        fecha_inicio: date,
        fecha_fin: date | None,
        precio_hora: Decimal,
        minimo_horas: int,
        operador_id: int | None,
        activa: bool = True,
    ) -> AsignacionMaquinaObra:
        """Inserta la asignación, hace flush (asigna `id`) y emite el evento SSE del calendario. La sesión
        del tenant ES la transacción (sin commit aquí); el NOTIFY sale al commit del llamador."""
        asig = AsignacionMaquinaObra(
            maquina_id=maquina_id,
            obra_id=obra_id,
            fecha_inicio=fecha_inicio,
            fecha_fin=fecha_fin,
            precio_hora=precio_hora,
            minimo_horas=minimo_horas,
            operador_id=operador_id,
            activa=activa,
        )
        self._s.add(asig)
        await self._s.flush()  # asigna asig.id
        await self._publicar_asignacion(asig)
        return asig

    async def actualizar_asignacion(
        self, asig: AsignacionMaquinaObra, cambios: dict
    ) -> AsignacionMaquinaObra:
        """Aplica un parche parcial (solo claves presentes) y reemite el evento. La tabla no tiene
        `actualizado_en`, así que no hace falta `refresh` para serializar."""
        for campo, valor in cambios.items():
            setattr(asig, campo, valor)
        await self._s.flush()
        await self._publicar_asignacion(asig)
        return asig

    async def _publicar_asignacion(self, asig: AsignacionMaquinaObra) -> None:
        """Evento SSE que consume el calendario de obra (alta y edición comparten payload)."""
        await publish(
            self._s,
            "asignacion_maquina_actualizada",
            {
                "asignacion_id": asig.id,
                "maquina_id": asig.maquina_id,
                "obra_id": asig.obra_id,
                "activa": asig.activa,
            },
        )

    async def set_estado_maquina(self, maquina: Maquina, estado: str) -> None:
        """Transiciona el estado de la máquina (helper de la asignación). Solo flush: evita el `refresh`
        de `actualizar` (no se serializa la máquina en este flujo)."""
        maquina.estado = estado
        await self._s.flush()

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

    async def obtener_registro(self, registro_id: int) -> RegistroHorasMaquina | None:
        """Parte de horas por id (para reconstruir el resumen de una sesión ya finalizada — replay)."""
        return (
            await self._s.execute(
                select(RegistroHorasMaquina).where(RegistroHorasMaquina.id == registro_id)
            )
        ).scalar_one_or_none()

    async def asignaciones_vigentes_hoy(
        self, maquina_id: int, hoy: date
    ) -> list[AsignacionMaquinaObra]:
        """Asignaciones ACTIVAS de la máquina que cubren `hoy` (activa, fecha_inicio ≤ hoy ≤ fecha_fin/
        NULL). Sirve para inferir la obra al iniciar una operación cuando hay una sola vigente."""
        stmt = (
            select(AsignacionMaquinaObra)
            .where(
                AsignacionMaquinaObra.maquina_id == maquina_id,
                AsignacionMaquinaObra.activa.is_(True),
                AsignacionMaquinaObra.fecha_inicio <= hoy,
                or_(
                    AsignacionMaquinaObra.fecha_fin.is_(None),
                    AsignacionMaquinaObra.fecha_fin >= hoy,
                ),
            )
            .order_by(AsignacionMaquinaObra.fecha_inicio.desc(), AsignacionMaquinaObra.id.desc())
        )
        return list((await self._s.execute(stmt)).scalars().all())

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
        await self._publicar_registro_horas(registro)
        return registro

    async def _publicar_registro_horas(self, registro: RegistroHorasMaquina) -> None:
        """Evento SSE del parte de horas (alta y recálculo por turno comparten payload)."""
        await publish(
            self._s,
            "registro_horas_creado",
            {
                "registro_id": registro.id,
                "maquina_id": registro.maquina_id,
                "obra_id": registro.obra_id,
                "fecha": registro.fecha,
            },
        )

    async def actualizar_registro_horas(
        self,
        registro: RegistroHorasMaquina,
        *,
        horas_trabajadas: Decimal,
        horas_facturables: Decimal,
        operador_id: int | None,
    ) -> RegistroHorasMaquina:
        """Recalcula el agregado del parte cuando entra un turno nuevo: `horas_trabajadas` = Σ turnos,
        `horas_facturables` = max(Σ, mínimo) del DÍA (el mínimo se aplica UNA vez), `operador_id` = el único
        operador o NULL si hay >1 distinto. Reemite `registro_horas_creado`. Sin commit (la sesión ES la
        transacción; el registro y el cargo delta de cartera commitean juntos)."""
        registro.horas_trabajadas = horas_trabajadas
        registro.horas_facturables = horas_facturables
        registro.operador_id = operador_id
        await self._s.flush()
        await self._publicar_registro_horas(registro)
        return registro

    # ---- Turnos del parte (rotación de operadores, migración 0054) --------------------------------
    async def crear_turno(
        self,
        *,
        registro_horas_id: int,
        operador_id: int | None,
        hora_inicio: time | None,
        hora_fin: time | None,
        horas: Decimal,
    ) -> TurnoHorasMaquina:
        """Inserta una franja de operador del parte y hace flush (asigna `id`)."""
        turno = TurnoHorasMaquina(
            registro_horas_id=registro_horas_id,
            operador_id=operador_id,
            hora_inicio=hora_inicio,
            hora_fin=hora_fin,
            horas=horas,
        )
        self._s.add(turno)
        await self._s.flush()  # asigna turno.id
        return turno

    async def turnos_por_registros(self, registro_ids: list[int]) -> dict[int, list[dict]]:
        """Turnos de varios partes en UNA consulta (N+1-free): `registro_horas_id → [turno, ...]` con el
        nombre del operador resuelto (LEFT JOIN `trabajadores`). Ordenados por franja (hora_inicio, id).

        Cada turno: `{id, operador_id, operador, hora_inicio, hora_fin, horas}`. `[]` para un parte sin
        turnos (los legacy). Sirve al POST/GET de horas y al calendario."""
        if not registro_ids:
            return {}
        filas = (
            await self._s.execute(
                text(
                    "SELECT th.registro_horas_id, th.id, th.operador_id, "
                    "  NULLIF(TRIM(COALESCE(t.nombres,'') || ' ' || COALESCE(t.apellidos,'')), '') "
                    "    AS operador, "
                    "  th.hora_inicio, th.hora_fin, th.horas "
                    "FROM turnos_horas_maquina th "
                    "LEFT JOIN trabajadores t ON t.id = th.operador_id "
                    "WHERE th.registro_horas_id = ANY(:ids) "
                    "ORDER BY th.registro_horas_id, th.hora_inicio NULLS FIRST, th.id"
                ),
                {"ids": registro_ids},
            )
        ).all()
        agrupado: dict[int, list[dict]] = {}
        for fila in filas:
            datos = dict(fila._mapping)
            agrupado.setdefault(datos.pop("registro_horas_id"), []).append(datos)
        return agrupado

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
        await publish(
            self._s,
            "mantenimiento_registrado",
            {
                "mantenimiento_id": mant.id,
                "maquina_id": mant.maquina_id,
                "tipo": mant.tipo,
                "fecha": mant.fecha,
            },
        )
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

    async def estado_maquinas_hoy(self, hoy: date) -> list[dict]:
        """Estado ACTUAL de TODAS las máquinas vivas: su asignación vigente hoy (obra/operador/desde) y
        las horas trabajadas del mes calendario de `hoy`. Sin dinero.

        LEFT JOIN LATERAL a la asignación vigente hoy (activa, fecha_inicio ≤ hoy ≤ fecha_fin/NULL; si hay
        varias, la de arranque más reciente): una máquina DISPONIBLE sin asignación conserva obra/operador/
        desde en NULL. `horas_mes` = Σ horas_trabajadas de sus partes del mes de `hoy` (subquery escalar,
        COALESCE a 0 cuando no hay partes). Orden: con obra primero, luego nombre. Una sola consulta (sin
        N+1). Molde: `ocupadas_hoy` (mismo LATERAL de asignación vigente + nombres de obra/operador)."""
        mes_desde = hoy.replace(day=1)
        mes_hasta = hoy.replace(day=monthrange(hoy.year, hoy.month)[1])
        filas = (
            await self._s.execute(
                text(
                    "SELECT m.id AS maquina_id, m.nombre AS maquina, m.estado AS estado, "
                    "  a.obra_id, o.nombre AS obra, a.operador_id, "
                    "  NULLIF(TRIM(COALESCE(t.nombres,'') || ' ' || COALESCE(t.apellidos,'')), '') "
                    "    AS operador, "
                    "  a.fecha_inicio AS desde, "
                    "  COALESCE(("
                    "    SELECT SUM(rh.horas_trabajadas) FROM registros_horas_maquina rh "
                    "    WHERE rh.maquina_id = m.id AND rh.fecha BETWEEN :mes_desde AND :mes_hasta"
                    "  ), 0) AS horas_mes "
                    "FROM maquinas m "
                    "LEFT JOIN LATERAL ("
                    "  SELECT amo.obra_id, amo.operador_id, amo.fecha_inicio "
                    "  FROM asignaciones_maquina_obra amo "
                    "  WHERE amo.maquina_id = m.id AND amo.activa AND amo.fecha_inicio <= :hoy "
                    "    AND (amo.fecha_fin IS NULL OR amo.fecha_fin >= :hoy) "
                    "  ORDER BY amo.fecha_inicio DESC, amo.id DESC LIMIT 1"
                    ") a ON true "
                    "LEFT JOIN obras o ON o.id = a.obra_id "
                    "LEFT JOIN trabajadores t ON t.id = a.operador_id "
                    "WHERE m.eliminado_en IS NULL "
                    "ORDER BY (a.obra_id IS NULL), m.nombre, m.codigo"
                ),
                {"hoy": hoy, "mes_desde": mes_desde, "mes_hasta": mes_hasta},
            )
        ).all()
        return [dict(f._mapping) for f in filas]

    # ---- Calendario de obra (commit 2): lecturas de OPERACIÓN por rango [desde, hasta], sin dinero ---
    # Cada método corre UNA consulta para todo el rango (el mes agrega en Python; el detalle usa
    # desde=hasta=fecha) — N+1-free. Los nombres (máquina/obra/operador) se resuelven por JOIN. Los
    # filtros opcionales se aplican como WHERE condicional (identificadores fijos; valores por bind param).
    async def horas_calendario(
        self,
        desde: date,
        hasta: date,
        *,
        maquina_id: int | None = None,
        obra_id: int | None = None,
        operador_id: int | None = None,
    ) -> list[dict]:
        """Partes de horas en el rango + nombres. Operador = COALESCE(parte, asignación vigente) resuelto a
        nombre; LEFT JOIN LATERAL a la asignación vigente (variante LEFT del `_LATERAL_PRECIO`, sin precio)."""
        params: dict = {"desde": desde, "hasta": hasta}
        extra = ""
        if maquina_id is not None:
            extra += " AND rh.maquina_id = :maquina_id"
            params["maquina_id"] = maquina_id
        if obra_id is not None:
            extra += " AND rh.obra_id = :obra_id"
            params["obra_id"] = obra_id
        if operador_id is not None:
            extra += " AND COALESCE(rh.operador_id, a.operador_id) = :operador_id"
            params["operador_id"] = operador_id
        sql = (
            "SELECT rh.id, rh.maquina_id, m.nombre AS maquina, rh.obra_id, o.nombre AS obra, "
            "  COALESCE(rh.operador_id, a.operador_id) AS operador_id, "
            "  NULLIF(TRIM(COALESCE(t.nombres,'') || ' ' || COALESCE(t.apellidos,'')), '') AS operador, "
            "  rh.horas_trabajadas, rh.horas_facturables, rh.observaciones, rh.origen_registro, rh.fecha "
            "FROM registros_horas_maquina rh "
            "JOIN maquinas m ON m.id = rh.maquina_id "
            "JOIN obras o ON o.id = rh.obra_id "
            "LEFT JOIN LATERAL ("
            "  SELECT amo.operador_id FROM asignaciones_maquina_obra amo "
            "  WHERE amo.maquina_id = rh.maquina_id AND amo.obra_id = rh.obra_id AND amo.activa "
            "    AND amo.fecha_inicio <= rh.fecha AND (amo.fecha_fin IS NULL OR amo.fecha_fin >= rh.fecha) "
            "  ORDER BY amo.fecha_inicio DESC, amo.id DESC LIMIT 1"
            ") a ON true "
            "LEFT JOIN trabajadores t ON t.id = COALESCE(rh.operador_id, a.operador_id) "
            "WHERE rh.fecha BETWEEN :desde AND :hasta" + extra + " ORDER BY rh.fecha, rh.id"
        )
        filas = [dict(f._mapping) for f in (await self._s.execute(text(sql), params)).all()]
        # Adjunta los turnos de rotación (una 2ª consulta batcheada por los ids del rango — N+1-free);
        # `[]` para los partes legacy. El calendario tipa cada turno como `TurnoDia`.
        turnos = await self.turnos_por_registros([f["id"] for f in filas])
        for f in filas:
            f["turnos"] = turnos.get(f["id"], [])
        return filas

    async def mantenimientos_calendario(
        self, desde: date, hasta: date, *, maquina_id: int | None = None
    ) -> list[dict]:
        """Mantenimientos HECHOS (por `fecha`) en el rango + nombre de máquina. Sin costo."""
        params: dict = {"desde": desde, "hasta": hasta}
        extra = ""
        if maquina_id is not None:
            extra += " AND m.maquina_id = :maquina_id"
            params["maquina_id"] = maquina_id
        sql = (
            "SELECT m.id, m.maquina_id, mq.nombre AS maquina, m.tipo, m.descripcion, "
            "  m.proximo_en_fecha, m.fecha "
            "FROM mantenimientos m JOIN maquinas mq ON mq.id = m.maquina_id "
            "WHERE m.fecha BETWEEN :desde AND :hasta" + extra + " ORDER BY m.fecha, m.id"
        )
        return [dict(f._mapping) for f in (await self._s.execute(text(sql), params)).all()]

    async def proximos_mantenimientos_calendario(
        self, desde: date, hasta: date, *, maquina_id: int | None = None
    ) -> list[dict]:
        """Mantenimientos PRÓXIMOS programados en el rango: la fecha del evento ES `proximo_en_fecha`."""
        params: dict = {"desde": desde, "hasta": hasta}
        extra = ""
        if maquina_id is not None:
            extra += " AND m.maquina_id = :maquina_id"
            params["maquina_id"] = maquina_id
        sql = (
            "SELECT m.maquina_id, mq.nombre AS maquina, m.tipo, m.descripcion, "
            "  m.proximo_en_fecha AS fecha "
            "FROM mantenimientos m JOIN maquinas mq ON mq.id = m.maquina_id "
            "WHERE m.proximo_en_fecha BETWEEN :desde AND :hasta" + extra
            + " ORDER BY m.proximo_en_fecha, m.id"
        )
        return [dict(f._mapping) for f in (await self._s.execute(text(sql), params)).all()]

    async def asignaciones_maquina_calendario(
        self, desde: date, hasta: date, *, maquina_id: int | None = None, obra_id: int | None = None
    ) -> list[dict]:
        """Asignaciones máquina→obra ACTIVAS cuyo rango SOLAPA [desde, hasta] + nombres. Sin precio_hora.

        Solape = `fecha_inicio <= hasta AND (fecha_fin IS NULL OR fecha_fin >= desde)` (NULL = abierto).
        Devuelve rangos (fecha_inicio/fecha_fin); el service los proyecta a cada día que cubren."""
        params: dict = {"desde": desde, "hasta": hasta}
        extra = ""
        if maquina_id is not None:
            extra += " AND amo.maquina_id = :maquina_id"
            params["maquina_id"] = maquina_id
        if obra_id is not None:
            extra += " AND amo.obra_id = :obra_id"
            params["obra_id"] = obra_id
        sql = (
            "SELECT amo.id AS asignacion_id, amo.maquina_id, mq.nombre AS maquina, "
            "  amo.obra_id, o.nombre AS obra, amo.operador_id, "
            "  NULLIF(TRIM(COALESCE(t.nombres,'') || ' ' || COALESCE(t.apellidos,'')), '') AS operador, "
            "  amo.fecha_inicio, amo.fecha_fin "
            "FROM asignaciones_maquina_obra amo "
            "JOIN maquinas mq ON mq.id = amo.maquina_id "
            "JOIN obras o ON o.id = amo.obra_id "
            "LEFT JOIN trabajadores t ON t.id = amo.operador_id "
            "WHERE amo.activa AND amo.fecha_inicio <= :hasta "
            "  AND (amo.fecha_fin IS NULL OR amo.fecha_fin >= :desde)" + extra
            + " ORDER BY amo.fecha_inicio, amo.id"
        )
        return [dict(f._mapping) for f in (await self._s.execute(text(sql), params)).all()]
