"""Repositorio del pack Agenda/Citas: único lugar con SQL del módulo (regla no negociable #2).

El motor (`service.py`) consume este puerto y nunca escribe SQL: arma los insumos del cálculo y
delega la persistencia aquí. Toda acción sobre `citas` se serializa por recurso con un *advisory
lock* transaccional (`lock_recurso`) y es idempotente por `idempotency_key`.

`ocupaciones_de_recurso` devuelve ya los intervalos ocupados (citas activas EXPANDIDAS con sus
buffers + bloqueos del recurso o globales): así la lógica pura de `slots.py` solo resta intervalos.
Las citas se guardan con `inicio` = inicio del servicio de cara al cliente y `fin` = `inicio +
duracion_min` (fin real); los buffers son agenda, no se muestran, y se expanden aquí.
"""
from datetime import datetime, timedelta
from typing import Protocol

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from core.config.timezone import COLOMBIA_TZ
from core.events import publish
from modules.agenda.models import (
    AgendaConfig,
    Bloqueo,
    Cita,
    Disponibilidad,
    Recurso,
    RecursoServicio,
    Servicio,
)
from modules.agenda.schemas import (
    AgendaConfigCrear,
    BloqueoCrear,
    CitaCrear,
    DisponibilidadCrear,
    RecursoCrear,
    ServicioCrear,
)
from modules.agenda.slots import Intervalo

# Namespace del advisory lock por recurso (dos enteros: namespace fijo + recurso_id).
_LOCK_NS = 0xA6E0  # "AGENDA"
# Estados de cita que ocupan agenda (los terminales liberan el cupo).
_ESTADOS_ACTIVOS = ("pendiente", "confirmada")


class AgendaRepo(Protocol):
    """Puerto de datos del pack (lo implementa `SqlAgendaRepository`; los tests lo falsean)."""

    # --- config que nutre el negocio ---
    async def listar_servicios(self, *, solo_activos: bool = True) -> list[Servicio]: ...
    async def servicio_por_id(self, servicio_id: int) -> Servicio | None: ...
    async def crear_servicio(self, datos: ServicioCrear) -> Servicio: ...
    async def listar_recursos(self, *, solo_activos: bool = True) -> list[Recurso]: ...
    async def recurso_por_id(self, recurso_id: int) -> Recurso | None: ...
    async def crear_recurso(self, datos: RecursoCrear) -> Recurso: ...
    async def asignar_servicio(self, *, recurso_id: int, servicio_id: int) -> None: ...
    async def recursos_de_servicio(
        self, servicio_id: int, *, solo_activos: bool = True
    ) -> list[Recurso]: ...
    async def recurso_presta(self, *, recurso_id: int, servicio_id: int) -> bool: ...
    async def disponibilidad_de(self, recurso_id: int) -> list[Disponibilidad]: ...
    async def crear_disponibilidad(self, datos: DisponibilidadCrear) -> Disponibilidad: ...
    async def bloqueos_en(
        self, *, inicio: datetime, fin: datetime, recurso_id: int | None = None
    ) -> list[Bloqueo]: ...
    async def crear_bloqueo(self, datos: BloqueoCrear) -> Bloqueo: ...
    async def obtener_config(self) -> AgendaConfig | None: ...
    async def guardar_config(self, datos: AgendaConfigCrear) -> AgendaConfig: ...

    # --- citas (transaccional) ---
    async def ocupaciones_de_recurso(
        self, *, recurso_id: int, inicio: datetime, fin: datetime, excluir_cita_id: int | None = None
    ) -> list[Intervalo]: ...
    async def citas_de_recurso(
        self, *, recurso_id: int, inicio: datetime, fin: datetime
    ) -> list[Cita]: ...
    async def citas_de_cliente(self, telefono: str) -> list[Cita]: ...
    async def cita_por_key(self, idempotency_key: str) -> Cita | None: ...
    async def cita_por_id(self, cita_id: int) -> Cita | None: ...
    async def lock_recurso(self, recurso_id: int) -> None: ...
    async def crear_cita(self, datos: CitaCrear, *, estado: str, fin: datetime) -> Cita: ...
    async def reprogramar_cita(self, cita: Cita, *, inicio: datetime, fin: datetime) -> Cita: ...
    async def cambiar_estado_cita(self, cita: Cita, estado: str) -> Cita: ...
    async def fijar_gcal_event_id(self, cita: Cita, event_id: str | None) -> Cita: ...

    # --- CRUD del dashboard ---
    async def actualizar_servicio(self, servicio: Servicio, datos: ServicioCrear) -> Servicio: ...
    async def actualizar_recurso(self, recurso: Recurso, datos: RecursoCrear) -> Recurso: ...
    async def desactivar_servicio(self, servicio: Servicio) -> Servicio: ...
    async def desactivar_recurso(self, recurso: Recurso) -> Recurso: ...
    async def desasignar_servicio(self, *, recurso_id: int, servicio_id: int) -> None: ...
    async def eliminar_disponibilidad(self, disponibilidad_id: int) -> bool: ...
    async def listar_bloqueos(
        self, *, desde: datetime | None = None, hasta: datetime | None = None
    ) -> list[Bloqueo]: ...
    async def eliminar_bloqueo(self, bloqueo_id: int) -> bool: ...
    async def listar_citas(
        self, *, inicio: datetime, fin: datetime, estado: str | None = None,
        recurso_id: int | None = None,
    ) -> list[Cita]: ...


class SqlAgendaRepository:
    """Implementación SQL del puerto sobre la sesión del tenant (regla de multitenancy #2)."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    # --- servicios -----------------------------------------------------------
    async def listar_servicios(self, *, solo_activos: bool = True) -> list[Servicio]:
        stmt = select(Servicio).order_by(Servicio.nombre)
        if solo_activos:
            stmt = stmt.where(Servicio.activo.is_(True))
        return list((await self._s.execute(stmt)).scalars().all())

    async def servicio_por_id(self, servicio_id: int) -> Servicio | None:
        return await self._s.get(Servicio, servicio_id)

    async def crear_servicio(self, datos: ServicioCrear) -> Servicio:
        servicio = Servicio(**datos.model_dump())
        self._s.add(servicio)
        await self._s.flush()
        return servicio

    # --- recursos ------------------------------------------------------------
    async def listar_recursos(self, *, solo_activos: bool = True) -> list[Recurso]:
        stmt = select(Recurso).order_by(Recurso.nombre)
        if solo_activos:
            stmt = stmt.where(Recurso.activo.is_(True))
        return list((await self._s.execute(stmt)).scalars().all())

    async def recurso_por_id(self, recurso_id: int) -> Recurso | None:
        return await self._s.get(Recurso, recurso_id)

    async def crear_recurso(self, datos: RecursoCrear) -> Recurso:
        recurso = Recurso(**datos.model_dump())
        self._s.add(recurso)
        await self._s.flush()
        return recurso

    async def asignar_servicio(self, *, recurso_id: int, servicio_id: int) -> None:
        """Vincula recurso↔servicio (N:N). Idempotente: ignora el duplicado de PK."""
        await self._s.execute(
            text(
                "INSERT INTO recurso_servicio (recurso_id, servicio_id) VALUES (:r, :s) "
                "ON CONFLICT DO NOTHING"
            ),
            {"r": recurso_id, "s": servicio_id},
        )

    async def recursos_de_servicio(
        self, servicio_id: int, *, solo_activos: bool = True
    ) -> list[Recurso]:
        stmt = (
            select(Recurso)
            .join(RecursoServicio, RecursoServicio.recurso_id == Recurso.id)
            .where(RecursoServicio.servicio_id == servicio_id)
            .order_by(Recurso.nombre)
        )
        if solo_activos:
            stmt = stmt.where(Recurso.activo.is_(True))
        return list((await self._s.execute(stmt)).scalars().all())

    async def recurso_presta(self, *, recurso_id: int, servicio_id: int) -> bool:
        existe = (
            await self._s.execute(
                select(RecursoServicio.recurso_id).where(
                    RecursoServicio.recurso_id == recurso_id,
                    RecursoServicio.servicio_id == servicio_id,
                )
            )
        ).scalar_one_or_none()
        return existe is not None

    # --- disponibilidad / bloqueos -------------------------------------------
    async def disponibilidad_de(self, recurso_id: int) -> list[Disponibilidad]:
        stmt = (
            select(Disponibilidad)
            .where(Disponibilidad.recurso_id == recurso_id)
            .order_by(Disponibilidad.dia_semana, Disponibilidad.hora_inicio)
        )
        return list((await self._s.execute(stmt)).scalars().all())

    async def crear_disponibilidad(self, datos: DisponibilidadCrear) -> Disponibilidad:
        fila = Disponibilidad(**datos.model_dump())
        self._s.add(fila)
        await self._s.flush()
        return fila

    async def bloqueos_en(
        self, *, inicio: datetime, fin: datetime, recurso_id: int | None = None
    ) -> list[Bloqueo]:
        """Bloqueos que solapan [inicio, fin): los del recurso y los globales (`recurso_id` NULL)."""
        stmt = select(Bloqueo).where(Bloqueo.inicio < fin, Bloqueo.fin > inicio)
        if recurso_id is not None:
            stmt = stmt.where(
                (Bloqueo.recurso_id == recurso_id) | (Bloqueo.recurso_id.is_(None))
            )
        return list((await self._s.execute(stmt)).scalars().all())

    async def crear_bloqueo(self, datos: BloqueoCrear) -> Bloqueo:
        bloqueo = Bloqueo(**datos.model_dump())
        self._s.add(bloqueo)
        await self._s.flush()
        return bloqueo

    # --- agenda_config (fila única id=1) -------------------------------------
    async def obtener_config(self) -> AgendaConfig | None:
        return await self._s.get(AgendaConfig, 1)

    async def guardar_config(self, datos: AgendaConfigCrear) -> AgendaConfig:
        """Upsert de la fila única de reglas (id=1): la crea o actualiza sus campos.

        `actualizado_en` se sella con un datetime concreto en hora Colombia (no `func.now()`): una
        expresión SQL diferida dejaría el atributo expirado tras el flush y leerlo al serializar la
        respuesta dispararía un lazy-load async fuera del greenlet (ResponseValidationError /
        MissingGreenlet). El `refresh` final concreta además los server_default (p. ej. `creado_en` al
        crear), para que la respuesta no toque la BD durante la serialización.
        """
        cfg = await self._s.get(AgendaConfig, 1)
        valores = datos.model_dump()
        if cfg is None:
            cfg = AgendaConfig(id=1, **valores)
            self._s.add(cfg)
        else:
            for campo, valor in valores.items():
                setattr(cfg, campo, valor)
            cfg.actualizado_en = datetime.now(COLOMBIA_TZ)
        await self._s.flush()
        await self._s.refresh(cfg)
        return cfg

    # --- citas ---------------------------------------------------------------
    async def ocupaciones_de_recurso(
        self, *, recurso_id: int, inicio: datetime, fin: datetime, excluir_cita_id: int | None = None
    ) -> list[Intervalo]:
        """Intervalos ocupados del recurso en [inicio, fin): citas activas (con buffers) + bloqueos.

        Cada cita activa se expande a [cita.inicio − buffer_antes, cita.fin + buffer_despues]; así la
        lógica pura solo necesita restar intervalos, sin saber de buffers de citas ajenas.
        `excluir_cita_id` omite una cita (al reagendarla no debe chocar consigo misma).
        """
        stmt = (
            select(
                Cita.inicio, Cita.fin,
                Servicio.buffer_antes_min, Servicio.buffer_despues_min,
            )
            .join(Servicio, Servicio.id == Cita.servicio_id)
            .where(
                Cita.recurso_id == recurso_id,
                Cita.estado.in_(_ESTADOS_ACTIVOS),
                Cita.inicio < fin,
                Cita.fin > inicio,
            )
        )
        if excluir_cita_id is not None:
            stmt = stmt.where(Cita.id != excluir_cita_id)
        filas = (await self._s.execute(stmt)).all()
        ocupaciones = [
            Intervalo(
                f.inicio - timedelta(minutes=f.buffer_antes_min),
                f.fin + timedelta(minutes=f.buffer_despues_min),
            )
            for f in filas
        ]
        bloqueos = await self.bloqueos_en(inicio=inicio, fin=fin, recurso_id=recurso_id)
        ocupaciones.extend(Intervalo(b.inicio, b.fin) for b in bloqueos)
        return ocupaciones

    async def citas_de_recurso(
        self, *, recurso_id: int, inicio: datetime, fin: datetime
    ) -> list[Cita]:
        stmt = (
            select(Cita)
            .where(
                Cita.recurso_id == recurso_id,
                Cita.estado.in_(_ESTADOS_ACTIVOS),
                Cita.inicio < fin,
                Cita.fin > inicio,
            )
            .order_by(Cita.inicio)
        )
        return list((await self._s.execute(stmt)).scalars().all())

    async def citas_de_cliente(self, telefono: str) -> list[Cita]:
        """Citas del cliente (por su teléfono = identidad de WhatsApp), más próximas primero."""
        stmt = (
            select(Cita).where(Cita.cliente_telefono == telefono).order_by(Cita.inicio)
        )
        return list((await self._s.execute(stmt)).scalars().all())

    async def cita_por_key(self, idempotency_key: str) -> Cita | None:
        return (
            await self._s.execute(
                select(Cita).where(Cita.idempotency_key == idempotency_key)
            )
        ).scalar_one_or_none()

    async def cita_por_id(self, cita_id: int) -> Cita | None:
        return await self._s.get(Cita, cita_id)

    async def lock_recurso(self, recurso_id: int) -> None:
        """Toma el lock transaccional del recurso: serializa los agendamientos de ese recurso.

        `pg_advisory_xact_lock` se libera solo al COMMIT/ROLLBACK; dos reservas del mismo recurso se
        encolan, y la segunda revalida el cupo (ya ocupado) → no hay doble-reserva.
        """
        await self._s.execute(
            text("SELECT pg_advisory_xact_lock(:ns, :rid)"),
            {"ns": _LOCK_NS, "rid": int(recurso_id)},
        )

    async def crear_cita(self, datos: CitaCrear, *, estado: str, fin: datetime) -> Cita:
        cita = Cita(
            servicio_id=datos.servicio_id,
            recurso_id=datos.recurso_id,
            cliente_nombre=datos.cliente_nombre,
            cliente_telefono=datos.cliente_telefono,
            inicio=datos.inicio,
            fin=fin,
            estado=estado,
            origen=datos.origen,
            notas=datos.notas,
            idempotency_key=datos.idempotency_key,
        )
        self._s.add(cita)
        await self._s.flush()
        await publish(self._s, "cita_agendada", {
            "cita_id": cita.id, "recurso_id": cita.recurso_id, "inicio": cita.inicio,
            "estado": cita.estado,
        })
        return cita

    async def reprogramar_cita(self, cita: Cita, *, inicio: datetime, fin: datetime) -> Cita:
        cita.inicio = inicio
        cita.fin = fin
        await self._s.flush()
        await publish(self._s, "cita_reagendada", {
            "cita_id": cita.id, "recurso_id": cita.recurso_id, "inicio": cita.inicio,
        })
        return cita

    async def cambiar_estado_cita(self, cita: Cita, estado: str) -> Cita:
        cita.estado = estado
        await self._s.flush()
        await publish(self._s, "cita_estado", {"cita_id": cita.id, "estado": estado})
        return cita

    async def fijar_gcal_event_id(self, cita: Cita, event_id: str | None) -> Cita:
        """Guarda (o limpia, con None) el id del evento espejo de Google. Sin evento SSE: detalle interno."""
        cita.gcal_event_id = event_id
        await self._s.flush()
        return cita

    # --- CRUD del dashboard (catálogo/config) --------------------------------
    async def actualizar_servicio(self, servicio: Servicio, datos: ServicioCrear) -> Servicio:
        for campo, valor in datos.model_dump().items():
            setattr(servicio, campo, valor)
        await self._s.flush()
        return servicio

    async def actualizar_recurso(self, recurso: Recurso, datos: RecursoCrear) -> Recurso:
        for campo, valor in datos.model_dump().items():
            setattr(recurso, campo, valor)
        await self._s.flush()
        return recurso

    async def desactivar_servicio(self, servicio: Servicio) -> Servicio:
        """Soft-delete: `activo=False` (no se borra; las citas lo siguen referenciando)."""
        servicio.activo = False
        await self._s.flush()
        return servicio

    async def desactivar_recurso(self, recurso: Recurso) -> Recurso:
        recurso.activo = False
        await self._s.flush()
        return recurso

    async def desasignar_servicio(self, *, recurso_id: int, servicio_id: int) -> None:
        await self._s.execute(
            text("DELETE FROM recurso_servicio WHERE recurso_id = :r AND servicio_id = :s"),
            {"r": recurso_id, "s": servicio_id},
        )

    async def eliminar_disponibilidad(self, disponibilidad_id: int) -> bool:
        fila = await self._s.get(Disponibilidad, disponibilidad_id)
        if fila is None:
            return False
        await self._s.delete(fila)
        await self._s.flush()
        return True

    async def listar_bloqueos(
        self, *, desde: datetime | None = None, hasta: datetime | None = None
    ) -> list[Bloqueo]:
        stmt = select(Bloqueo).order_by(Bloqueo.inicio)
        if hasta is not None:
            stmt = stmt.where(Bloqueo.inicio < hasta)
        if desde is not None:
            stmt = stmt.where(Bloqueo.fin > desde)
        return list((await self._s.execute(stmt)).scalars().all())

    async def eliminar_bloqueo(self, bloqueo_id: int) -> bool:
        fila = await self._s.get(Bloqueo, bloqueo_id)
        if fila is None:
            return False
        await self._s.delete(fila)
        await self._s.flush()
        return True

    async def listar_citas(
        self,
        *,
        inicio: datetime,
        fin: datetime,
        estado: str | None = None,
        recurso_id: int | None = None,
    ) -> list[Cita]:
        """Citas cuyo `inicio` cae en [inicio, fin], con filtros opcionales de estado y recurso."""
        stmt = select(Cita).where(Cita.inicio >= inicio, Cita.inicio <= fin).order_by(Cita.inicio)
        if estado is not None:
            stmt = stmt.where(Cita.estado == estado)
        if recurso_id is not None:
            stmt = stmt.where(Cita.recurso_id == recurso_id)
        return list((await self._s.execute(stmt)).scalars().all())
