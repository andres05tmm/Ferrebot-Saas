"""Recepción de facturas de proveedor por QR (ADR 0020, F1).

Orquesta —sin SQL propio— la recepción de una factura electrónica de proveedor escaneando su QR:

  QR ──extraer_cufe──▶ CUFE ──(idempotencia)──▶ soporte fiscal (`compras_fiscal`, con el CUFE)
                                             + cuenta por pagar (`facturas_proveedores`, con vencimiento)
                                             + acuse RADIAN 030 (reusa `RadianService`, best-effort)

**Sin inventario (v1):** el mapeo de las líneas al catálogo del tenant es de alto riesgo y queda diferido
a F4 (ADR 0020 §Problema clave). Aquí NO se crea ninguna `compra` ni movimiento de stock.

**Idempotencia por CUFE (invariante crítico):** el CUFE es el ancla. `compras_fiscal.cufe_proveedor` es
UNIQUE (migración 0042) y la cuenta por pagar usa el CUFE como PK; reescanear el mismo QR devuelve el par
existente (replay) sin duplicar factura ni deuda ni reenviar eventos DIAN.

**Degradación (no acoplamiento):** si la empresa no tiene credenciales MATIAS, `radian` llega None y la
recepción igual registra la deuda + el soporte con el CUFE (sin acuse ni XML), como el patrón
Cloudinary→503. El acuse se puede reintentar luego desde la superficie RADIAN existente.

**Aislamiento por tenant:** ambos repositorios y el `RadianService` operan sobre la sesión del tenant del
request; la base ES la frontera (regla multitenancy). Todo corre en la misma transacción.
"""
from __future__ import annotations

from core.config.timezone import today_co
from core.logging import get_logger
from modules.compras_fiscal.qr import extraer_cufe
from modules.compras_fiscal.radian_service import RadianService
from modules.compras_fiscal.repository import SqlComprasFiscalRepository
from modules.compras_fiscal.schemas import (
    CompraFiscalLeer,
    EscanearQR,
    FacturaRecibidaLeer,
)
from modules.proveedores.repository import SqlProveedoresRepository
from modules.proveedores.schemas import FacturaProveedorLeer

log = get_logger("compras_fiscal.recepcion")


class RecepcionService:
    """Recibe una factura de proveedor por QR: soporte fiscal + cuenta por pagar + acuse RADIAN opcional."""

    def __init__(
        self,
        fiscal: SqlComprasFiscalRepository,
        proveedores: SqlProveedoresRepository,
        *,
        radian: RadianService | None = None,
    ) -> None:
        self._fiscal = fiscal
        self._prov = proveedores
        self._radian = radian

    async def recibir(
        self, datos: EscanearQR, *, usuario_id: int | None = None
    ) -> tuple[FacturaRecibidaLeer, bool]:
        """Registra la factura recibida y devuelve `(vista, creada)`. `QRInvalido` si el QR no trae CUFE.

        `creada=False` (replay idempotente) si ese CUFE ya se había recibido: se devuelve el par existente
        sin tocar MATIAS ni crear nada. `creada=True` la primera vez.
        """
        cufe = extraer_cufe(datos.qr)   # QRInvalido → el router responde 422

        existente = await self._fiscal.por_cufe(cufe)
        if existente is not None:
            cxp = await self._prov.obtener(cufe)
            log.info("factura_recibida_replay", cufe=cufe, fiscal_id=existente.id)
            return _componer(cufe, existente, cxp), False

        # 1) Soporte fiscal con el CUFE (ancla de idempotencia persistida ANTES del acuse).
        fiscal = await self._fiscal.crear(
            proveedor_nit=datos.proveedor_nit,
            base=datos.base,
            iva=datos.iva,
            total=datos.total,
            cufe_proveedor=cufe,
        )

        # 2) Cuenta por pagar (deuda con vencimiento real). PK = CUFE: dedup natural de la deuda por QR.
        cxp = await self._prov.crear_factura(
            factura_id=cufe,
            proveedor=(datos.proveedor_nombre or datos.proveedor_nit),
            descripcion=_descripcion(datos),
            total=datos.total,
            fecha=datos.fecha or today_co(),
            fecha_vencimiento=datos.fecha_vencimiento,
            usuario_id=usuario_id,
        )

        # 3) Acuse RADIAN 030 (best-effort). Sin credenciales MATIAS → degrada (deuda + soporte ya quedaron).
        if self._radian is not None:
            fiscal, ok = await self._radian.importar(fiscal.id, cufe)
            if not ok:
                log.warning("factura_recibida_acuse_fallo", cufe=cufe, error=fiscal.evento_error)

        log.info("factura_recibida_creada", cufe=cufe, fiscal_id=fiscal.id)
        return _componer(cufe, fiscal, cxp), True

    async def listar_recibidas(self) -> list[FacturaRecibidaLeer]:
        """Facturas recibidas (compras fiscales con CUFE) compuestas con su cuenta por pagar."""
        fiscales = await self._fiscal.listar_recibidas()
        cufes = [f.cufe_proveedor for f in fiscales if f.cufe_proveedor]
        cxps = await self._prov.mapa_por_ids(cufes)
        return [
            _componer(f.cufe_proveedor or "", f, cxps.get(f.cufe_proveedor or ""))
            for f in fiscales
        ]


def _descripcion(datos: EscanearQR) -> str:
    """Descripción legible de la deuda: la que dé el operador o una derivada del nº de factura/proveedor."""
    if datos.descripcion and datos.descripcion.strip():
        return datos.descripcion.strip()
    if datos.numero_factura and datos.numero_factura.strip():
        return f"Factura electrónica {datos.numero_factura.strip()}"
    return f"Factura electrónica de proveedor {datos.proveedor_nit}"


def _componer(
    cufe: str, fiscal: CompraFiscalLeer, cxp: FacturaProveedorLeer | None
) -> FacturaRecibidaLeer:
    """Une el soporte fiscal (CUFE + RADIAN) con la cuenta por pagar en la vista de salida."""
    return FacturaRecibidaLeer(
        cufe=cufe,
        fiscal_id=fiscal.id,
        proveedor_nit=fiscal.proveedor_nit,
        base=fiscal.base,
        iva=fiscal.iva,
        total=fiscal.total,
        evento_030_at=fiscal.evento_030_at,
        evento_estado=fiscal.evento_estado,
        evento_error=fiscal.evento_error,
        cuenta_por_pagar_id=cxp.id if cxp else None,
        fecha=cxp.fecha if cxp else None,
        fecha_vencimiento=cxp.fecha_vencimiento if cxp else None,
        pendiente=cxp.pendiente if cxp else None,
        estado=cxp.estado if cxp else None,
        descripcion=cxp.descripcion if cxp else None,
    )
