"""F2.1.2 — webhook MATIAS: seguridad (firma/token), idempotencia y delegación al worker.

Handler PURO (`manejar_webhook_matias`) con deps fakes: sin FastAPI ni red. El orden de las defensas
es el contrato (resolver token → firma → id → body → dedup → encolar)."""
import hashlib
import hmac
import json

from modules.facturacion.webhook import (
    AccionWebhook,
    WebhookMatiasDeps,
    WebhookResuelto,
    manejar_webhook_matias,
    verificar_firma_matias,
)

_SECRET = "wh_secret_123"


def _firmar(cuerpo: bytes, secret: str = _SECRET, *, prefijo: str = "") -> str:
    return prefijo + hmac.new(secret.encode(), cuerpo, hashlib.sha256).hexdigest()


class _Deps:
    """Fakes de los puertos del webhook; registra encolados y simula dedup por `vistos`."""

    def __init__(self, *, resuelto: WebhookResuelto | None, dup: bool = False) -> None:
        self._resuelto = resuelto
        self._dup = dup
        self.encolados: list[tuple[int, int]] = []
        self.registrados: list[tuple] = []
        self._next_id = 100

    def como_deps(self) -> WebhookMatiasDeps:
        return WebhookMatiasDeps(
            resolver=self._resolver, registrar=self._registrar, encolar=self._encolar
        )

    async def _resolver(self, token: str) -> WebhookResuelto | None:
        return self._resuelto

    async def _registrar(self, empresa_id, webhook_id, evento, payload) -> int | None:
        self.registrados.append((empresa_id, webhook_id, evento))
        if self._dup:
            return None
        self._next_id += 1
        return self._next_id

    async def _encolar(self, empresa_id: int, recibido_id: int) -> None:
        self.encolados.append((empresa_id, recibido_id))


_CUERPO = json.dumps({"event": "document.accepted", "document_key": "a" * 40}).encode()


async def _llamar(deps: _Deps, *, cuerpo=_CUERPO, firma=None, webhook_id="wh-1", token="tok"):
    return await manejar_webhook_matias(
        token=token, firma=firma if firma is not None else _firmar(cuerpo),
        webhook_id=webhook_id, cuerpo=cuerpo, deps=deps.como_deps(),
    )


# --- firma pura --------------------------------------------------------------

def test_verificar_firma_matias():
    cuerpo = b'{"x":1}'
    assert verificar_firma_matias(_SECRET, cuerpo, _firmar(cuerpo)) is True
    assert verificar_firma_matias(_SECRET, cuerpo, _firmar(cuerpo, prefijo="sha256=")) is True  # prefijo tolerado
    assert verificar_firma_matias(_SECRET, cuerpo, "deadbeef") is False                          # firma mala
    assert verificar_firma_matias(_SECRET, cuerpo, None) is False                                 # sin firma
    assert verificar_firma_matias(None, cuerpo, _firmar(cuerpo)) is False                         # sin secret → fail-closed


# --- handler: camino feliz y defensas ----------------------------------------

async def test_webhook_aceptado_encola():
    deps = _Deps(resuelto=WebhookResuelto(empresa_id=7, secret=_SECRET))
    res = await _llamar(deps)
    assert res.accion == AccionWebhook.ACEPTADO and res.status == 200
    assert deps.encolados == [(7, 101)]
    assert deps.registrados == [(7, "wh-1", "document.accepted")]


async def test_webhook_token_no_registrado_404():
    deps = _Deps(resuelto=None)
    res = await _llamar(deps)
    assert res.accion == AccionWebhook.NO_REGISTRADO and res.status == 404
    assert deps.encolados == []


async def test_webhook_firma_invalida_401():
    deps = _Deps(resuelto=WebhookResuelto(empresa_id=7, secret=_SECRET))
    res = await _llamar(deps, firma="firma-mala")
    assert res.accion == AccionWebhook.FIRMA_INVALIDA and res.status == 401
    assert deps.encolados == [] and deps.registrados == []   # no se registra nada sin firma válida


async def test_webhook_sin_secret_configurado_401():
    # Empresa registrada pero sin secret en secretos_empresa → fail-closed (no se procesa).
    deps = _Deps(resuelto=WebhookResuelto(empresa_id=7, secret=None))
    res = await _llamar(deps)
    assert res.accion == AccionWebhook.FIRMA_INVALIDA and res.status == 401


async def test_webhook_sin_id_400():
    deps = _Deps(resuelto=WebhookResuelto(empresa_id=7, secret=_SECRET))
    res = await _llamar(deps, webhook_id=None)
    assert res.accion == AccionWebhook.SIN_ID and res.status == 400


async def test_webhook_body_invalido_400():
    deps = _Deps(resuelto=WebhookResuelto(empresa_id=7, secret=_SECRET))
    cuerpo = b"no-json"
    res = await _llamar(deps, cuerpo=cuerpo, firma=_firmar(cuerpo))
    assert res.accion == AccionWebhook.BODY_INVALIDO and res.status == 400


async def test_webhook_duplicado_200_no_encola():
    deps = _Deps(resuelto=WebhookResuelto(empresa_id=7, secret=_SECRET), dup=True)
    res = await _llamar(deps)
    assert res.accion == AccionWebhook.DUPLICADO and res.status == 200
    assert deps.encolados == []     # idempotente: el segundo no re-encola


# --- aplicar_evento_dian (procesamiento en el worker, con fakes) -------------

from modules.facturacion.repository import FacturaLeer
from modules.facturacion.service import (
    ConfigFiscal,
    FacturacionService,
    _datos_evento,
    _solo_digitos,
)

_CFG = ConfigFiscal(resolution_number="r", prefix="FPR", notes="", city_id_default=None)


def _factura(*, id=1, estado="enviada", cufe=None, consecutivo=1024) -> FacturaLeer:
    return FacturaLeer(id=id, venta_id=10, tipo="factura", prefijo="FPR", consecutivo=consecutivo,
                       cufe=cufe, estado=estado, idempotency_key="k", intentos=0)


class _RepoEvento:
    def __init__(self, *, por_cufe=None, por_numero=None):
        self._por_cufe = por_cufe
        self._por_numero = por_numero
        self.acciones: list[tuple] = []

    async def buscar_por_cufe(self, cufe):
        return self._por_cufe

    async def buscar_por_numero(self, prefijo, consecutivo):
        return self._por_numero

    async def marcar_aceptada(self, factura_id, *, cufe, dian_respuesta):
        self.acciones.append(("aceptada", factura_id, cufe))

    async def marcar_rechazada(self, factura_id, *, error_msg, dian_respuesta):
        self.acciones.append(("rechazada", factura_id, error_msg))

    async def anotar_anulacion(self, factura_id, *, dian_respuesta):
        self.acciones.append(("anulada", factura_id))


def _svc(repo):
    return FacturacionService(repo, matias=None, config=_CFG)


def test_datos_evento_extrae_cufe_prefijo_consecutivo():
    cufe, prefijo, cons = _datos_evento(
        {"document": {"document_key": "a" * 40, "prefix": "FPR", "number": "FPR1024"}}
    )
    assert cufe == "a" * 40 and prefijo == "FPR" and cons == 1024
    assert _datos_evento({"cufe": "b" * 40})[0] == "b" * 40       # en la raíz
    assert _solo_digitos("FPR1024") == 1024 and _solo_digitos(None) is None


async def test_evento_accepted_marca_aceptada():
    repo = _RepoEvento(por_cufe=_factura(estado="enviada"))
    accion, fid = await _svc(repo).aplicar_evento_dian(
        "document.accepted", {"document_key": "a" * 40}
    )
    assert (accion, fid) == ("aceptada", 1)
    assert repo.acciones == [("aceptada", 1, "a" * 40)]


async def test_evento_accepted_idempotente():
    repo = _RepoEvento(por_cufe=_factura(estado="aceptada", cufe="a" * 40))
    accion, _ = await _svc(repo).aplicar_evento_dian("document.accepted", {"document_key": "a" * 40})
    assert accion == "aceptada" and repo.acciones == []          # ya aceptada: no re-marca


async def test_evento_rejected_marca_rechazada():
    repo = _RepoEvento(por_cufe=_factura(estado="enviada"))
    accion, _ = await _svc(repo).aplicar_evento_dian(
        "document.rejected", {"document_key": "a" * 40, "message": "NIT inválido"}
    )
    assert accion == "rechazada"
    assert repo.acciones == [("rechazada", 1, "NIT inválido")]


async def test_evento_voided_anula():
    repo = _RepoEvento(por_cufe=_factura(estado="aceptada", cufe="a" * 40))
    accion, _ = await _svc(repo).aplicar_evento_dian("document.voided", {"document_key": "a" * 40})
    assert accion == "anulada" and repo.acciones == [("anulada", 1)]


async def test_evento_voided_idempotente():
    # Ya anulada: el evento repetido no re-anota (mismo patrón idempotente que accepted/rejected).
    repo = _RepoEvento(por_cufe=_factura(estado="anulada", cufe="a" * 40))
    accion, fid = await _svc(repo).aplicar_evento_dian("document.voided", {"document_key": "a" * 40})
    assert (accion, fid) == ("anulada", 1) and repo.acciones == []


async def test_evento_sin_factura():
    repo = _RepoEvento(por_cufe=None, por_numero=None)
    accion, fid = await _svc(repo).aplicar_evento_dian("document.accepted", {"document_key": "z" * 40})
    assert (accion, fid) == ("sin_factura", None) and repo.acciones == []


async def test_evento_fallback_por_numero():
    repo = _RepoEvento(por_cufe=None, por_numero=_factura(estado="enviada"))
    accion, fid = await _svc(repo).aplicar_evento_dian(
        "document.accepted", {"prefix": "FPR", "number": "1024"}      # sin CUFE → correlaciona por número
    )
    assert (accion, fid) == ("aceptada", 1)
