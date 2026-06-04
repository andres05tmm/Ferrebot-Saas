"""CR-2 — dominio puro de confirmación: clasificación de texto + (de)serialización (sin Redis).

Pin del contrato:
  - `es_afirmacion`: True solo para un 'sí' claro (con tildes/mayúsculas); False para negación,
    comandos o vacío (solo un sí mueve plata);
  - `es_negacion`: True solo para negación explícita ('no'/'cancela'/'cancelar');
  - `_serializar`/`_deserializar`: round-trip exacto de un `Pendiente` (ToolCall con arguments dict).
"""
from ai.confirmacion import Pendiente, _deserializar, _serializar, es_afirmacion, es_negacion
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
