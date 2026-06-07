"""Flujo del webhook de WhatsApp (`apps/wa/webhook.py`) con fakes: seguridad, dedup, tenancy.

Verifica la regla de seguridad (firma ANTES de procesar, fail-closed), el dedup por id de mensaje,
la resolución de tenant (incl. número no mapeado) y que el `cliente_telefono` del Contexto SIEMPRE
sale del payload. Además, la ruta FastAPI y el procesador de eco (encolado).
"""
import hashlib
import hmac
import json

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport

from apps.wa.kapso import MensajeWa
from apps.wa.ports import AccionWa, WaDeps
from apps.wa.webhook import crear_router_wa, manejar_mensaje
from apps.wa.wiring import JOB_AGENTE, ProcesadorAgente
from ai.envelope import Contexto
from core.tenancy.context import ResolvedTenant

SECRET = "kapso-webhook-secret"
EVENTO = "whatsapp.message.received"
PNID = "123456789012345"
TEL = "573001112233"

PAYLOAD = {
    "message": {"id": "wamid.abc", "type": "text", "from": TEL, "text": {"body": "Hola"}},
    "conversation": {"phone_number_id": PNID},
    "phone_number_id": PNID,
}


def _cuerpo(payload=PAYLOAD) -> bytes:
    return json.dumps(payload).encode()


def _firma(cuerpo: bytes, secret: str = SECRET) -> str:
    return hmac.new(secret.encode(), cuerpo, hashlib.sha256).hexdigest()


def _tenant(estado: str = "activa", id: int = 7) -> ResolvedTenant:
    return ResolvedTenant(id=id, slug="pr", estado=estado, db_name="d", connection_url="postgresql://x/y")


class _FakeResolver:
    def __init__(self, tenant): self.tenant = tenant; self.pedidos = []
    async def por_phone_number_id(self, pnid):
        self.pedidos.append(pnid)
        return self.tenant


class _FakeDedup:
    def __init__(self, nuevo=True): self.nuevo = nuevo; self.vistos = []
    async def marcar_si_nuevo(self, message_id):
        self.vistos.append(message_id)
        return self.nuevo


class _FakeProcesar:
    def __init__(self): self.llamadas = []
    async def __call__(self, mensaje, ctx): self.llamadas.append((mensaje, ctx))


_SIN_TENANT = object()  # centinela: distinguir "no provisto" (usa default) de None (no mapeado)


def _deps(*, tenant=_SIN_TENANT, nuevo=True, procesar=None, secret=SECRET) -> tuple[WaDeps, _FakeProcesar]:
    proc = procesar or _FakeProcesar()
    resuelto = _tenant() if tenant is _SIN_TENANT else tenant
    deps = WaDeps(
        webhook_secret=secret,
        resolver=_FakeResolver(resuelto),
        dedup=_FakeDedup(nuevo),
        procesar=proc,
    )
    return deps, proc


async def _correr(deps, *, evento=EVENTO, cuerpo=None, firma=None):
    cuerpo = cuerpo if cuerpo is not None else _cuerpo()
    firma = firma if firma is not None else _firma(cuerpo)
    return await manejar_mensaje(evento=evento, firma=firma, cuerpo=cuerpo, deps=deps)


# --- seguridad: firma -------------------------------------------------------
async def test_firma_invalida_rechaza_403_y_no_procesa():
    deps, proc = _deps()
    res = await _correr(deps, firma="malo")
    assert res.accion == AccionWa.FIRMA_INVALIDA and res.status == 403
    assert proc.llamadas == []                 # no se procesó nada
    assert deps.dedup.vistos == []             # ni siquiera se llegó al dedup


async def test_sin_secreto_configurado_rechaza_fail_closed():
    deps, proc = _deps(secret=None)
    res = await _correr(deps)
    assert res.accion == AccionWa.FIRMA_INVALIDA and res.status == 403


# --- evento / parseo --------------------------------------------------------
async def test_evento_distinto_se_ignora():
    deps, proc = _deps()
    res = await _correr(deps, evento="whatsapp.message.delivered")
    assert res.accion == AccionWa.EVENTO_IGNORADO and res.status == 200
    assert proc.llamadas == []


async def test_body_invalido_da_400():
    deps, _ = _deps()
    cuerpo = b"no-es-json{"
    res = await _correr(deps, cuerpo=cuerpo, firma=_firma(cuerpo))
    assert res.accion == AccionWa.BODY_INVALIDO and res.status == 400


async def test_body_json_no_objeto_da_400():
    deps, _ = _deps()
    cuerpo = b"[1, 2, 3]"  # JSON válido pero no es un objeto
    res = await _correr(deps, cuerpo=cuerpo, firma=_firma(cuerpo))
    assert res.accion == AccionWa.BODY_INVALIDO and res.status == 400


async def test_mensaje_no_texto_se_ignora():
    payload = {"message": {"id": "w1", "type": "image", "from": TEL}, "phone_number_id": PNID}
    cuerpo = _cuerpo(payload)
    deps, proc = _deps()
    res = await _correr(deps, cuerpo=cuerpo, firma=_firma(cuerpo))
    assert res.accion == AccionWa.MENSAJE_IGNORADO and res.status == 200
    assert proc.llamadas == []


# --- dedup ------------------------------------------------------------------
async def test_dedup_mensaje_repetido():
    deps, proc = _deps(nuevo=False)
    res = await _correr(deps)
    assert res.accion == AccionWa.DUPLICADO and res.status == 200
    assert deps.dedup.vistos == ["wamid.abc"]  # dedup por message.id
    assert proc.llamadas == []                 # no se procesa el duplicado


# --- tenancy ----------------------------------------------------------------
async def test_numero_no_mapeado_se_descarta():
    deps, proc = _deps(tenant=None)            # resolver devuelve None
    res = await _correr(deps)
    assert res.accion == AccionWa.NO_MAPEADO and res.status == 200
    assert deps.resolver.pedidos == [PNID]     # resolvió por phone_number_id
    assert proc.llamadas == []


async def test_empresa_inactiva_se_descarta():
    deps, proc = _deps(tenant=_tenant(estado="suspendida"))
    res = await _correr(deps)
    assert res.accion == AccionWa.EMPRESA_INACTIVA and res.status == 200
    assert proc.llamadas == []


# --- éxito: Contexto con el teléfono DEL PAYLOAD ----------------------------
async def test_procesado_construye_contexto_con_telefono_del_payload():
    deps, proc = _deps(tenant=_tenant(id=7))
    res = await _correr(deps)
    assert res.accion == AccionWa.PROCESADO and res.status == 200
    mensaje, ctx = proc.llamadas[0]
    assert isinstance(ctx, Contexto)
    assert ctx.tenant_id == 7
    assert ctx.cliente_telefono == TEL         # SIEMPRE del payload (message.from)
    assert ctx.origen == "whatsapp"
    assert mensaje.texto == "Hola"


# --- procesador del agente (encola con el teléfono del Contexto) ------------
async def test_procesador_agente_encola_el_turno():
    encolados = []
    async def fake_encolar(*args):
        encolados.append(args)
    proc = ProcesadorAgente(encolar=fake_encolar)
    mensaje = MensajeWa(message_id="w1", telefono=TEL, phone_number_id=PNID, texto="hola")
    ctx = Contexto(tenant_id=7, usuario_id=0, rol="cliente", origen="whatsapp", cliente_telefono=TEL)
    await proc(mensaje, ctx)
    # Encola el job con (tenant_id, phone_number_id, cliente_telefono del ctx, texto, message_id).
    assert encolados == [(JOB_AGENTE, 7, PNID, TEL, "hola", "w1")]


# --- ruta FastAPI -----------------------------------------------------------
async def test_ruta_webhook_responde_200_con_firma_valida():
    deps, proc = _deps()
    app = FastAPI()
    app.include_router(crear_router_wa())
    app.state.wa_deps = deps

    cuerpo = _cuerpo()
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as cliente:
        r = await cliente.post(
            "/wa/webhook", content=cuerpo,
            headers={"X-Webhook-Event": EVENTO, "X-Webhook-Signature": _firma(cuerpo)},
        )
    assert r.status_code == 200 and r.json()["accion"] == "procesado"
    assert proc.llamadas and proc.llamadas[0][1].cliente_telefono == TEL


async def test_ruta_webhook_rechaza_firma_invalida():
    deps, _ = _deps()
    app = FastAPI()
    app.include_router(crear_router_wa())
    app.state.wa_deps = deps
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as cliente:
        r = await cliente.post(
            "/wa/webhook", content=_cuerpo(),
            headers={"X-Webhook-Event": EVENTO, "X-Webhook-Signature": "malo"},
        )
    assert r.status_code == 403


# --- job del agente (resuelve tenant + delega en el AgenteWa) ---------------
async def test_job_atiende_via_agente():
    from apps.worker.jobs import atender_mensaje_wa

    atendidos = []
    tenant = _tenant(id=7)

    class _FakeAgente:
        async def atender(self, mensaje, tnt):
            atendidos.append((mensaje, tnt))

    async def _resolver(tid):
        return tenant if tid == 7 else None

    ctx = {"resolver_tenant": _resolver, "wa_agente": _FakeAgente()}
    res = await atender_mensaje_wa(ctx, 7, PNID, TEL, "quiero una cita", "wamid.1")
    assert res == "atendido"
    mensaje, tnt = atendidos[0]
    assert tnt.id == 7
    assert mensaje.telefono == TEL and mensaje.texto == "quiero una cita"


async def test_job_sin_tenant_no_atiende():
    from apps.worker.jobs import atender_mensaje_wa

    async def _resolver(tid):
        return None  # tenant_id ya no mapea

    res = await atender_mensaje_wa({"resolver_tenant": _resolver, "wa_agente": None}, 9, PNID, TEL, "hola", "w1")
    assert res == "sin_tenant"
