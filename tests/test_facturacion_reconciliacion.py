"""F2.1.3 — reconciliación (D7.2): consulta el estado en MATIAS de las facturas estancadas.

Servicio PURO con fakes (transiciones + conteos + tolerancia a fallo de consulta) y repo en integración
(query de `pendiente`/`error` viejas, acotada y ordenada)."""
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from core.config.timezone import now_co
from modules.facturacion.matias_client import EstadoConsulta
from modules.facturacion.repository import FacturaLeer, SqlFacturacionRepository
from modules.facturacion.service import ConfigFiscal, FacturacionService

_CFG = ConfigFiscal(resolution_number="18760000001", prefix="FPR", notes="", city_id_default=None)


def _f(*, id, estado="pendiente", consecutivo=1000, cufe=None) -> FacturaLeer:
    return FacturaLeer(id=id, venta_id=id, tipo="factura", prefijo="FPR", consecutivo=consecutivo,
                       cufe=cufe, estado=estado, idempotency_key=f"k{id}", intentos=0)


class _Repo:
    def __init__(self, facturas):
        self._facturas = facturas
        self.transiciones: list[tuple] = []

    async def pendientes_para_reconciliar(self, *, antiguedad, limite):
        return self._facturas[:limite]

    async def marcar_aceptada(self, factura_id, *, cufe, dian_respuesta):
        self.transiciones.append(("aceptada", factura_id, cufe))

    async def marcar_rechazada(self, factura_id, *, error_msg, dian_respuesta):
        self.transiciones.append(("rechazada", factura_id, error_msg))


class _Matias:
    """Devuelve un `EstadoConsulta` por consecutivo (o excepción) según el mapa pre-cargado."""

    def __init__(self, por_consecutivo: dict):
        self._mapa = por_consecutivo
        self.consultados: list[int] = []

    async def consultar_estado(self, *, prefijo, consecutivo, resolution=None):
        self.consultados.append(consecutivo)
        val = self._mapa[consecutivo]
        if isinstance(val, Exception):
            raise val
        return val


def _svc(repo, matias):
    return FacturacionService(repo, matias, _CFG)


async def test_reconciliar_aplica_transiciones():
    facturas = [_f(id=1, consecutivo=1001), _f(id=2, consecutivo=1002), _f(id=3, consecutivo=1003)]
    matias = _Matias({
        1001: EstadoConsulta("aceptada", cufe="a" * 40, raw={"is_valid": True}),
        1002: EstadoConsulta("rechazada", raw={"is_valid": False}),
        1003: EstadoConsulta("pendiente"),
    })
    repo = _Repo(facturas)
    resumen = await _svc(repo, matias).reconciliar(antiguedad=now_co(), limite=100)
    assert resumen.revisadas == 3 and resumen.aceptadas == 1 and resumen.rechazadas == 1
    assert resumen.sin_cambio == 1 and resumen.ids_aceptadas == [1]
    assert ("aceptada", 1, "a" * 40) in repo.transiciones
    assert ("rechazada", 2, "rechazada (reconciliación)") in repo.transiciones


async def test_reconciliar_aceptada_sin_cufe_no_marca():
    # DIAN validada pero MATIAS no devolvió CUFE y la factura tampoco lo tiene → no se puede archivar.
    repo = _Repo([_f(id=1, consecutivo=1001, cufe=None)])
    matias = _Matias({1001: EstadoConsulta("aceptada", cufe=None)})
    resumen = await _svc(repo, matias).reconciliar(antiguedad=now_co(), limite=100)
    assert resumen.aceptadas == 0 and resumen.sin_cambio == 1 and repo.transiciones == []


async def test_reconciliar_consulta_falla_deja_igual():
    repo = _Repo([_f(id=1, consecutivo=1001)])
    matias = _Matias({1001: RuntimeError("timeout")})
    resumen = await _svc(repo, matias).reconciliar(antiguedad=now_co(), limite=100)
    assert resumen.sin_cambio == 1 and repo.transiciones == []     # no propaga, no toca el estado


async def test_reconciliar_sin_consecutivo_se_salta():
    repo = _Repo([_f(id=1, consecutivo=None)])
    matias = _Matias({})
    resumen = await _svc(repo, matias).reconciliar(antiguedad=now_co(), limite=100)
    assert resumen.sin_cambio == 1 and matias.consultados == []     # ni siquiera consulta


# --- repo en integración -----------------------------------------------------

async def test_pendientes_para_reconciliar_filtra_estado_y_antiguedad(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        repo = SqlFacturacionRepository(s)
        ids = []
        for k in ("p1", "p2", "p3"):
            cons = await repo.siguiente_consecutivo()
            f = await repo.crear_pendiente(
                venta_id=None, tipo="factura", prefijo="FPR", consecutivo=cons, idempotency_key=k
            )
            ids.append(f.id)
        await s.commit()
        # p2 → aceptada (terminal): NO debe salir; p3 → error: SÍ debe salir.
        await repo.marcar_aceptada(ids[1], cufe="a" * 40, dian_respuesta={})
        await repo.marcar_error(ids[2], error_msg="500")
        await s.commit()
        corte = now_co() + timedelta(minutes=1)            # todo lo creado ya es "viejo" frente al corte
        pendientes = await repo.pendientes_para_reconciliar(antiguedad=corte, limite=100)
        limitado = await repo.pendientes_para_reconciliar(antiguedad=corte, limite=1)
    estados = {p.id for p in pendientes}
    assert ids[0] in estados and ids[2] in estados        # pendiente + error
    assert ids[1] not in estados                          # aceptada queda fuera
    assert len(limitado) == 1                             # respeta el tope
