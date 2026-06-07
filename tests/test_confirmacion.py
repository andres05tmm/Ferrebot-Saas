"""CR-2 — dominio puro de confirmación: clasificación de texto + (de)serialización (sin Redis).

Pin del contrato:
  - `es_afirmacion`: True solo para un 'sí' claro (con tildes/mayúsculas); False para negación,
    comandos o vacío (solo un sí mueve plata);
  - `es_negacion`: True solo para negación explícita ('no'/'cancela'/'cancelar');
  - `_serializar`/`_deserializar`: round-trip exacto de un `Pendiente` (ToolCall con arguments dict).
"""
from decimal import Decimal

import pytest

from ai.confirmacion import (
    Pendiente,
    _deserializar,
    _json_default,
    _serializar,
    es_afirmacion,
    es_negacion,
)
from ai.tools import POR_NOMBRE
from core.llm.base import ToolCall


def test_es_afirmacion_positivos():
    for t in ["sí", "si", "SÍ", "Dale", "confirmo", "ok", "Listo", "hágale", "hagale"]:
        assert es_afirmacion(t) is True, t


def test_es_afirmacion_negativos():
    for t in ["no", "cancela", "2 martillos", "mejor no", "   ", ""]:
        assert es_afirmacion(t) is False, t


def test_es_negacion_positivos():
    for t in ["no", "No", "NO", "cancela", "Cancelar"]:
        assert es_negacion(t) is True, t


def test_es_negacion_negativos():
    for t in ["sí", "dale", "ok", "2 martillos", ""]:
        assert es_negacion(t) is False, t


def test_serializar_deserializar_round_trip():
    pendiente = Pendiente(
        tool_call=ToolCall(
            id="call_1", name="registrar_gasto",
            arguments={"monto": 15000, "categoria": "transporte"},
        ),
        idempotency_key="idem-abc-123",
    )

    crudo = _serializar(pendiente)
    assert isinstance(crudo, str)

    recuperado = _deserializar(crudo)
    assert recuperado == pendiente
    assert recuperado.tool_call.arguments == {"monto": 15000, "categoria": "transporte"}
    assert recuperado.idempotency_key == "idem-abc-123"


# --- Decimal en el estado pendiente (regresión: TypeError en prod al guardar en Redis) ---

def _pendiente_venta(cantidad: Decimal) -> Pendiente:
    return Pendiente(
        tool_call=ToolCall(
            id="bypass:7", name="registrar_venta",
            arguments={"items": [{"producto_id": 7, "cantidad": cantidad}], "metodo_pago": "efectivo"},
        ),
        idempotency_key="idem-dec",
    )


def test_serializar_maneja_decimal_en_la_cantidad():
    # Antes reventaba con TypeError: Object of type Decimal is not JSON serializable.
    crudo = _serializar(_pendiente_venta(Decimal("0.5")))
    assert '"0.5"' in crudo                                  # Decimal → str en el JSON
    recuperado = _deserializar(crudo)
    assert recuperado.tool_call.arguments["items"][0]["cantidad"] == "0.5"   # round-trip por valor


def test_toolcall_deserializado_ejecuta_con_cantidad_decimal():
    # El args_model (Pydantic) de la herramienta coacciona la cantidad str → Decimal al ejecutar,
    # que es justo lo que hace `dispatcher.ejecutar` (tool.args_model(**tool_call.arguments)).
    tc = _deserializar(_serializar(_pendiente_venta(Decimal("1.5")))).tool_call
    args = POR_NOMBRE[tc.name].args_model(**tc.arguments)
    assert args.items[0].cantidad == Decimal("1.5")
    assert isinstance(args.items[0].cantidad, Decimal)


def test_json_default_relanza_para_tipos_no_serializables():
    # No es un default=str ciego: un tipo que no es Decimal re-lanza TypeError (no enmascara bugs).
    with pytest.raises(TypeError):
        _json_default({1, 2, 3})
