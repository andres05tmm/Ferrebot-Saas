"""Unit del adaptador del vendor Kapso (`apps/wa/kapso.py`): parseo, firma y envío. Sin red.

El payload de muestra reproduce el contrato real de `whatsapp.message.received` (docs.kapso.ai).
"""
import hashlib
import hmac
import json

from apps.wa.kapso import KapsoSender, MensajeWa, parsear_mensaje, verificar_firma

SECRET = "kapso-webhook-secret"

# Payload real (recortado) de whatsapp.message.received.
PAYLOAD = {
    "message": {
        "id": "wamid.123",
        "timestamp": "1730092800",
        "type": "text",
        "from": "573001112233",
        "text": {"body": "Hola, quiero una cita"},
        "kapso": {"direction": "inbound"},
    },
    "conversation": {"id": "conv_1", "phone_number_id": "123456789012345"},
    "is_new_conversation": True,
    "phone_number_id": "123456789012345",
}


def _firma(cuerpo: bytes, secret: str = SECRET) -> str:
    return hmac.new(secret.encode(), cuerpo, hashlib.sha256).hexdigest()


# --- firma ------------------------------------------------------------------
def test_firma_valida():
    cuerpo = json.dumps(PAYLOAD).encode()
    assert verificar_firma(SECRET, cuerpo, _firma(cuerpo)) is True


def test_firma_invalida_y_fail_closed():
    cuerpo = json.dumps(PAYLOAD).encode()
    assert verificar_firma(SECRET, cuerpo, "deadbeef") is False          # firma que no coincide
    assert verificar_firma(SECRET, cuerpo, _firma(cuerpo, "otro")) is False  # otro secreto
    assert verificar_firma(SECRET, cuerpo, None) is False                # sin header de firma
    assert verificar_firma(None, cuerpo, _firma(cuerpo)) is False        # sin secreto configurado
    # Un byte distinto en el cuerpo invalida la firma (se firma el cuerpo CRUDO).
    assert verificar_firma(SECRET, cuerpo + b" ", _firma(cuerpo)) is False


# --- parseo -----------------------------------------------------------------
def test_parsear_mensaje_texto():
    msg = parsear_mensaje(PAYLOAD)
    assert msg == MensajeWa(
        message_id="wamid.123", telefono="573001112233",
        phone_number_id="123456789012345", texto="Hola, quiero una cita",
    )


def test_parsear_usa_phone_number_id_de_conversacion_si_falta_arriba():
    payload = {**PAYLOAD}
    del payload["phone_number_id"]  # cae al de conversation
    msg = parsear_mensaje(payload)
    assert msg is not None and msg.phone_number_id == "123456789012345"


def test_parsear_ignora_no_texto_y_campos_faltantes():
    assert parsear_mensaje({"message": {"id": "x", "from": "1", "type": "image"}}) is None  # no texto
    assert parsear_mensaje({"message": {"type": "text", "from": "1", "text": {"body": "h"}}}) is None  # sin id
    assert parsear_mensaje({"conversation": {}}) is None  # sin message
    assert parsear_mensaje({"message": {"id": "x", "type": "text", "from": "1",
                                        "text": {"body": "h"}}}) is None  # sin phone_number_id


# --- envío ------------------------------------------------------------------
class _FakeResp:
    def __init__(self, data): self._data = data
    def raise_for_status(self): return None
    def json(self): return self._data


class _FakeHttpx:
    def __init__(self): self.llamadas = []
    async def post(self, url, *, json, headers):
        self.llamadas.append((url, json, headers))
        return _FakeResp({"messages": [{"id": "wamid.out.1"}]})


async def test_kapso_sender_arma_la_peticion():
    fake = _FakeHttpx()
    sender = KapsoSender("api-key-123", base_url="https://api.kapso.ai/meta/whatsapp/v24.0", client=fake)
    res = await sender.enviar_texto(phone_number_id="123456789012345", to="573001112233", texto="recibí: hola")

    assert res["messages"][0]["id"] == "wamid.out.1"
    url, cuerpo, headers = fake.llamadas[0]
    assert url == "https://api.kapso.ai/meta/whatsapp/v24.0/123456789012345/messages"
    assert headers["X-API-Key"] == "api-key-123"            # credencial en header, no en URL
    assert cuerpo == {
        "messaging_product": "whatsapp", "recipient_type": "individual",
        "to": "573001112233", "type": "text",
        "text": {"body": "recibí: hola", "preview_url": False},
    }


async def test_kapso_sender_arma_la_plantilla():
    """El recordatorio de reconfirmación va como template (único tipo permitido fuera de las 24h)."""
    fake = _FakeHttpx()
    sender = KapsoSender("api-key-123", base_url="https://api.kapso.ai/meta/whatsapp/v24.0", client=fake)
    res = await sender.enviar_plantilla(
        phone_number_id="123456789012345", to="573001112233",
        nombre="recordatorio_cita", idioma="es",
    )

    assert res["messages"][0]["id"] == "wamid.out.1"
    url, cuerpo, headers = fake.llamadas[0]
    assert url == "https://api.kapso.ai/meta/whatsapp/v24.0/123456789012345/messages"
    assert headers["X-API-Key"] == "api-key-123"
    assert cuerpo == {
        "messaging_product": "whatsapp", "recipient_type": "individual",
        "to": "573001112233", "type": "template",
        "template": {"name": "recordatorio_cita", "language": {"code": "es"}},
    }
