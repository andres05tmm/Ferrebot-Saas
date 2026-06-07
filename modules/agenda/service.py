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

from core.config.timezone import COLOMBIA_TZ, now_co, to_co, today_co
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
from modules.agenda.models import Cita, Servicio
from modules.agenda.repository import AgendaRepo
from modules.agenda.schemas import CitaCrear
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

    async def listar_servicios(self) -> list[Servicio]:
        """Servicios activos del negocio (para que el agente los ofrezca)."""
        return await self._repo.listar_servicios(solo_activos=True)

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
