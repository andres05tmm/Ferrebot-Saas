"""Fase 6 — extracción de recibo Bancolombia con Visión (proveedor MOCKEADO, sin llamadas reales).

Cubre el contrato que consumirá la Fase 6 completa:
  (a) recibo feliz → `ReciboExtraido` con campos correctos y `valor` `Decimal`;
  (b) confianza baja → se propaga y prende `requiere_revision`;
  (c) salida malformada (JSON roto / texto sin JSON) → degrada a `confianza 0` sin excepción;
  (d) el `Message` enviado al proveedor lleva `images` no vacío con el `ImageBlock`;
  (e) `valor` se parsea como `Decimal` exacto (número plano y formato colombiano).

Ningún test hace red ni usa API keys: el proveedor es un fake que devuelve texto pre-cargado.
"""
import json
from datetime import date
from decimal import Decimal

import pytest

from ai.vision.recibo import (
    UMBRAL_REVISION,
    ReciboExtraido,
    _a_decimal,
    extraer_recibo,
)
from core.llm.base import ImageBlock, LLMResponse, Message


# --------------------------------- fakes ----------------------------------
class FakeVisionLLM:
    """Proveedor de visión falso: devuelve un texto fijo y captura lo que recibió `generate`."""

    nombre = "fake"
    api_key = "k"

    def __init__(self, texto: str | None):
        self._texto = texto
        self.llamadas: list[dict] = []

    async def generate(self, *, messages, tools, model, system=None, **kw) -> LLMResponse:
        self.llamadas.append(
            {"messages": list(messages), "tools": tools, "model": model, "system": system}
        )
        return LLMResponse(text=self._texto)


# -------------------------------- helpers ---------------------------------
def _imagen() -> ImageBlock:
    return ImageBlock.desde_base64("QUJDREVG", "image/jpeg")


def _json_recibo(**over) -> str:
    base = {
        "fecha": "2026-07-05",
        "valor": 1150000,
        "referencia": "M1234567",
        "tipo_transaccion": "transferencia",
        "entidad_o_producto_origen": "Cuenta de Ahorros",
        "destino": "Ferretería PIM",
        "descripcion": "Pago de materiales",
        "confianza": 0.95,
    }
    base.update(over)
    return json.dumps(base)


# --------------------------------- tests ----------------------------------
async def test_a_recibo_feliz_extrae_campos_y_decimal():
    fake = FakeVisionLLM(_json_recibo())
    r = await extraer_recibo(_imagen(), fake)

    assert isinstance(r, ReciboExtraido)
    assert r.fecha == date(2026, 7, 5)
    assert r.valor == Decimal("1150000")
    assert isinstance(r.valor, Decimal)
    assert r.referencia == "M1234567"
    assert r.tipo_transaccion == "transferencia"
    assert r.entidad_o_producto_origen == "Cuenta de Ahorros"
    assert r.destino == "Ferretería PIM"
    assert r.descripcion == "Pago de materiales"
    assert r.confianza == Decimal("0.95")
    assert r.requiere_revision is False
    assert r.motivo is None


async def test_b_confianza_baja_se_propaga_y_requiere_revision():
    fake = FakeVisionLLM(_json_recibo(confianza=0.4))
    r = await extraer_recibo(_imagen(), fake)

    assert r.confianza == Decimal("0.4")
    assert r.confianza < UMBRAL_REVISION
    assert r.requiere_revision is True


@pytest.mark.parametrize(
    "texto",
    [
        "no pude leer la imagen, está borrosa",          # texto sin JSON
        '{"valor": 1000, "fecha": "2026-07-05"',          # JSON roto (sin cerrar)
        "",                                                # vacío
        None,                                              # el modelo no devolvió texto
    ],
)
async def test_c_salida_malformada_degrada_sin_excepcion(texto):
    fake = FakeVisionLLM(texto)
    r = await extraer_recibo(_imagen(), fake)  # no debe lanzar

    assert isinstance(r, ReciboExtraido)
    assert r.confianza == Decimal("0")
    assert r.requiere_revision is True
    assert r.motivo is not None
    assert r.valor is None


async def test_d_mensaje_lleva_imagen_no_vacia():
    fake = FakeVisionLLM(_json_recibo())
    imagen = _imagen()
    await extraer_recibo(imagen, fake, modelo="modelo-vision-x")

    assert len(fake.llamadas) == 1
    llamada = fake.llamadas[0]
    assert llamada["model"] == "modelo-vision-x"
    mensajes = llamada["messages"]
    assert len(mensajes) == 1
    msg = mensajes[0]
    assert isinstance(msg, Message)
    assert msg.role == "user"
    assert msg.images == [imagen]         # la imagen viaja en el bloque de visión
    assert len(msg.images) == 1
    assert msg.content                     # y hay prompt de texto acompañando


@pytest.mark.parametrize(
    ("entrada", "esperado"),
    [
        (1150000, Decimal("1150000")),            # número JSON (int)
        ("1150000", Decimal("1150000")),          # string plano
        ("1.150.000,00", Decimal("1150000.00")),  # formato colombiano (miles '.', decimal ',')
        ("$150.000", Decimal("150000")),          # un punto de miles + símbolo
        ("1150000,50", Decimal("1150000.50")),    # coma decimal colombiana
        (1150000.50, Decimal("1150000.50")),      # número JSON (float) sin ruido binario
    ],
)
async def test_e_valor_se_parsea_como_decimal_exacto(entrada, esperado):
    fake = FakeVisionLLM(_json_recibo(valor=entrada))
    r = await extraer_recibo(_imagen(), fake)

    assert isinstance(r.valor, Decimal)
    assert r.valor == esperado


def test_e_bis_parser_decimal_unitario():
    """El punto único con 3 decimales es separador de miles (montos reales Bancolombia)."""
    assert _a_decimal("1.150") == Decimal("1150")       # miles
    assert _a_decimal("1150.50") == Decimal("1150.50")  # decimal (1-2 dígitos)
    assert _a_decimal("basura") is None
    assert _a_decimal(None) is None
    assert _a_decimal(True) is None                     # bool no es un monto


async def test_json_embebido_en_texto_se_extrae():
    fake = FakeVisionLLM("Claro, aquí tienes los datos:\n" + _json_recibo() + "\n¡Listo!")
    r = await extraer_recibo(_imagen(), fake)
    assert r.valor == Decimal("1150000")
    assert r.motivo is None


async def test_json_en_fence_markdown_se_extrae():
    fake = FakeVisionLLM("```json\n" + _json_recibo(confianza=0.8) + "\n```")
    r = await extraer_recibo(_imagen(), fake)
    assert r.confianza == Decimal("0.8")
    assert r.destino == "Ferretería PIM"


async def test_alias_de_claves_del_modelo():
    """Si el modelo usa sinónimos (monto/destinatario/numeroReferencia/concepto) se mapean igual."""
    crudo = json.dumps(
        {
            "fecha": "2026-07-05",
            "monto": "2.300.000",
            "numeroReferencia": "AP-99",
            "destinatario": "Proveedor X",
            "concepto": "Cemento",
            "confidence": 0.9,
        }
    )
    fake = FakeVisionLLM(crudo)
    r = await extraer_recibo(_imagen(), fake)
    assert r.valor == Decimal("2300000")
    assert r.referencia == "AP-99"
    assert r.destino == "Proveedor X"
    assert r.descripcion == "Cemento"
    assert r.confianza == Decimal("0.9")


async def test_campos_ilegibles_quedan_none_sin_bajar_confianza_valida():
    fake = FakeVisionLLM(
        _json_recibo(referencia=None, destino="null", fecha="ilegible")
    )
    r = await extraer_recibo(_imagen(), fake)
    assert r.referencia is None
    assert r.destino is None      # "null" textual colapsa a None
    assert r.fecha is None        # fecha ilegible → None, no revienta
    assert r.confianza == Decimal("0.95")  # la confianza del modelo se respeta
