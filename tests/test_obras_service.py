"""Ciclo de vida de la obra: transiciones de estado válidas e inválidas (lógica real del servicio).

Prueba `ObrasService.cambiar_estado` contra un repo FALSO (sin BD): las transiciones permitidas se
aplican y las imposibles se rechazan con `TransicionEstadoInvalida` (nada de estados imposibles). Es el
núcleo de dominio del módulo, por eso se testea aparte del wiring HTTP.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from modules.obra.errors import ObraInexistente, TransicionEstadoInvalida
from modules.obra.service import ObrasService


class _FakeObrasRepo:
    """Repo en memoria: guarda una obra por id y persiste el cambio de estado sin validar."""

    def __init__(self, obras: dict[int, object]) -> None:
        self._obras = obras

    async def obtener(self, obra_id: int):
        return self._obras.get(obra_id)

    async def cambiar_estado(self, obra, nuevo_estado: str):
        obra.estado = nuevo_estado
        return obra


def _servicio(estado: str) -> ObrasService:
    obra = SimpleNamespace(id=1, estado=estado)
    return ObrasService(_FakeObrasRepo({1: obra}))


# (estado_actual, destino) permitidos por el ciclo de vida v1.
_VALIDAS = [
    ("PLANIFICADA", "EN_EJECUCION"),
    ("PLANIFICADA", "SUSPENDIDA"),
    ("EN_EJECUCION", "SUSPENDIDA"),
    ("EN_EJECUCION", "FINALIZADA"),
    ("SUSPENDIDA", "EN_EJECUCION"),
    ("SUSPENDIDA", "FINALIZADA"),
    ("FINALIZADA", "LIQUIDADA"),
]

# Muestras de transiciones IMPOSIBLES que deben rechazarse.
_INVALIDAS = [
    ("PLANIFICADA", "FINALIZADA"),   # no se puede finalizar sin ejecutar
    ("PLANIFICADA", "LIQUIDADA"),    # ni liquidar de una
    ("EN_EJECUCION", "LIQUIDADA"),   # liquidar exige pasar por FINALIZADA
    ("LIQUIDADA", "EN_EJECUCION"),   # LIQUIDADA es terminal
    ("FINALIZADA", "EN_EJECUCION"),  # no se reabre una obra finalizada
    ("EN_EJECUCION", "EN_EJECUCION"),  # no-op no es una transición válida
]


@pytest.mark.parametrize("actual,destino", _VALIDAS)
async def test_transicion_valida_se_aplica(actual: str, destino: str):
    obra = await _servicio(actual).cambiar_estado(1, destino)
    assert obra.estado == destino


@pytest.mark.parametrize("actual,destino", _INVALIDAS)
async def test_transicion_invalida_se_rechaza(actual: str, destino: str):
    servicio = _servicio(actual)
    with pytest.raises(TransicionEstadoInvalida):
        await servicio.cambiar_estado(1, destino)


async def test_cambiar_estado_obra_inexistente_404():
    servicio = ObrasService(_FakeObrasRepo({}))
    with pytest.raises(ObraInexistente):
        await servicio.cambiar_estado(999, "EN_EJECUCION")
