"""Motor del pack Agenda/Citas (Capa 2 del doc): determinista, igual para todos los verticales.

Orquesta la lógica pura de `slots.py` con la I/O del repositorio. El servicio NO escribe SQL (regla
no negociable #2) ni calcula tiempo a mano: arma los insumos (config, horarios, ocupaciones), llama
al cálculo puro y persiste vía el repo. Todo en hora Colombia (`COLOMBIA_TZ`, regla #4).

- `calcular_disponibilidad`: cupos libres de los recursos que prestan el servicio.
- `validar_y_agendar`: valida reglas, toma un advisory lock por recurso y revalida el cupo DENTRO de
  la sección crítica antes de insertar (anti doble-reserva). Idempotente por `idempotency_key`.
  Estado según `modo_confirmacion` (auto→confirmada, manual→pendiente).
- `reagendar` / `cancelar`: aplican `politica_cancelacion_horas` y `permite_reagendar`.
"""
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy.exc import IntegrityError

from core.config.timezone import COLOMBIA_TZ, now_co, rango_dia_co, to_co, today_co
from modules.agenda.errors import (
    CitaInexistente,
    CitaNoModificable,
    CupoNoDisponible,
    FueraDePoliticaCancelacion,
    RecursoInexistente,
    RecursoNoPrestaServicio,
    ReagendarNoPermitido,
    ServicioInexistente,
)
from modules.agenda.models import AgendaConfig, Bloqueo, Cita, Disponibilidad, Recurso, Servicio
from modules.agenda.repository import AgendaRepo
from modules.agenda.schemas import (
    AgendaConfigCrear,
    BloqueoCrear,
    CitaCrear,
    DisponibilidadCrear,
    RecursoCrear,
    ServicioCrear,
)
from modules.agenda.slots import (
    HorarioSemanal,
    Intervalo,
    ReglasCupo,
    calcular_slots,
    cupo_disponible,
    expandir_ventanas,
)

# Cuántos cupos alternativos ofrecer cuando el pedido choca, y por cuántos días buscarlos.
_MAX_ALTERNATIVAS = 3
_DIAS_ALTERNATIVAS = 2
_ESTADOS_TERMINALES = ("cumplida", "cancelada", "no_show")


@dataclass(frozen=True, slots=True)
class SlotDisponible:
    """Un cupo libre ofrecible: inicio (hora Colombia) y el recurso que lo prestaría."""

    inicio: datetime
    recurso_id: int


@dataclass(frozen=True, slots=True)
class ResultadoAgendar:
    """Cita agendada. `replay=True` cuando la `idempotency_key` ya existía (no se duplicó)."""

    cita: Cita
    replay: bool


@dataclass(frozen=True, slots=True)
class _Config:
    """Reglas efectivas del negocio (la fila de `agenda_config` o sus defaults si no existe)."""

    reglas: ReglasCupo
    politica_cancelacion_horas: int
    permite_reagendar: bool
    modo_confirmacion: str


class AgendaService:
    def __init__(self, repo: AgendaRepo) -> None:
        self._repo = repo

    # --- disponibilidad ------------------------------------------------------
    async def calcular_disponibilidad(
        self,
        servicio_id: int,
        *,
        desde: date | None = None,
        hasta: date | None = None,
        recurso_id: int | None = None,
    ) -> list[SlotDisponible]:
        """Cupos libres del servicio en [desde, hasta] (Colombia), por uno o todos sus recursos.

        El cálculo (rejilla − citas − bloqueos, reglas de anticipación/ventana/capacidad) vive en
        `slots.py`; aquí solo se reúnen los insumos. Default: hoy → hoy (un día).
        """
        servicio = await self._servicio_activo(servicio_id)
        config = await self._cargar_config()
        desde = desde or today_co()
        hasta = hasta or desde

        recursos = await self._recursos_objetivo(servicio_id, recurso_id)
        ahora = now_co()
        slots: list[SlotDisponible] = []
        for recurso in recursos:
            ventanas = await self._ventanas(recurso.id, desde, hasta)
            if not ventanas:
                continue
            ocupaciones = await self._ocupaciones(recurso.id, ventanas)
            for inicio in calcular_slots(
                ventanas=ventanas,
                ocupaciones=ocupaciones,
                duracion_min=servicio.duracion_min,
                buffer_antes_min=servicio.buffer_antes_min,
                buffer_despues_min=servicio.buffer_despues_min,
                reglas=config.reglas,
                ahora=ahora,
            ):
                slots.append(SlotDisponible(inicio=inicio, recurso_id=recurso.id))
        slots.sort(key=lambda s: (s.inicio, s.recurso_id))
        return slots

    async def listar_servicios(self, *, solo_activos: bool = True) -> list[Servicio]:
        """Servicios del negocio. Por defecto solo activos (el agente solo ofrece activos)."""
        return await self._repo.listar_servicios(solo_activos=solo_activos)

    async def proximas_citas(self, telefono: str) -> list[Cita]:
        """Citas vigentes (pendiente/confirmada) aún no pasadas del cliente, ordenadas por fecha.

        Acotado al teléfono: es la identidad del cliente en WhatsApp (guardarraíl del agente).
        """
        ahora = now_co()
        citas = await self._repo.citas_de_cliente(telefono)
        return [c for c in citas if c.estado in ("pendiente", "confirmada") and c.fin >= ahora]

    # --- agendar -------------------------------------------------------------
    async def validar_y_agendar(
        self,
        *,
        servicio_id: int,
        recurso_id: int,
        inicio: datetime,
        cliente_nombre: str,
        cliente_telefono: str,
        idempotency_key: str | None = None,
        origen: str = "whatsapp",
        notas: str | None = None,
    ) -> ResultadoAgendar:
        """Agenda una cita de forma segura: valida, serializa por recurso y revalida bajo lock.

        Idempotente: si la `idempotency_key` ya existe, devuelve la cita previa (replay) sin duplicar.
        """
        inicio = self._a_colombia(inicio)
        if idempotency_key:
            previa = await self._repo.cita_por_key(idempotency_key)
            if previa is not None:
                return ResultadoAgendar(previa, replay=True)

        servicio = await self._servicio_activo(servicio_id)
        await self._recurso_presta(recurso_id, servicio_id)
        config = await self._cargar_config()

        # Sección crítica: el lock por recurso serializa reservas; la revalidación del cupo y el
        # insert ocurren con el lock tomado, así dos pedidos del mismo cupo no se duplican.
        await self._repo.lock_recurso(recurso_id)
        if idempotency_key:
            previa = await self._repo.cita_por_key(idempotency_key)
            if previa is not None:
                return ResultadoAgendar(previa, replay=True)

        if not await self._cupo_libre(servicio, recurso_id, inicio, config):
            alternativas = await self._alternativas(servicio_id, recurso_id, inicio)
            raise CupoNoDisponible(inicio, alternativas)

        estado = "confirmada" if config.modo_confirmacion == "auto" else "pendiente"
        fin = inicio + timedelta(minutes=servicio.duracion_min)
        datos = CitaCrear(
            servicio_id=servicio_id,
            recurso_id=recurso_id,
            cliente_nombre=cliente_nombre,
            cliente_telefono=cliente_telefono,
            inicio=inicio,
            fin=fin,
            origen=origen,
            notas=notas,
            idempotency_key=idempotency_key,
        )
        try:
            cita = await self._repo.crear_cita(datos, estado=estado, fin=fin)
        except IntegrityError:
            # Carrera con la misma idempotency_key por otro recurso: la unique es el respaldo final.
            if idempotency_key:
                previa = await self._repo.cita_por_key(idempotency_key)
                if previa is not None:
                    return ResultadoAgendar(previa, replay=True)
            raise
        return ResultadoAgendar(cita, replay=False)

    # --- reagendar / cancelar ------------------------------------------------
    async def reagendar(
        self, cita_id: int, nuevo_inicio: datetime, *, telefono: str | None = None
    ) -> Cita:
        """Mueve la cita a `nuevo_inicio` si lo permite la política y el cupo está libre."""
        nuevo_inicio = self._a_colombia(nuevo_inicio)
        cita = await self._cita_modificable(cita_id, telefono)
        config = await self._cargar_config()
        if not config.permite_reagendar:
            raise ReagendarNoPermitido()
        self._exigir_politica(cita.inicio, config.politica_cancelacion_horas)

        servicio = await self._servicio_activo(cita.servicio_id)
        await self._repo.lock_recurso(cita.recurso_id)
        if not await self._cupo_libre(
            servicio, cita.recurso_id, nuevo_inicio, config, excluir_cita_id=cita.id
        ):
            alternativas = await self._alternativas(cita.servicio_id, cita.recurso_id, nuevo_inicio)
            raise CupoNoDisponible(nuevo_inicio, alternativas)

        fin = nuevo_inicio + timedelta(minutes=servicio.duracion_min)
        return await self._repo.reprogramar_cita(cita, inicio=nuevo_inicio, fin=fin)

    async def cancelar(self, cita_id: int, *, telefono: str | None = None) -> Cita:
        """Cancela la cita si la política de cancelación lo permite."""
        cita = await self._cita_modificable(cita_id, telefono)
        config = await self._cargar_config()
        self._exigir_politica(cita.inicio, config.politica_cancelacion_horas)
        return await self._repo.cambiar_estado_cita(cita, "cancelada")

    # --- dashboard: catálogo (servicios / recursos / N:N) --------------------
    async def crear_servicio(self, datos: ServicioCrear) -> Servicio:
        return await self._repo.crear_servicio(datos)

    async def obtener_servicio(self, servicio_id: int) -> Servicio:
        servicio = await self._repo.servicio_por_id(servicio_id)
        if servicio is None:
            raise ServicioInexistente(servicio_id)
        return servicio

    async def actualizar_servicio(self, servicio_id: int, datos: ServicioCrear) -> Servicio:
        servicio = await self.obtener_servicio(servicio_id)
        return await self._repo.actualizar_servicio(servicio, datos)

    async def desactivar_servicio(self, servicio_id: int) -> Servicio:
        servicio = await self.obtener_servicio(servicio_id)
        return await self._repo.desactivar_servicio(servicio)

    async def listar_recursos(self, *, solo_activos: bool = True) -> list[Recurso]:
        return await self._repo.listar_recursos(solo_activos=solo_activos)

    async def crear_recurso(self, datos: RecursoCrear) -> Recurso:
        return await self._repo.crear_recurso(datos)

    async def obtener_recurso(self, recurso_id: int) -> Recurso:
        recurso = await self._repo.recurso_por_id(recurso_id)
        if recurso is None:
            raise RecursoInexistente(recurso_id)
        return recurso

    async def actualizar_recurso(self, recurso_id: int, datos: RecursoCrear) -> Recurso:
        recurso = await self.obtener_recurso(recurso_id)
        return await self._repo.actualizar_recurso(recurso, datos)

    async def desactivar_recurso(self, recurso_id: int) -> Recurso:
        recurso = await self.obtener_recurso(recurso_id)
        return await self._repo.desactivar_recurso(recurso)

    async def asignar_recurso_servicio(self, *, recurso_id: int, servicio_id: int) -> None:
        """Vincula recurso↔servicio (valida que ambos existan)."""
        await self.obtener_servicio(servicio_id)
        await self.obtener_recurso(recurso_id)
        await self._repo.asignar_servicio(recurso_id=recurso_id, servicio_id=servicio_id)

    async def desasignar_recurso_servicio(self, *, recurso_id: int, servicio_id: int) -> None:
        await self._repo.desasignar_servicio(recurso_id=recurso_id, servicio_id=servicio_id)

    async def recursos_de_servicio(self, servicio_id: int) -> list[Recurso]:
        await self.obtener_servicio(servicio_id)
        return await self._repo.recursos_de_servicio(servicio_id, solo_activos=False)

    # --- dashboard: disponibilidad / bloqueos --------------------------------
    async def listar_disponibilidad(self, recurso_id: int) -> list[Disponibilidad]:
        await self.obtener_recurso(recurso_id)
        return await self._repo.disponibilidad_de(recurso_id)

    async def crear_disponibilidad(self, datos: DisponibilidadCrear) -> Disponibilidad:
        await self.obtener_recurso(datos.recurso_id)
        return await self._repo.crear_disponibilidad(datos)

    async def eliminar_disponibilidad(self, disponibilidad_id: int) -> bool:
        return await self._repo.eliminar_disponibilidad(disponibilidad_id)

    async def listar_bloqueos(
        self, *, desde: date | None = None, hasta: date | None = None
    ) -> list[Bloqueo]:
        inicio, fin = rango_dia_co(desde, hasta) if (desde or hasta) else (None, None)
        return await self._repo.listar_bloqueos(desde=inicio, hasta=fin)

    async def crear_bloqueo(self, datos: BloqueoCrear) -> Bloqueo:
        if datos.recurso_id is not None:
            await self.obtener_recurso(datos.recurso_id)
        return await self._repo.crear_bloqueo(datos)

    async def eliminar_bloqueo(self, bloqueo_id: int) -> bool:
        return await self._repo.eliminar_bloqueo(bloqueo_id)

    # --- dashboard: agenda_config (fila única) -------------------------------
    async def obtener_config(self) -> AgendaConfig | None:
        return await self._repo.obtener_config()

    async def guardar_config(self, datos: AgendaConfigCrear) -> AgendaConfig:
        return await self._repo.guardar_config(datos)

    # --- dashboard: citas (lectura + acciones del negocio) -------------------
    async def listar_citas(
        self,
        *,
        desde: date | None = None,
        hasta: date | None = None,
        estado: str | None = None,
        recurso_id: int | None = None,
    ) -> list[Cita]:
        """Citas del rango (hora Colombia), con filtros opcionales. Default: hoy → +30 días."""
        hoy = today_co()
        inicio, fin = rango_dia_co(desde or hoy, hasta or (hoy + timedelta(days=30)))
        return await self._repo.listar_citas(
            inicio=inicio, fin=fin, estado=estado, recurso_id=recurso_id
        )

    async def obtener_cita(self, cita_id: int) -> Cita:
        cita = await self._repo.cita_por_id(cita_id)
        if cita is None:
            raise CitaInexistente(cita_id)
        return cita

    async def confirmar(self, cita_id: int) -> Cita:
        """Confirma una cita pendiente (modo manual). Idempotente si ya está confirmada."""
        cita = await self._cita_modificable(cita_id, None)
        if cita.estado != "pendiente":
            return cita
        return await self._repo.cambiar_estado_cita(cita, "confirmada")

    async def cancelar_negocio(self, cita_id: int) -> Cita:
        """Cancela una cita desde el dashboard. El negocio NO está sujeto a la política de cancelación."""
        cita = await self._cita_modificable(cita_id, None)
        return await self._repo.cambiar_estado_cita(cita, "cancelada")

    async def reagendar_negocio(self, cita_id: int, nuevo_inicio: datetime) -> Cita:
        """Reagenda desde el dashboard: revalida el cupo (con lock) pero sin política ni teléfono."""
        nuevo_inicio = self._a_colombia(nuevo_inicio)
        cita = await self._cita_modificable(cita_id, None)
        servicio = await self._servicio_activo(cita.servicio_id)
        config = await self._cargar_config()
        await self._repo.lock_recurso(cita.recurso_id)
        if not await self._cupo_libre(
            servicio, cita.recurso_id, nuevo_inicio, config, excluir_cita_id=cita.id
        ):
            alternativas = await self._alternativas(cita.servicio_id, cita.recurso_id, nuevo_inicio)
            raise CupoNoDisponible(nuevo_inicio, alternativas)
        fin = nuevo_inicio + timedelta(minutes=servicio.duracion_min)
        return await self._repo.reprogramar_cita(cita, inicio=nuevo_inicio, fin=fin)

    # --- helpers de I/O ------------------------------------------------------
    async def _servicio_activo(self, servicio_id: int) -> Servicio:
        servicio = await self._repo.servicio_por_id(servicio_id)
        if servicio is None or not servicio.activo:
            raise ServicioInexistente(servicio_id)
        return servicio

    async def _recurso_presta(self, recurso_id: int, servicio_id: int) -> None:
        recurso = await self._repo.recurso_por_id(recurso_id)
        if recurso is None or not recurso.activo:
            raise RecursoInexistente(recurso_id)
        if not await self._repo.recurso_presta(recurso_id=recurso_id, servicio_id=servicio_id):
            raise RecursoNoPrestaServicio(recurso_id, servicio_id)

    async def _recursos_objetivo(self, servicio_id: int, recurso_id: int | None) -> list:
        if recurso_id is not None:
            await self._recurso_presta(recurso_id, servicio_id)
            recurso = await self._repo.recurso_por_id(recurso_id)
            return [recurso] if recurso is not None else []
        return await self._repo.recursos_de_servicio(servicio_id)

    async def _ventanas(self, recurso_id: int, desde: date, hasta: date) -> list[Intervalo]:
        horarios = [
            HorarioSemanal(d.dia_semana, d.hora_inicio, d.hora_fin)
            for d in await self._repo.disponibilidad_de(recurso_id)
        ]
        return expandir_ventanas(horarios, desde, hasta)

    async def _ocupaciones(
        self, recurso_id: int, ventanas: list[Intervalo], *, excluir_cita_id: int | None = None
    ) -> list[Intervalo]:
        inicio = min(v.inicio for v in ventanas)
        fin = max(v.fin for v in ventanas)
        return await self._repo.ocupaciones_de_recurso(
            recurso_id=recurso_id, inicio=inicio, fin=fin, excluir_cita_id=excluir_cita_id
        )

    async def _cupo_libre(
        self,
        servicio: Servicio,
        recurso_id: int,
        inicio: datetime,
        config: _Config,
        *,
        excluir_cita_id: int | None = None,
    ) -> bool:
        ventanas = await self._ventanas(recurso_id, inicio.date(), inicio.date())
        if not ventanas:
            return False
        ocupaciones = await self._ocupaciones(
            recurso_id, ventanas, excluir_cita_id=excluir_cita_id
        )
        return cupo_disponible(
            inicio=inicio,
            ventanas=ventanas,
            ocupaciones=ocupaciones,
            duracion_min=servicio.duracion_min,
            buffer_antes_min=servicio.buffer_antes_min,
            buffer_despues_min=servicio.buffer_despues_min,
            reglas=config.reglas,
            ahora=now_co(),
        )

    async def _alternativas(
        self, servicio_id: int, recurso_id: int, inicio: datetime
    ) -> list[datetime]:
        slots = await self.calcular_disponibilidad(
            servicio_id,
            desde=inicio.date(),
            hasta=inicio.date() + timedelta(days=_DIAS_ALTERNATIVAS),
            recurso_id=recurso_id,
        )
        return [s.inicio for s in slots[:_MAX_ALTERNATIVAS]]

    async def _cita_modificable(self, cita_id: int, telefono: str | None) -> Cita:
        cita = await self._repo.cita_por_id(cita_id)
        # Guardarraíl del agente: si se pasa teléfono, solo se toca la cita de ESE cliente (sin
        # filtrar existencia para no revelar citas ajenas).
        if cita is None or (telefono is not None and cita.cliente_telefono != telefono):
            raise CitaInexistente(cita_id)
        if cita.estado in _ESTADOS_TERMINALES:
            raise CitaNoModificable(cita_id, cita.estado)
        return cita

    async def _cargar_config(self) -> _Config:
        cfg = await self._repo.obtener_config()
        if cfg is None:
            return _Config(
                reglas=ReglasCupo(),
                politica_cancelacion_horas=24,
                permite_reagendar=True,
                modo_confirmacion="auto",
            )
        return _Config(
            reglas=ReglasCupo(
                intervalo_slots_min=cfg.intervalo_slots_min,
                anticipacion_minima_min=cfg.anticipacion_minima_min,
                ventana_maxima_dias=cfg.ventana_maxima_dias,
                capacidad_por_slot=cfg.capacidad_por_slot,
            ),
            politica_cancelacion_horas=cfg.politica_cancelacion_horas,
            permite_reagendar=cfg.permite_reagendar,
            modo_confirmacion=cfg.modo_confirmacion,
        )

    @staticmethod
    def _a_colombia(dt: datetime) -> datetime:
        """Normaliza a hora Colombia: lo aware se convierte; lo naive se asume ya en Colombia."""
        if dt.tzinfo is None:
            return dt.replace(tzinfo=COLOMBIA_TZ)
        return to_co(dt)

    @staticmethod
    def _exigir_politica(cita_inicio: datetime, horas: int) -> None:
        if cita_inicio - now_co() < timedelta(hours=horas):
            raise FueraDePoliticaCancelacion(horas)
