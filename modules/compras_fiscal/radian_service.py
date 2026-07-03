"""Servicio RADIAN: eventos DIAN sobre facturas recibidas de proveedor (Slice 6b).

⚠️ ACCIONES DIAN REALES. Orquesta el cliente MATIAS por-empresa (inyectado, perezoso) y refleja el
desenlace en `compras_fiscal` (cufe + fechas de eventos 030-033 + estado + error). CUFE MANUAL (se
captura a mano; sin Gmail). Contrato MATIAS en `docs/facturacion-matias-extract.md` §14:
`import-track-id` registra la FE recibida y `events/send/{cufe}` envía el evento (030 acuse, 031
reclamo, 032 recibo, 033 aceptación; aceptar = 032+033).

Manejo de errores (innegociable): un fallo de MATIAS **persiste** `evento_error` y se devuelve como
resultado `ok=False` (NO se lanza, para no hacer rollback del error guardado); el router lo mapea a 502.
La fecha de cada evento usa hora Colombia (regla #4). El `ambiente` se inyecta solo para trazabilidad.
"""
from typing import Protocol

from core.config.timezone import now_co
from core.logging import get_logger
from modules.compras_fiscal.errors import (
    CompraFiscalInexistente,
    CufeNoImportado,
    EventoRadianYaResuelto,
)
from modules.compras_fiscal.repository import SqlComprasFiscalRepository
from modules.compras_fiscal.schemas import CompraFiscalLeer

log = get_logger("compras_fiscal.radian")


class RadianMatias(Protocol):
    """Puerto del cliente MATIAS para RADIAN (lo cumple `MatiasClient`; los tests lo falsean)."""

    async def importar_track_id(self, cufe: str): ...
    async def enviar_evento(self, cufe: str, code: str, notes: str = ""): ...


class RadianService:
    """Envía eventos RADIAN sobre una compra fiscal y refleja el desenlace en la fila (sin SQL aquí)."""

    def __init__(
        self, repo: SqlComprasFiscalRepository, matias: RadianMatias, *, ambiente: str = "pruebas"
    ) -> None:
        self._repo = repo
        self._matias = matias
        self._ambiente = ambiente

    async def importar(self, fiscal_id: int, cufe: str) -> tuple[CompraFiscalLeer, bool]:
        """Importa la FE recibida (import-track-id) y envía el acuse 030. Idempotente por 030.

        Devuelve `(fiscal, ok)`. Si ya tiene 030 no re-acusa (ok=True, sin tocar MATIAS). 404 si la
        compra fiscal no existe. Un fallo de MATIAS persiste `evento_error` y devuelve ok=False.
        """
        fiscal = await self._repo.obtener(fiscal_id)
        if fiscal is None:
            raise CompraFiscalInexistente(fiscal_id)
        if fiscal.evento_030_at is not None:
            return fiscal, True   # idempotente: ya se acusó recibo de esta factura
        try:
            r1 = await self._matias.importar_track_id(cufe)
            if not r1.ok:
                return await self._fallo(fiscal_id, r1.error_msg), False
            r2 = await self._matias.enviar_evento(cufe, "030", "Acuse de recibo")
            if not r2.ok:
                return await self._fallo(fiscal_id, r2.error_msg), False
        except Exception:  # noqa: BLE001 — transporte/timeout: se guarda y se reporta 502, no propaga
            log.warning("radian_importar_fallo_transporte", fiscal_id=fiscal_id, exc_info=True)
            return await self._fallo(fiscal_id, "fallo de transporte MATIAS"), False
        actualizada = await self._repo.set_radian(
            fiscal_id, cufe_proveedor=cufe, evento_030_at=now_co(),
            evento_estado="pendiente", evento_error=None,
        )
        return actualizada, True

    async def aceptar(self, fiscal_id: int) -> tuple[CompraFiscalLeer, bool]:
        """Acepta la factura: envía 032 (recibo) y 033 (aceptación expresa) → estado 'aceptada'.

        Requiere el CUFE ya importado (409 si falta). 404 si no existe. Fallo MATIAS → error + ok=False.
        Idempotencia PARCIAL del par: `evento_032_at` se persiste apenas el 032 sale bien — si el 033
        falla, el reintento NO reenvía el 032 (un evento DIAN real no se duplica). Una fiscal ya
        'aceptada'/'reclamada' rechaza el reenvío (409).
        """
        fiscal = await self._repo.obtener(fiscal_id)
        if fiscal is None:
            raise CompraFiscalInexistente(fiscal_id)
        if fiscal.evento_estado in ("aceptada", "reclamada"):
            raise EventoRadianYaResuelto(fiscal_id, fiscal.evento_estado)
        if not fiscal.cufe_proveedor:
            raise CufeNoImportado(fiscal_id)
        cufe = fiscal.cufe_proveedor
        try:
            if fiscal.evento_032_at is None:
                r32 = await self._matias.enviar_evento(cufe, "032", "Recibo del bien o servicio")
                if not r32.ok:
                    return await self._fallo(fiscal_id, r32.error_msg), False
                fiscal = await self._repo.set_radian(fiscal_id, evento_032_at=now_co())
            r33 = await self._matias.enviar_evento(cufe, "033", "Aceptación expresa")
            if not r33.ok:
                return await self._fallo(fiscal_id, r33.error_msg), False
        except Exception:  # noqa: BLE001
            log.warning("radian_aceptar_fallo_transporte", fiscal_id=fiscal_id, exc_info=True)
            return await self._fallo(fiscal_id, "fallo de transporte MATIAS"), False
        actualizada = await self._repo.set_radian(
            fiscal_id, evento_033_at=now_co(),
            evento_estado="aceptada", evento_error=None,
        )
        return actualizada, True

    async def reclamar(self, fiscal_id: int, motivo: str | None) -> tuple[CompraFiscalLeer, bool]:
        """Reclama la factura: envía el evento 031 → estado 'reclamada'.

        Requiere el CUFE ya importado (409 si falta). 404 si no existe. Fallo MATIAS → error + ok=False.
        """
        fiscal = await self._repo.obtener(fiscal_id)
        if fiscal is None:
            raise CompraFiscalInexistente(fiscal_id)
        if fiscal.evento_estado in ("aceptada", "reclamada"):
            raise EventoRadianYaResuelto(fiscal_id, fiscal.evento_estado)
        if not fiscal.cufe_proveedor:
            raise CufeNoImportado(fiscal_id)
        try:
            r31 = await self._matias.enviar_evento(
                fiscal.cufe_proveedor, "031", (motivo or "").strip() or "Reclamo"
            )
            if not r31.ok:
                return await self._fallo(fiscal_id, r31.error_msg), False
        except Exception:  # noqa: BLE001
            log.warning("radian_reclamar_fallo_transporte", fiscal_id=fiscal_id, exc_info=True)
            return await self._fallo(fiscal_id, "fallo de transporte MATIAS"), False
        actualizada = await self._repo.set_radian(
            fiscal_id, evento_031_at=now_co(), evento_estado="reclamada", evento_error=None,
        )
        return actualizada, True

    async def _fallo(self, fiscal_id: int, error_msg: str | None) -> CompraFiscalLeer:
        """Persiste `evento_error` (sin lanzar, para no perder el error en el rollback)."""
        return await self._repo.set_radian(fiscal_id, evento_error=error_msg or "error MATIAS")
