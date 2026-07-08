"""Servicio de la cartera de alquiler (Fase 5): dominio sobre el repositorio (sin SQL suelto).

Reusa el ledger de `modules/fiados` (NO duplica el saldo, diseño §1.2): el consumo de horas de máquina
nace como un CARGO en `fiados` vía `FiadosService.crear`, idempotente por
`idempotency_key="alquiler:horas:{registro_horas_id}"`. La fila puente `cargos_alquiler` ancla el
invariante «un registro de horas no genera dos cargos» con su `UNIQUE(registro_horas_id)` —doble guarda
sobre el lock de cliente de fiados—. El cupo solo dispara la ALERTA de excedido (SSE al dueño); NO bloquea
(decisión del dueño, diseño §4.a).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from core.config.timezone import now_co
from core.money import cuantizar
from modules.cartera.errors import CupoInexistente
from modules.cartera.models import CarteraConfig, Cupo
from modules.cartera.repository import SqlCarteraAlquilerRepository
from modules.cartera.schemas import (
    AbonoCarteraLeer,
    CargoObraLeer,
    ColitaLeer,
    CupoActualizar,
    CupoCrear,
    CupoLeer,
    ObraCarteraLeer,
)
from modules.fiados.repository import SqlFiadosRepository
from modules.fiados.service import FiadosService

# Umbral del semáforo verde/amarillo: disponible por encima de este % del cupo = verde; por debajo (aún
# sin exceder) = amarillo; excedido = rojo (diseño §5).
_UMBRAL_AMARILLO = Decimal("0.20")


@dataclass(frozen=True, slots=True)
class ResultadoConsumo:
    """Lo que ve el caller (seam de Fase 3 / bot de Fase 6) al asentar el consumo de horas."""

    fiado_id: int
    monto: Decimal          # cuantizado a MONEY(12,2)
    saldo_obra: Decimal
    cupo_excedido: bool
    replay: bool            # True si el registro ya había generado su cargo


@dataclass(frozen=True, slots=True)
class Colita:
    """Colita estancada detectada por el cron (cliente con saldo, obra cerrada, sin abonar hace mucho)."""

    cliente_id: int
    obra_id: int
    saldo: Decimal
    dias_sin_abono: int
    ultimo_abono_en: datetime | None
    cliente_nombre: str | None = None
    obra_nombre: str | None = None
    # DEDUP del aviso: `obras.ultimo_aviso_colita_en` (NULL = nunca avisada). Lo usa `avisar_colitas` para
    # respetar la cadencia; no lo consume el dashboard.
    ultimo_aviso_colita_en: datetime | None = None


def _semaforo(cupo: Decimal, consumido: Decimal) -> str:
    """Verde/amarillo/rojo según el disponible relativo al cupo. Puro (diseño §5)."""
    if consumido > cupo:
        return "rojo"
    disponible = cupo - consumido
    if cupo > 0 and disponible <= cupo * _UMBRAL_AMARILLO:
        return "amarillo"
    return "verde"


def _cupo_leer(fila: dict, *, colita: bool) -> CupoLeer:
    """Arma la fila de cupo del dashboard desde el dict del repo: deriva `disponible`/`semaforo`."""
    cupo = Decimal(fila["cupo"])
    consumido = Decimal(fila["consumido"])
    return CupoLeer(
        id=fila["id"], cliente_id=fila["cliente_id"], cliente_nombre=fila["cliente_nombre"],
        cupo=cupo, vigente_desde=fila["vigente_desde"], vigente_hasta=fila["vigente_hasta"],
        activo=fila["activo"], notas=fila["notas"],
        consumido=consumido, disponible=cupo - consumido, semaforo=_semaforo(cupo, consumido),
        colita=colita,
    )


class CarteraAlquilerService:
    def __init__(self, repo: SqlCarteraAlquilerRepository, fiados: FiadosService) -> None:
        self._repo = repo
        self._fiados = fiados

    # ---- Consumo (crítico): RegistroHorasMaquina → cargo en el ledger --------
    async def cupo_activo(self, cliente_id: int) -> Cupo | None:
        """Cupo ACTIVO del cliente (None si no tiene). Lo usa el seam de Fase 3 como compuerta."""
        return await self._repo.cupo_activo(cliente_id)

    async def cliente_de_obra(self, obra_id: int) -> int | None:
        """`obras.cliente_id` (el seam resuelve el cliente por la obra, diseño §6.2)."""
        return await self._repo.cliente_de_obra(obra_id)

    async def asentar_consumo_horas(
        self,
        *,
        registro_horas_id: int,
        obra_id: int,
        maquina_id: int,
        asignacion_id: int,
        cliente_id: int,
        horas_facturables: Decimal,
        precio_hora: Decimal,
    ) -> ResultadoConsumo:
        """Asienta el consumo de un parte de horas como CARGO en el ledger de fiados. IDEMPOTENTE por
        `registro_horas_id`. Corre en la sesión/transacción que le pasa Fase 3 (commitean juntos:
        invariante «nada mueve cartera sin registro de horas»).

        IDEMPOTENCIA (invariante del carve-out, doble guarda):
          1. Pre-check `cargo_por_registro`: si el registro ya asentó su cargo → replay, no se asienta nada.
          2. `FiadosService.crear(idempotency_key="alquiler:horas:{id}")`: serializa por cliente y consulta
             la key dentro de la sección crítica (una 2ª llamada devuelve el mismo fiado, replay).
          3. A nivel de base, `UNIQUE(cargos_alquiler.registro_horas_id)` cierra cualquier carrera que
             esquivara el lock (el 2º INSERT viola el UNIQUE).

        Cupo excedido (diseño §4.a): si tras el cargo `saldo_fiado > cupo_activo` → aviso SSE al dueño.
        NO bloquea: el cargo se asienta igual.
        """
        # 1) Idempotencia: ¿ya se asentó el cargo de este registro? → replay (no re-asienta).
        existente = await self._repo.cargo_por_registro(registro_horas_id)
        if existente is not None:
            excedido = await self._cupo_excedido(cliente_id)
            return ResultadoConsumo(
                fiado_id=existente.fiado_id, monto=existente.monto,
                saldo_obra=await self._repo.saldo_obra(obra_id),
                cupo_excedido=excedido, replay=True,
            )

        # 2) Monto: cruza la frontera de precisión 18,4 → 12,2 (cuantizar en el borde, diseño §2/§7).
        monto = cuantizar(horas_facturables * precio_hora)

        # 3) Cargo en el ledger (reusa la función existente, idempotente por key).
        resultado_fiado = await self._fiados.crear(
            cliente_id=cliente_id, venta_id=None, monto=monto,
            idempotency_key=f"alquiler:horas:{registro_horas_id}",
        )
        fiado = resultado_fiado.fiado

        # 4) Traza puente (ancla dura del invariante a nivel de base).
        await self._repo.crear_cargo(
            registro_horas_id=registro_horas_id, fiado_id=fiado.id, obra_id=obra_id,
            maquina_id=maquina_id, asignacion_id=asignacion_id, monto=monto,
        )

        # 5) Chequeo de cupo (NO bloquea): si excede, avisa al dueño por SSE (transaccional).
        cupo = await self._repo.cupo_activo(cliente_id)
        saldo = await self._repo.saldo_cliente(cliente_id)   # ya incluye este cargo (dual-write de fiados)
        excedido = cupo is not None and saldo > cupo.cupo
        if excedido:
            await self._repo.avisar_cupo_excedido(
                cliente_id=cliente_id, obra_id=obra_id, cupo=cupo.cupo, saldo=saldo, generado_en=now_co(),
            )

        return ResultadoConsumo(
            fiado_id=fiado.id, monto=monto, saldo_obra=await self._repo.saldo_obra(obra_id),
            cupo_excedido=excedido, replay=resultado_fiado.replay,
        )

    async def _cupo_excedido(self, cliente_id: int) -> bool:
        cupo = await self._repo.cupo_activo(cliente_id)
        if cupo is None:
            return False
        return await self._repo.saldo_cliente(cliente_id) > cupo.cupo

    # ---- Colitas (cron + semáforo) -------------------------------------------
    async def detectar_colitas(self, *, ahora: datetime, dias_umbral: int) -> list[Colita]:
        """Obras cerradas (FINALIZADA/LIQUIDADA) con saldo estancado sin abono > `dias_umbral` días.

        La atribución de la colita es por (cliente, obra); `dias_sin_abono` se cuenta desde el último
        abono, o —si nunca abonó— desde el primer cargo de la obra."""
        corte = ahora - timedelta(days=dias_umbral)
        colitas: list[Colita] = []
        for fila in await self._repo.colitas(corte=corte):
            ultimo = fila["ultimo_abono_en"]
            ancla = ultimo if ultimo is not None else fila["primer_cargo"]
            dias = (ahora - ancla).days if ancla is not None else dias_umbral
            colitas.append(
                Colita(
                    cliente_id=fila["cliente_id"], obra_id=fila["obra_id"],
                    saldo=Decimal(fila["saldo"]), dias_sin_abono=dias, ultimo_abono_en=ultimo,
                    cliente_nombre=fila.get("cliente_nombre"), obra_nombre=fila.get("obra_nombre"),
                    ultimo_aviso_colita_en=fila.get("ultimo_aviso_colita_en"),
                )
            )
        return colitas

    async def avisar_colitas(self, *, ahora: datetime, dias_umbral: int, cadencia_dias: int) -> int:
        """Corrida del cron: detecta colitas y publica el aviso INTERNO al dueño (SSE) por cada una,
        respetando la CADENCIA de dedup (MEDIUM-1). NO re-avisa una colita cuyo último aviso al dueño fue
        hace menos de `cadencia_dias` (mismo criterio que `PagarService.procesar_avisos` con
        `cartera_config.cadencia_aviso_dias`); sin esto el cron re-avisaba todos los días.

        NO envía nada de cara al cliente: la colita YA está en el ciclo de `pack_cobranza` (el motor barre
        a todo cliente con `saldo_fiado > mínimo`). Aquí solo se avisa al dueño y se alimenta el semáforo
        del dashboard. Sella el dedup (`obras.ultimo_aviso_colita_en`) SOLO de las obras avisadas, en la
        misma transacción del `pg_notify`. Devuelve cuántas colitas se avisaron en esta corrida."""
        colitas = await self.detectar_colitas(ahora=ahora, dias_umbral=dias_umbral)
        cadencia = timedelta(days=cadencia_dias)
        avisadas: list[int] = []
        for c in colitas:
            if (
                c.ultimo_aviso_colita_en is not None
                and ahora - c.ultimo_aviso_colita_en < cadencia
            ):
                continue   # cadencia: ya se avisó de esta colita hace poco (dedup)
            await self._repo.avisar_colita(
                cliente_id=c.cliente_id, obra_id=c.obra_id, saldo=c.saldo,
                dias_sin_abono=c.dias_sin_abono, generado_en=ahora,
            )
            avisadas.append(c.obra_id)
        await self._repo.sellar_avisos_colita(avisadas, cuando=ahora)
        return len(avisadas)

    # ---- Cupos (CRUD dashboard) ----------------------------------------------
    async def listar_cupos(self) -> list[CupoLeer]:
        """Cupos activos con `consumido`/`disponible`/`semáforo` (ledger) + chip `colita` en vivo."""
        clientes_colita = await self._clientes_en_colita()
        return [
            _cupo_leer(fila, colita=fila["cliente_id"] in clientes_colita)
            for fila in await self._repo.listar_cupos_con_consumo()
        ]

    async def cupo_leer(self, cupo_id: int) -> CupoLeer | None:
        """Vista de UN cupo (activo o no) con su consumo/semáforo/chip colita. Para la respuesta de
        alta/edición. None si no existe."""
        fila = await self._repo.cupo_con_consumo(cupo_id)
        if fila is None:
            return None
        clientes_colita = await self._clientes_en_colita()
        return _cupo_leer(fila, colita=fila["cliente_id"] in clientes_colita)

    async def _clientes_en_colita(self) -> set[int]:
        config = await self._repo.obtener_config()
        return {
            c.cliente_id
            for c in await self.detectar_colitas(ahora=now_co(), dias_umbral=config.dias_colita)
        }

    async def crear_cupo(self, datos: CupoCrear) -> Cupo:
        """Alta de cupo (desactiva el activo previo del cliente: un solo cupo activo)."""
        return await self._repo.crear_cupo(
            cliente_id=datos.cliente_id, cupo=datos.cupo, vigente_desde=datos.vigente_desde,
            vigente_hasta=datos.vigente_hasta, notas=datos.notas,
        )

    async def actualizar_cupo(self, cupo_id: int, datos: CupoActualizar) -> Cupo:
        """Edición parcial (solo los campos enviados). 404 si no existe."""
        cupo = await self._repo.obtener_cupo(cupo_id)
        if cupo is None:
            raise CupoInexistente(cupo_id)
        return await self._repo.actualizar_cupo(cupo, datos.model_dump(exclude_unset=True))

    # ---- Vista de obra (liquidación) -----------------------------------------
    async def cartera_de_obra(self, obra_id: int) -> ObraCarteraLeer:
        """Detalle de cartera de la obra: encabezado (nombres), saldo pendiente, cargos y abonos.

        Pocas queries acotadas a la obra (cabecera + cargos + saldo + abonos); cada una resuelve sus
        nombres en el mismo SELECT (sin N+1)."""
        cabecera = await self._repo.obra_cabecera(obra_id) or {}
        cargos = [CargoObraLeer(**fila) for fila in await self._repo.cargos_de_obra(obra_id)]
        abonos = [AbonoCarteraLeer(**fila) for fila in await self._repo.abonos_de_obra(obra_id)]
        return ObraCarteraLeer(
            obra_id=obra_id, cliente_id=cabecera.get("cliente_id") or 0,
            obra_nombre=cabecera.get("obra_nombre"), cliente_nombre=cabecera.get("cliente_nombre"),
            saldo=await self._repo.saldo_obra(obra_id), cargos=cargos, abonos=abonos,
        )

    async def listar_colitas(self) -> list[ColitaLeer]:
        config = await self._repo.obtener_config()
        return [
            ColitaLeer(
                cliente_id=c.cliente_id, obra_id=c.obra_id,
                cliente_nombre=c.cliente_nombre, obra_nombre=c.obra_nombre, saldo=c.saldo,
                dias_sin_abono=c.dias_sin_abono, ultimo_abono_en=c.ultimo_abono_en,
            )
            for c in await self.detectar_colitas(ahora=now_co(), dias_umbral=config.dias_colita)
        ]

    # ---- Config --------------------------------------------------------------
    async def obtener_config(self) -> CarteraConfig:
        return await self._repo.obtener_config()

    async def guardar_config(self, cambios: dict) -> CarteraConfig:
        return await self._repo.guardar_config(cambios)


def construir_cartera_service(session: AsyncSession) -> CarteraAlquilerService:
    """Fábrica: arma el `CarteraAlquilerService` con su `FiadosService` sobre la MISMA sesión del tenant.

    Único punto de cableado (router, seam de maquinaria, cron): fiados y cartera comparten la sesión, así
    el cargo y el registro de horas commitean juntos (invariante «nada mueve cartera sin registro»)."""
    return CarteraAlquilerService(
        SqlCarteraAlquilerRepository(session), FiadosService(SqlFiadosRepository(session))
    )
