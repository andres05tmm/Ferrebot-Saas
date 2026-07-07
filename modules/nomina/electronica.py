"""Nómina electrónica: transmisión del CUNE de cada trabajador DIRECTO a DIAN vía MATIAS (Fase 7 PIM).

Espeja el operativo de la facturación electrónica (FE) SIN reescribirlo: reutiliza la política de
estado/reintento (`facturacion.politica.decidir_emision` + `MAX_INTENTOS`), el tope de intentos y la
clasificación de 5xx del `MatiasClient`. La diferencia es el documento (nómina, clave CUNE) y dónde se
persiste (columnas de `detalles_liquidacion`, no `facturas_electronicas`).

Reglas de la spec 08 que este pipeline hace cumplir:
  - Solo se transmite el trabajador DIRECTO. El PATACALIENTE NO genera documento de nómina electrónica
    ("not formal employees"): queda PENDIENTE pero el barrido lo EXCLUYE por `tipo_vinculacion='DIRECTO'`.
  - Se transmite tras CERRAR el periodo (LIQUIDADO/PAGADO); un periodo ABIERTO se rechaza.

IDEMPOTENCIA (invariante crítico, test-primero): un detalle ya TRANSMITIDO (o con `cune_dian` set) NO se
re-transmite —`directos_transmitibles` lo excluye—; re-disparar el periodo solo reprocesa PENDIENTE/ERROR.
Así reintentar jamás genera un segundo CUNE ni una segunda llamada efectiva a MATIAS. El histórico fiscal
(CUNE + respuesta MATIAS) no se borra.

REGLA DE ORO FISCAL: la transmisión REAL está GO-LIVE GATED (habilitación DIAN + certificado + resolución
+ cuenta MATIAS real de nómina de PIM, que consigue el owner). Hasta entonces MATIAS_AMBIENTE=pruebas y el
`MatiasClient` se MOCKEA en todos los tests: el entregable es el PIPELINE probado contra mock, no un golpe
a DIAN real. El payload y el endpoint MATIAS de nómina quedan [VERIFICAR] (ver `matias_client`).

El job ARQ `transmitir_nomina` vive aquí (concern cohesivo); el INTEGRADOR lo registra en
`WorkerSettings.functions` y cablea el seam `ctx["crear_servicio"]` para que el adaptador por empresa
exponga `.transmitir_nomina(periodo_id)` (ver el docstring del job).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from core.config.timezone import now_co
from core.logging import get_logger
from modules.facturacion.matias_client import TransmisionNominaResultado
from modules.facturacion.politica import decidir_emision
from modules.facturacion.service import MAX_INTENTOS
from modules.nomina.errors import PeriodoBloqueado, PeriodoNominaInexistente
from modules.nomina.models import DetalleLiquidacion, PeriodoNomina
from modules.trabajadores.models import Trabajador

log = get_logger("nomina.electronica")

# Estados en que un periodo YA está cerrado y por tanto es transmisible (spec 08: cerrar → transmitir).
_ESTADOS_TRANSMISIBLES = frozenset({"LIQUIDADO", "PAGADO"})

# Mapea el desenlace de la política (vocabulario FE: aceptada/rechazada/error) al enum de columna
# `estado_transmision` de `detalles_liquidacion`. Reusar `decidir_emision` sin traducir es a propósito:
# una sola máquina de estados para FE y nómina.
_ESTADO_POR_DECISION = {"aceptada": "TRANSMITIDO", "rechazada": "RECHAZADO", "error": "ERROR"}


@dataclass(frozen=True, slots=True)
class ResumenTransmision:
    """Desenlace de transmitir un periodo, agregado para el job ARQ (paridad con la `Decision` de la FE).

    `reintentar`/`dead_letter` son la AGREGACIÓN de las decisiones por detalle: `reintentar` = algún
    detalle transitorio (ERROR) aún dentro del tope → el job re-encola con backoff (idempotente: al
    reintentar solo reprocesa PENDIENTE/ERROR); `dead_letter` = sin nada por reintentar pero algún detalle
    agotó el tope. `transmitidos`/`rechazados`/`errores` son conteos para el log/observabilidad.
    """

    periodo_id: int
    transmitidos: int = 0
    rechazados: int = 0
    errores: int = 0
    reintentar: bool = False
    dead_letter: bool = False


class NominaElectronicaRepo(Protocol):
    """Puerto de datos que consume el servicio (lo implementa `SqlNominaRepository`; los tests lo falsean)."""

    async def obtener_periodo(self, periodo_id: int) -> PeriodoNomina | None: ...
    async def directos_transmitibles(self, periodo_id: int) -> list[DetalleLiquidacion]: ...
    async def trabajadores_map(self, ids: list[int]) -> dict[int, Trabajador]: ...
    async def marcar_transmision(
        self, detalle_id: int, *, estado: str, intentos: int, ahora: datetime,
        cune: str | None = None, fecha_transmision: datetime | None = None, raw: dict | None = None,
    ) -> None: ...


class _MatiasNomina(Protocol):
    """Lo mínimo que el servicio necesita del `MatiasClient` (facilita el doble en tests)."""

    async def transmitir_nomina(self, payload: dict) -> TransmisionNominaResultado: ...


class _ConfigNomina(Protocol):
    """Config fiscal de la empresa (se reutiliza `facturacion.service.ConfigFiscal`): solo el ambiente."""

    ambiente: str


def construir_payload_nomina(
    periodo: PeriodoNomina, detalle: DetalleLiquidacion, trabajador: Trabajador | None,
    config: _ConfigNomina,
) -> dict:
    """Arma el payload de nómina electrónica de UN trabajador para MATIAS. PURO (sin red/BD).

    Toma los montos YA cuantizados por el motor (`services.calculations.nomina`, una fórmula una verdad):
    no recalcula dinero. El dinero viaja como string con la escala del `Decimal` MONEY4 (DIAN espera
    cadenas numéricas, no floats).

    [VERIFICAR con MATIAS real en go-live]: el shape EXACTO del documento de nómina electrónica de MATIAS
    NO está documentado en `docs/facturacion-matias-extract.md` (solo FE/POS/notas). Esta es una interfaz
    RAZONABLE espejo del payload de factura (identificación del trabajador + periodo + devengados +
    deducciones); se ajusta al wire real cuando el owner habilite la cuenta MATIAS de nómina de PIM.
    NUNCA se loguea (documento + salario son datos personales)."""
    # DIAN: 1=producción, 2=pruebas/habilitación. `config.ambiente` es la fuente de verdad del ambiente
    # (default seguro 'pruebas'); mientras no haya go-live real, siempre 2. [VERIFICAR el código exacto].
    ambiente_dian = 1 if config.ambiente == "produccion" else 2
    return {
        "ambiente": ambiente_dian,
        "periodo": {
            "tipo": periodo.tipo,
            "fecha_inicio": periodo.fecha_inicio.isoformat(),
            "fecha_fin": periodo.fecha_fin.isoformat(),
        },
        "trabajador": {
            "tipo_documento": (trabajador.tipo_documento if trabajador else "CC"),
            "numero_documento": (trabajador.documento if trabajador else ""),
            "primer_apellido": (trabajador.apellidos if trabajador else ""),
            "nombres": (trabajador.nombres if trabajador else ""),
            "tipo_contrato": "DIRECTO",
            "salario": str(trabajador.salario_base) if (trabajador and trabajador.salario_base is not None) else "0",
            "dias_liquidados": str(detalle.dias_liquidados),
        },
        "devengados": {
            "salario": str(detalle.salario_devengado),
            "auxilio_transporte": str(detalle.auxilio_transporte),
            "horas_extra": str(detalle.valor_horas_extra),
            "total": str(detalle.total_devengado),
        },
        "deducciones": {
            "salud": str(detalle.salud_empleado),
            "pension": str(detalle.pension_empleado),
            "total": str(detalle.total_deducciones),
        },
        "neto_pagar": str(detalle.neto_pagar),
    }


class NominaElectronicaService:
    """Transmite el CUNE de cada DIRECTO de un periodo CERRADO a DIAN (vía MATIAS), idempotente.

    La fuente única del estado/reintento es `politica.decidir_emision` (la misma de la FE): el `try`
    envuelve SOLO la llamada a MATIAS (`_llamar_matias`); un fallo de transporte se trata como error
    transitorio, no propaga. La sesión del tenant ES la transacción (el repo solo hace flush; el commit
    es del llamador/worker).
    """

    def __init__(
        self, repo: NominaElectronicaRepo, matias: _MatiasNomina, config: _ConfigNomina,
        *, max_intentos: int = MAX_INTENTOS,
    ) -> None:
        self._repo = repo
        self._matias = matias
        self._config = config
        self._max_intentos = max_intentos

    async def transmitir_periodo(self, periodo_id: int) -> ResumenTransmision:
        """Transmite cada trabajador DIRECTO transmitible del periodo y persiste su desenlace.

        Idempotente: `directos_transmitibles` ya excluye lo TRANSMITIDO/RECHAZADO, y en el bucle un
        detalle con `cune_dian` set se salta (defensa en profundidad). Reintentar el periodo NO produce
        un segundo CUNE. Lanza `PeriodoNominaInexistente` (404) o `PeriodoBloqueado` (409, periodo abierto).
        """
        periodo = await self._repo.obtener_periodo(periodo_id)
        if periodo is None:
            raise PeriodoNominaInexistente(periodo_id)
        if periodo.estado not in _ESTADOS_TRANSMISIBLES:
            raise PeriodoBloqueado(
                f"periodo {periodo_id} está {periodo.estado}: ciérralo (LIQUIDADO) antes de transmitir a DIAN"
            )

        detalles = await self._repo.directos_transmitibles(periodo_id)
        if not detalles:
            # Nada por transmitir (ya todo TRANSMITIDO/RECHAZADO, o sin directos): replay limpio.
            return ResumenTransmision(periodo_id=periodo_id)

        trabajadores = await self._repo.trabajadores_map([d.trabajador_id for d in detalles])
        ahora = now_co()
        transmitidos = rechazados = errores = 0
        reintentar = dead_letter = False
        for d in detalles:
            if d.cune_dian:
                continue  # ya transmitido (defensivo): no se re-transmite, no se genera un segundo CUNE
            payload = construir_payload_nomina(periodo, d, trabajadores.get(d.trabajador_id), self._config)
            res = await self._llamar_matias(payload)
            intentos = d.intentos_transmision + 1
            decision = decidir_emision(res.categoria, intentos=intentos, max_intentos=self._max_intentos)
            estado = _ESTADO_POR_DECISION[decision.estado]
            await self._repo.marcar_transmision(
                d.id, estado=estado, intentos=intentos, ahora=ahora,
                cune=res.cune if decision.estado == "aceptada" else None,
                fecha_transmision=ahora if decision.estado == "aceptada" else None,
                raw=res.raw if res.raw is not None else ({"error": res.error_msg} if res.error_msg else None),
            )
            if decision.estado == "aceptada":
                transmitidos += 1
            elif decision.estado == "rechazada":
                rechazados += 1
            else:
                errores += 1
                reintentar = reintentar or decision.reintentar
                dead_letter = dead_letter or decision.dead_letter
        log.info(
            "nomina_periodo_transmitido", periodo_id=periodo_id,
            transmitidos=transmitidos, rechazados=rechazados, errores=errores,
        )
        return ResumenTransmision(
            periodo_id=periodo_id, transmitidos=transmitidos, rechazados=rechazados, errores=errores,
            reintentar=reintentar, dead_letter=dead_letter and not reintentar,
        )

    async def _llamar_matias(self, payload: dict) -> TransmisionNominaResultado:
        """Envuelve la ÚNICA llamada de red a MATIAS: un fallo de transporte/timeout es transitorio
        ('error'), la política decide el reintento, no propaga. NO loguea el payload (datos personales)."""
        try:
            return await self._matias.transmitir_nomina(payload)
        except Exception:  # noqa: BLE001 — transporte/timeout: transitorio, la política reintenta
            log.warning("transmitir_nomina_fallo_transporte", exc_info=True)
            return TransmisionNominaResultado(False, categoria="error", error_msg="fallo de transporte")


# --- job ARQ (el INTEGRADOR lo registra en WorkerSettings.functions) ---------

def _backoff(job_try: int, *, base: int = 30, tope: int = 3600) -> int:
    """Backoff exponencial acotado `min(base * 2**(job_try-1), tope)` segundos. PURO.

    Espejo del `_backoff` de `apps.worker.jobs` (la nómina reusa la misma cadencia de reintento que la
    emisión FE). Duplicado a propósito para no atar `modules/` a `apps/` (capa)."""
    return min(base * 2 ** (job_try - 1), tope)


async def transmitir_nomina(ctx: dict, tenant_id: int, periodo_id: int) -> str:
    """Job ARQ: transmite la nómina electrónica de un periodo, traduciendo `ResumenTransmision` a la
    semántica del worker (paridad con `emitir_documento`). Nunca propaga otra excepción.

    Seam (lo cablea `on_startup`, IGUAL que la emisión FE): `ctx["crear_servicio"](tenant_id)` devuelve el
    adaptador por empresa, que debe exponer `.transmitir_nomina(periodo_id) -> ResumenTransmision`. El
    INTEGRADOR agrega ese método a `_ServicioEmision` (reusa `_componer`: tenant + `MatiasClient` cacheado
    + `ConfigFiscal`) abriendo la sesión del tenant y corriendo
    `NominaElectronicaService(SqlNominaRepository(s), cliente, config).transmitir_periodo(periodo_id)`.

    `reintentar` → `Retry` con backoff (idempotente: al reintentar solo reprocesa PENDIENTE/ERROR);
    `dead_letter` → log + "dead_letter"; si no → "transmitido".
    """
    from arq import Retry

    servicio = await ctx["crear_servicio"](tenant_id)
    resumen = await servicio.transmitir_nomina(periodo_id)
    if resumen.reintentar:
        raise Retry(defer=_backoff(ctx.get("job_try", 1)))
    if resumen.dead_letter:
        log.warning("transmision_nomina_dead_letter", tenant_id=tenant_id, periodo_id=periodo_id)
        return "dead_letter"
    return "transmitido"
