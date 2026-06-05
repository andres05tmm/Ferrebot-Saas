"""Entregable 4 — contrato de los side-writes: cada uno se aísla en un SAVEPOINT (begin_nested).

Unitario con sesión FAKE: solo verifica que el repo ABRE un savepoint alrededor del write. El pin
de comportamiento real (la venta sobrevive al fallo) vive en tests/test_turno_transaccional.py.
Contra el código actual (sin savepoints) estos tests FALLAN; con `session.begin_nested()` PASAN.
"""
from core.config.timezone import today_co
from modules.memoria.repository import (
    SqlAudioLogsRepository,
    SqlCostosRepository,
    SqlMemoriaRepository,
)


class _FakeNested:
    def __init__(self, registro: list[str]) -> None:
        self._r = registro

    async def __aenter__(self) -> "_FakeNested":
        self._r.append("enter")
        return self

    async def __aexit__(self, *exc) -> bool:
        self._r.append("exit")
        return False


class FakeSession:
    """Sesión falsa que registra si el repo abre un savepoint (begin_nested)."""

    def __init__(self) -> None:
        self.savepoints: list[str] = []
        self.added: list[object] = []
        self.flushes = 0
        self.executes = 0

    def begin_nested(self) -> _FakeNested:
        self.savepoints.append("begin")
        return _FakeNested(self.savepoints)

    def add(self, obj: object) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        self.flushes += 1

    async def execute(self, stmt) -> None:
        self.executes += 1


async def test_guardar_mensaje_usa_savepoint():
    s = FakeSession()
    await SqlMemoriaRepository(s).guardar_mensaje(555, "user", "hola")
    assert "begin" in s.savepoints


async def test_upsert_entidad_usa_savepoint():
    s = FakeSession()
    await SqlMemoriaRepository(s).upsert_entidad("ultimo_cliente", "555", {"id": 1, "nombre": "Ana"})
    assert "begin" in s.savepoints


async def test_acumular_usa_savepoint():
    s = FakeSession()
    await SqlCostosRepository(s).acumular(fecha=today_co(), modelo="m", tokens_in=1, tokens_out=1)
    assert "begin" in s.savepoints


async def test_registrar_audio_log_usa_savepoint():
    s = FakeSession()
    await SqlAudioLogsRepository(s).registrar(555, "2 martillos", 3)
    assert "begin" in s.savepoints
