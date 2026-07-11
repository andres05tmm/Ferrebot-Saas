"""Servicio de operación de máquina EN VIVO: iniciar, rotar, finalizar, anular (feature PIM).

Orquesta sesiones/tramos (`SqlOperacionRepository`) sobre la lógica de máquinas ya existente
(`SqlMaquinasRepository`) y, al FINALIZAR, MATERIALIZA la sesión en el parte de horas diario reusando
`MaquinariaService.registrar_horas` — de ahí salen, sin reconstruir nada, la facturación y el seam de
cartera de alquiler (idempotentes). El cronómetro propone las horas por tramo; el supervisor las
confirma (`ajustes`). La sesión del tenant ES la transacción: todo (sesión, tramos, parte, cartera)
commitea junto en el llamador.
"""
from decimal import Decimal

from core.config.timezone import now_co, to_co, today_co
from services.calculations.maquinas import horas_transcurridas

from modules.maquinaria.errors import (
    MaquinaInexistente,
    OperadorInexistente,
    SesionInexistente,
    SesionNoAbierta,
    SesionYaAbierta,
    SinAsignacionActiva,
)
from modules.maquinaria.models import SesionMaquina
from modules.maquinaria.operacion_repository import SqlOperacionRepository
from modules.maquinaria.repository import SqlMaquinasRepository
from modules.maquinaria.schemas import RegistroHorasCrear
from modules.maquinaria.service import MaquinariaService, ResultadoRegistroHoras


class OperacionMaquinaService:
    def __init__(
        self,
        maq_repo: SqlMaquinasRepository,
        op_repo: SqlOperacionRepository,
        cartera=None,
    ) -> None:
        """`cartera` (opcional) se inyecta SOLO en tenants con `cartera_alquiler`; al materializar, el
        seam de cartera se dispara a través del `MaquinariaService` compuesto (misma señal que el POST de
        horas). Sin ella el parte se escribe sin tocar la cartera (comportamiento base)."""
        self._maq_repo = maq_repo
        self._op = op_repo
        self._maquinaria = MaquinariaService(maq_repo, cartera)

    async def iniciar(
        self, maquina_id: int, obra_id: int | None = None, operador_id: int | None = None
    ) -> SesionMaquina:
        """Activa la máquina: abre una sesión ABIERTA (cronómetro) con su primer tramo de operador.

        404 si la máquina no existe; 409 `SesionYaAbierta` si ya está corriendo; 409 `SinAsignacionActiva`
        si no hay asignación activa que la ponga en una obra hoy; 404 `OperadorInexistente` si el operador
        no es un trabajador activo. `obra_id` opcional: se infiere la asignación vigente si es única."""
        maquina = await self._maq_repo.obtener(maquina_id)
        if maquina is None:
            raise MaquinaInexistente(maquina_id)
        if await self._op.sesion_abierta_de_maquina(maquina_id) is not None:
            raise SesionYaAbierta(maquina_id)

        hoy = today_co()
        obra_id = await self._resolver_obra(maquina_id, obra_id, hoy)
        # Lock del ancla (asignación) como en `registrar_horas`: serializa activaciones concurrentes.
        asignacion = await self._maq_repo.asignacion_activa(
            maquina_id, obra_id, hoy, bloquear=True
        )
        if asignacion is None:
            raise SinAsignacionActiva(maquina_id, obra_id, hoy)
        if operador_id is not None and not await self._maq_repo.operador_valido(operador_id):
            raise OperadorInexistente(operador_id)

        ahora = now_co()
        sesion = await self._op.crear_sesion(
            maquina_id=maquina_id,
            obra_id=obra_id,
            asignacion_id=asignacion.id,
            fecha=hoy,
            iniciada_en=ahora,
        )
        await self._op.abrir_tramo(sesion_id=sesion.id, operador_id=operador_id, iniciado_en=ahora)
        return sesion

    async def _resolver_obra(self, maquina_id: int, obra_id: int | None, hoy) -> int:
        """`obra_id` explícito manda; si no viene, se toma la asignación vigente hoy (por el invariante de
        no-solape hay a lo sumo una). Sin ninguna → mismo 409 que registrar horas."""
        if obra_id is not None:
            return obra_id
        vigentes = await self._maq_repo.asignaciones_vigentes_hoy(maquina_id, hoy)
        if vigentes:
            return vigentes[0].obra_id
        raise SinAsignacionActiva(maquina_id, 0, hoy)

    async def rotar(self, sesion_id: int, operador_id: int | None) -> SesionMaquina:
        """Cambia de operador en vivo: cierra el tramo corriente y abre uno nuevo. 404/409 si la sesión no
        existe o no está abierta; 404 si el operador no es válido."""
        sesion = await self._sesion_abierta(sesion_id)
        if operador_id is not None and not await self._maq_repo.operador_valido(operador_id):
            raise OperadorInexistente(operador_id)
        ahora = now_co()
        abierto = await self._op.tramo_abierto(sesion.id)
        if abierto is not None:
            await self._op.cerrar_tramo(abierto, finalizado_en=ahora)
        await self._op.abrir_tramo(sesion_id=sesion.id, operador_id=operador_id, iniciado_en=ahora)
        await self._op.publicar_rotacion(sesion)   # el tablero en vivo refresca el operador actual
        return sesion

    async def detalle(self, sesion_id: int) -> dict:
        """Sesión + sus tramos con horas PROPUESTAS (para el modal de revisión al finalizar).

        Horas propuestas por tramo: lo confirmado si ya existe; si no, lo medido por el reloj
        (`finalizado_en − iniciado_en`, usando `ahora` para el tramo aún corriendo). 404 si no existe."""
        sesion = await self._op.obtener_sesion(sesion_id)
        if sesion is None:
            raise SesionInexistente(sesion_id)
        ahora = now_co()
        tramos = []
        for tr in await self._op.tramos_detalle(sesion_id):
            propuestas = tr["horas_confirmadas"]
            if propuestas is None:
                propuestas = horas_transcurridas(tr["iniciado_en"], tr["finalizado_en"] or ahora)
            tramos.append({**tr, "horas_propuestas": propuestas})
        return {"sesion": sesion, "tramos": tramos}

    async def finalizar(
        self, sesion_id: int, ajustes: dict[int, Decimal] | None = None
    ) -> ResultadoRegistroHoras:
        """Cierra la sesión y la MATERIALIZA en el parte del día. Cada tramo se escribe con
        `registrar_horas` (default de horas = lo medido por el reloj; `ajustes` {tramo_id: horas} lo pisa),
        reusando mínimo facturable, agregación por turnos y seam de cartera.

        IDEMPOTENTE: si la sesión ya estaba FINALIZADA, NO re-materializa — devuelve el resumen del parte
        que generó (replay). Ancla: `sesion.registro_horas_id`. 404/409 si no existe o fue ANULADA."""
        sesion = await self._op.obtener_sesion(sesion_id)
        if sesion is None:
            raise SesionInexistente(sesion_id)
        if sesion.estado == "FINALIZADA":
            return await self._maquinaria.resumen_de_registro(sesion.registro_horas_id)
        if sesion.estado != "ABIERTA":
            raise SesionNoAbierta(sesion_id, sesion.estado)

        ahora = now_co()
        abierto = await self._op.tramo_abierto(sesion.id)
        if abierto is not None:
            await self._op.cerrar_tramo(abierto, finalizado_en=ahora)

        ajustes = ajustes or {}
        resultado: ResultadoRegistroHoras | None = None
        registro_id: int | None = None
        for tramo in await self._op.tramos_de_sesion(sesion.id):
            horas = ajustes.get(tramo.id)
            if horas is None:
                horas = horas_transcurridas(tramo.iniciado_en, tramo.finalizado_en or ahora)
            await self._op.fijar_horas_confirmadas(tramo, horas)
            resultado = await self._maquinaria.registrar_horas(
                sesion.maquina_id,
                RegistroHorasCrear(
                    obra_id=sesion.obra_id,
                    fecha=sesion.fecha,
                    horas_trabajadas=horas,
                    operador_id=tramo.operador_id,
                    hora_inicio=to_co(tramo.iniciado_en).time(),
                    hora_fin=to_co(tramo.finalizado_en).time() if tramo.finalizado_en else None,
                ),
            )
            registro_id = resultado.registro_id

        await self._op.finalizar_sesion(sesion, finalizada_en=ahora, registro_horas_id=registro_id)
        return resultado   # None solo si la sesión no tuviera tramos (imposible: iniciar abre uno)

    async def anular(self, sesion_id: int) -> SesionMaquina:
        """Descarta una sesión ABIERTA (no materializa, no factura). 404/409 si no existe o no está abierta.

        Cierra el tramo corriente antes de anular: una sesión terminal no debe dejar un tramo abierto
        (`finalizado_en` NULL) colgando —el detalle lo mostraría "en curso"—."""
        sesion = await self._sesion_abierta(sesion_id)
        ahora = now_co()
        abierto = await self._op.tramo_abierto(sesion.id)
        if abierto is not None:
            await self._op.cerrar_tramo(abierto, finalizado_en=ahora)
        return await self._op.anular_sesion(sesion, finalizada_en=ahora)

    async def tablero(self) -> list[dict]:
        """Sesiones en curso con máquina/obra/operador actual + inicio (para las tarjetas con cronómetro)."""
        return await self._op.tablero()

    async def _sesion_abierta(self, sesion_id: int) -> SesionMaquina:
        sesion = await self._op.obtener_sesion(sesion_id)
        if sesion is None:
            raise SesionInexistente(sesion_id)
        if sesion.estado != "ABIERTA":
            raise SesionNoAbierta(sesion_id, sesion.estado)
        return sesion


def construir_operacion_service(session, cartera=None) -> OperacionMaquinaService:
    """Arma el servicio sobre la sesión del tenant (el router lo usa; los tests lo llaman directo)."""
    return OperacionMaquinaService(
        SqlMaquinasRepository(session), SqlOperacionRepository(session), cartera
    )
