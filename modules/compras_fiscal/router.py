"""Router de compras fiscales (gateado por la capacidad OPCIONAL `compras_fiscal`). RBAC = admin.

Sin la feature, las rutas responden 404 (como si no existieran, feature-flags.md). Dos planos:
- **DATOS (Slice 6a):** registrar/listar compras fiscales y derivarlas de una compra normal. SIN MATIAS.
- **RADIAN-FE (Slice 6b):** eventos DIAN REALES sobre la factura recibida (acuse/aceptar/reclamar). El
  cliente MATIAS por-empresa se compone en `get_radian_deps` (perezoso) y se inyecta en `RadianService`;
  un fallo de MATIAS responde **502** con el `evento_error` ya persistido (no rompe). CUFE MANUAL.

La lógica vive en los servicios; aquí solo se valida, se compone y se mapea a HTTP.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth import Principal, require_role
from core.auth.features import require_feature
from core.config import get_settings
from core.db.session import control_session, get_tenant_db
from core.logging import get_logger
from modules.compras_fiscal.errors import (
    CompraFiscalInexistente,
    CompraInexistente,
    CufeNoImportado,
    EventoRadianYaResuelto,
    QRInvalido,
)
from modules.compras_fiscal.radian_service import RadianMatias, RadianService
from modules.compras_fiscal.recepcion_service import RecepcionService
from modules.compras_fiscal.repository import SqlComprasFiscalRepository
from modules.compras_fiscal.schemas import (
    AmbienteFiscal,
    CompraFiscalCrear,
    CompraFiscalLeer,
    EscanearQR,
    FacturaRecibidaLeer,
    ImportarCufe,
    ReclamarMotivo,
)
from modules.compras_fiscal.service import ComprasFiscalService
from modules.facturacion.config import cargar_ambiente, cargar_config_matias
from modules.facturacion.matias_client import MatiasClient
from modules.proveedores.repository import SqlProveedoresRepository

log = get_logger("compras_fiscal.router")

router = APIRouter(tags=["compras-fiscal"], dependencies=[Depends(require_feature("compras_fiscal"))])


def _service(session: AsyncSession) -> ComprasFiscalService:
    return ComprasFiscalService(SqlComprasFiscalRepository(session))


@dataclass(frozen=True, slots=True)
class RadianDeps:
    """Dependencias de RADIAN compuestas por-empresa: cliente MATIAS perezoso + ambiente declarado."""

    matias: RadianMatias
    ambiente: str


async def get_radian_deps(request: Request) -> RadianDeps:
    """Compone el cliente MATIAS de la empresa (perezoso, sin red al construir) + su ambiente DIAN.

    Descifra las credenciales del control DB (sesión per-call). Overridable en tests para FAKEAR MATIAS.
    """
    async with control_session() as cs:
        cred, config = await cargar_config_matias(
            cs, get_settings().secrets_master_key, request.state.tenant.id
        )
    return RadianDeps(matias=MatiasClient(cred), ambiente=config.ambiente)


def get_radian_service(
    session: AsyncSession = Depends(get_tenant_db),
    deps: RadianDeps = Depends(get_radian_deps),
) -> RadianService:
    """`RadianService` sobre la sesión del tenant + el cliente MATIAS de la empresa (overridable)."""
    return RadianService(SqlComprasFiscalRepository(session), deps.matias, ambiente=deps.ambiente)


async def get_recepcion_deps(request: Request) -> RadianDeps | None:
    """Cliente MATIAS de la empresa para la recepción por QR, o **None** si no está configurado.

    A diferencia de `get_radian_deps` (que asume MATIAS presente), la recepción DEGRADA: si la empresa no
    tiene credenciales MATIAS (config incompleta en el control DB), devuelve None y la recepción registra
    igual la deuda + el soporte con el CUFE (sin acuse ni XML). Overridable en tests para FAKEAR MATIAS.
    """
    try:
        async with control_session() as cs:
            cred, config = await cargar_config_matias(
                cs, get_settings().secrets_master_key, request.state.tenant.id
            )
    except Exception:  # noqa: BLE001 — config MATIAS ausente/incompleta: se degrada sin acoplar
        log.info("recepcion_matias_no_configurado", tenant_id=request.state.tenant.id)
        return None
    return RadianDeps(matias=MatiasClient(cred), ambiente=config.ambiente)


def get_recepcion_service(
    session: AsyncSession = Depends(get_tenant_db),
    deps: RadianDeps | None = Depends(get_recepcion_deps),
) -> RecepcionService:
    """`RecepcionService` sobre la sesión del tenant: compone fiscal + proveedores + RADIAN (opcional)."""
    radian = (
        RadianService(SqlComprasFiscalRepository(session), deps.matias, ambiente=deps.ambiente)
        if deps is not None
        else None
    )
    return RecepcionService(
        SqlComprasFiscalRepository(session), SqlProveedoresRepository(session), radian=radian
    )


# ---- DATOS (Slice 6a) ------------------------------------------------------
@router.post("/compras-fiscal", response_model=CompraFiscalLeer, status_code=status.HTTP_201_CREATED)
async def crear_compra_fiscal(
    payload: CompraFiscalCrear,
    session: AsyncSession = Depends(get_tenant_db),
    _user: Principal = Depends(require_role("admin")),
) -> CompraFiscalLeer:
    """Registra una compra fiscal con su desglose de IVA (alimenta el Libro IVA)."""
    return await _service(session).registrar(payload)


@router.get("/compras-fiscal", response_model=list[CompraFiscalLeer])
async def listar_compras_fiscal(
    desde: date | None = Query(default=None),
    hasta: date | None = Query(default=None),
    session: AsyncSession = Depends(get_tenant_db),
    _user: Principal = Depends(require_role("admin")),
) -> list[CompraFiscalLeer]:
    """Compras fiscales del rango (default mes en curso, hora Colombia)."""
    return await _service(session).listar(desde=desde, hasta=hasta)


@router.get("/compras-fiscal/ambiente", response_model=AmbienteFiscal)
async def ambiente_fiscal(
    request: Request,
    _user: Principal = Depends(require_role("admin")),
) -> AmbienteFiscal:
    """Ambiente DIAN declarado de la empresa ('produccion'|'pruebas') para la confirmación del operador."""
    async with control_session() as cs:
        ambiente = await cargar_ambiente(cs, request.state.tenant.id)
    return AmbienteFiscal(ambiente=ambiente)


@router.post("/compras/{compra_id}/to-fiscal", response_model=CompraFiscalLeer)
async def compra_a_fiscal(
    compra_id: int,
    response: Response,
    session: AsyncSession = Depends(get_tenant_db),
    _user: Principal = Depends(require_role("admin")),
) -> CompraFiscalLeer:
    """Crea una compra fiscal a partir de una compra normal (idempotente). 201 si la crea, 200 si ya
    existía; 404 si la compra no existe."""
    try:
        fiscal, creada = await _service(session).desde_compra(compra_id)
    except CompraInexistente as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    response.status_code = status.HTTP_201_CREATED if creada else status.HTTP_200_OK
    return fiscal


# ---- RADIAN-FE recibidas (Slice 6b) — eventos DIAN REALES ------------------
@router.post("/compras-fiscal/{fiscal_id}/importar", response_model=CompraFiscalLeer)
async def importar_cufe(
    fiscal_id: int,
    payload: ImportarCufe,
    response: Response,
    service: RadianService = Depends(get_radian_service),
    _user: Principal = Depends(require_role("admin")),
) -> CompraFiscalLeer:
    """Importa el CUFE de la factura recibida y envía el acuse 030. 404 si no existe; 502 si MATIAS falla."""
    try:
        fiscal, ok = await service.importar(fiscal_id, payload.cufe.strip())
    except CompraFiscalInexistente as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    if not ok:
        response.status_code = status.HTTP_502_BAD_GATEWAY
    return fiscal


@router.post("/compras-fiscal/{fiscal_id}/aceptar", response_model=CompraFiscalLeer)
async def aceptar_factura(
    fiscal_id: int,
    response: Response,
    service: RadianService = Depends(get_radian_service),
    _user: Principal = Depends(require_role("admin")),
) -> CompraFiscalLeer:
    """Acepta la FE recibida: envía 032 + 033 → estado 'aceptada'. 404/409; 502 si MATIAS falla."""
    try:
        fiscal, ok = await service.aceptar(fiscal_id)
    except CompraFiscalInexistente as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except (CufeNoImportado, EventoRadianYaResuelto) as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    if not ok:
        response.status_code = status.HTTP_502_BAD_GATEWAY
    return fiscal


# ---- Recepción por QR (ADR 0020, F1) — factura recibida → cuenta por pagar ---
@router.get("/facturas-recibidas", response_model=list[FacturaRecibidaLeer])
async def listar_facturas_recibidas(
    service: RecepcionService = Depends(get_recepcion_service),
    _user: Principal = Depends(require_role("admin")),
) -> list[FacturaRecibidaLeer]:
    """Facturas de proveedor recibidas por QR (soporte fiscal con CUFE + su cuenta por pagar)."""
    return await service.listar_recibidas()


@router.post("/facturas-recibidas/escanear", response_model=FacturaRecibidaLeer)
async def escanear_factura_recibida(
    payload: EscanearQR,
    response: Response,
    service: RecepcionService = Depends(get_recepcion_service),
    user: Principal = Depends(require_role("admin")),
) -> FacturaRecibidaLeer:
    """Escanea el QR de una factura de proveedor: extrae el CUFE, registra la cuenta por pagar y el
    soporte fiscal, y acusa recibo (030) por RADIAN. 201 la primera vez; 200 si el CUFE ya se había
    recibido (idempotente, no duplica); 422 si el QR no contiene un CUFE reconocible."""
    try:
        recibida, creada = await service.recibir(payload, usuario_id=user.user_id)
    except QRInvalido as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    response.status_code = status.HTTP_201_CREATED if creada else status.HTTP_200_OK
    return recibida


@router.post("/compras-fiscal/{fiscal_id}/reclamar", response_model=CompraFiscalLeer)
async def reclamar_factura(
    fiscal_id: int,
    payload: ReclamarMotivo,
    response: Response,
    service: RadianService = Depends(get_radian_service),
    _user: Principal = Depends(require_role("admin")),
) -> CompraFiscalLeer:
    """Reclama la FE recibida: envía el evento 031 → estado 'reclamada'. 404/409; 502 si MATIAS falla."""
    try:
        fiscal, ok = await service.reclamar(fiscal_id, payload.motivo)
    except CompraFiscalInexistente as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except (CufeNoImportado, EventoRadianYaResuelto) as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    if not ok:
        response.status_code = status.HTTP_502_BAD_GATEWAY
    return fiscal
