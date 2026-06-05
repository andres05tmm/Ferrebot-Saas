"""Caché de MatiasClient por tenant en el runtime del worker (reusa token JWT + caché de ciudades).

El bug: `_ServicioEmision.emitir` construía un `MatiasClient` nuevo en CADA emisión → re-login y
recarga de ciudades cada vez. La caché debe vivir en el runtime (compartida entre jobs), NO en
`_ServicioEmision` (que se crea nuevo por job). Patrón de los tests de worker: colaboradores
mockeados, sin Redis ni red real.
"""
from types import SimpleNamespace

import apps.worker.main as wm
from modules.facturacion.matias_client import MatiasCredenciales
from modules.facturacion.politica import Decision


class _FakeControlSession:
    async def __aenter__(self) -> "_FakeControlSession":
        return self

    async def __aexit__(self, *_exc) -> bool:
        return False


class _CountingFactory:
    """Factory de MatiasClient que cuenta construcciones y devuelve un stand-in por llamada."""

    def __init__(self) -> None:
        self.count = 0
        self.creados: list = []

    def __call__(self, cred: MatiasCredenciales) -> SimpleNamespace:
        self.count += 1
        cliente = SimpleNamespace(cred=cred, n=self.count)
        self.creados.append(cliente)
        return cliente


def _instalar_dobles(monkeypatch, registro_clientes: list) -> None:
    """Aísla el camino de emisión: mockea control DB, tenant, config y servicio del worker."""

    def _fake_control_session() -> _FakeControlSession:
        return _FakeControlSession()

    async def _fake_resolve(_cs, tid: int) -> SimpleNamespace:
        return SimpleNamespace(id=tid)

    async def _fake_cargar(_cs, _master: str, tid: int):
        return MatiasCredenciales("e@x", "pw", f"http://t{tid}"), object()

    async def _fake_tenant_session(_tenant):
        yield object()

    class _FakeService:
        def __init__(self, _repo, cliente, _config) -> None:
            registro_clientes.append(cliente)

        async def emitir(self, _factura_id: int) -> Decision:
            return Decision("aceptada", reintentar=False, dead_letter=False)

    monkeypatch.setattr(wm, "control_session", _fake_control_session)
    monkeypatch.setattr(wm, "resolve_tenant_by_id", _fake_resolve)
    monkeypatch.setattr(wm, "cargar_config_matias", _fake_cargar)
    monkeypatch.setattr(wm, "tenant_session", _fake_tenant_session)
    monkeypatch.setattr(wm, "SqlFacturacionRepository", lambda _s: object())
    monkeypatch.setattr(wm, "FacturacionService", _FakeService)


async def test_cliente_matias_cacheado_por_tenant(monkeypatch):
    registro: list = []
    _instalar_dobles(monkeypatch, registro)
    factory = _CountingFactory()
    cache = wm._MatiasClientCache(factory=factory)

    # Dos emisiones del MISMO tenant; cada job arma su _ServicioEmision nuevo con la caché compartida.
    await wm._ServicioEmision(1, "master", cache).emitir(10)
    await wm._ServicioEmision(1, "master", cache).emitir(11)

    assert factory.count == 1            # MatiasClient construido UNA sola vez
    assert registro[0] is registro[1]    # ambas emisiones reusan el mismo cliente


async def test_tenants_distintos_no_comparten_cliente(monkeypatch):
    registro: list = []
    _instalar_dobles(monkeypatch, registro)
    factory = _CountingFactory()
    cache = wm._MatiasClientCache(factory=factory)

    await wm._ServicioEmision(1, "master", cache).emitir(10)
    await wm._ServicioEmision(2, "master", cache).emitir(10)

    assert factory.count == 2                 # un cliente por empresa
    assert registro[0] is not registro[1]     # no se mezclan clientes entre empresas
