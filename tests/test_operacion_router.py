"""Wiring HTTP de la operación de máquina en vivo (patrón `test_maquinaria_mantenimientos.py`).

Servicio FAKE (sin BD): forma de la respuesta, mapeo de errores de dominio a HTTP (404/409), roles
(operar = vendedor; anular = admin) y el gate de la capacidad `maquinaria`. La lógica y los invariantes
van en `test_operacion_maquina.py` (integración contra Postgres)."""
from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace

import httpx
from fastapi import FastAPI
from httpx import ASGITransport

from core.auth import Principal, get_current_user
from core.auth.features import get_capacidades
from modules.maquinaria.errors import (
    MaquinaInexistente,
    SesionInexistente,
    SesionNoAbierta,
    SesionYaAbierta,
    SinAsignacionActiva,
)
from modules.maquinaria.operacion_router import get_operacion_service, router

_AHORA = datetime(2026, 7, 11, 8, 0, 0)


def _sesion(**over) -> SimpleNamespace:
    base = dict(
        id=1, maquina_id=1, obra_id=5, asignacion_id=9, fecha=date(2026, 7, 11), estado="ABIERTA",
        iniciada_en=_AHORA, finalizada_en=None, registro_horas_id=None, notas=None,
    )
    base.update(over)
    return SimpleNamespace(**base)


def _resultado(**over) -> SimpleNamespace:
    base = dict(
        registro_id=3, maquina_id=1, obra_id=5, fecha=date(2026, 7, 11),
        horas_trabajadas=Decimal("9"), horas_facturables=Decimal("9"), minimo_cubierto=True,
        precio_hora=Decimal("160000"), ingreso=Decimal("1440000"), origen_registro="MANUAL",
        replay=False, turnos=[],
    )
    base.update(over)
    return SimpleNamespace(**base)


class _FakeOperacion:
    """Fake de `OperacionMaquinaService`: devuelve datos fijos o lanza el error de dominio inyectado."""

    def __init__(self, *, error: Exception | None = None) -> None:
        self._error = error
        self.ajustes_recibidos: dict | None = None

    async def iniciar(self, maquina_id, *, obra_id=None, operador_id=None):
        if self._error:
            raise self._error
        return _sesion(maquina_id=maquina_id, obra_id=obra_id or 5)

    async def rotar(self, sesion_id, operador_id):
        if self._error:
            raise self._error
        return _sesion(id=sesion_id)

    async def finalizar(self, sesion_id, ajustes=None):
        if self._error:
            raise self._error
        self.ajustes_recibidos = ajustes
        return _resultado()

    async def anular(self, sesion_id):
        if self._error:
            raise self._error
        return _sesion(id=sesion_id, estado="ANULADA")

    async def detalle(self, sesion_id):
        if self._error:
            raise self._error
        return {
            "sesion": _sesion(id=sesion_id),
            "tramos": [
                dict(
                    id=10, operador_id=7, operador="Juan Pérez", iniciado_en=_AHORA,
                    finalizado_en=None, horas_propuestas=Decimal("2.5"),
                )
            ],
        }

    async def tablero(self):
        return [
            dict(
                sesion_id=1, maquina_id=1, maquina="Vibrocompactador", obra_id=5, obra="Vía La Paz",
                iniciada_en=_AHORA, operador_id=7, operador="Juan Pérez", tramo_desde=_AHORA,
            )
        ]


def _app(service, *, rol="vendedor", caps=frozenset({"maquinaria"})) -> FastAPI:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_operacion_service] = lambda: service
    app.dependency_overrides[get_current_user] = lambda: Principal(user_id=1, tenant="pim", rol=rol)
    app.dependency_overrides[get_capacidades] = lambda: caps
    return app


def _cliente(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://t"
    )


# --------------------------- iniciar ---------------------------

async def test_iniciar_201_forma():
    async with _cliente(_app(_FakeOperacion())) as c:
        r = await c.post("/api/v1/maquinas/1/operacion/iniciar", json={"operador_id": 7})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["estado"] == "ABIERTA" and body["maquina_id"] == 1


async def test_iniciar_409_sesion_ya_abierta():
    async with _cliente(_app(_FakeOperacion(error=SesionYaAbierta(1)))) as c:
        r = await c.post("/api/v1/maquinas/1/operacion/iniciar", json={})
    assert r.status_code == 409, r.text


async def test_iniciar_409_sin_asignacion():
    async with _cliente(_app(_FakeOperacion(error=SinAsignacionActiva(1, 0, "2026-07-11")))) as c:
        r = await c.post("/api/v1/maquinas/1/operacion/iniciar", json={})
    assert r.status_code == 409, r.text


async def test_iniciar_404_maquina_inexistente():
    async with _cliente(_app(_FakeOperacion(error=MaquinaInexistente(999)))) as c:
        r = await c.post("/api/v1/maquinas/999/operacion/iniciar", json={})
    assert r.status_code == 404, r.text


# --------------------------- rotar ---------------------------

async def test_rotar_200():
    async with _cliente(_app(_FakeOperacion())) as c:
        r = await c.post("/api/v1/operacion/1/rotar", json={"operador_id": 8})
    assert r.status_code == 200, r.text


async def test_rotar_409_no_abierta():
    async with _cliente(_app(_FakeOperacion(error=SesionNoAbierta(1, "FINALIZADA")))) as c:
        r = await c.post("/api/v1/operacion/1/rotar", json={})
    assert r.status_code == 409, r.text


async def test_rotar_404_inexistente():
    async with _cliente(_app(_FakeOperacion(error=SesionInexistente(9)))) as c:
        r = await c.post("/api/v1/operacion/9/rotar", json={})
    assert r.status_code == 404, r.text


# --------------------------- finalizar ---------------------------

async def test_finalizar_200_forma_y_pasa_ajustes():
    fake = _FakeOperacion()
    async with _cliente(_app(fake)) as c:
        r = await c.post(
            "/api/v1/operacion/1/finalizar",
            json={"ajustes": [{"tramo_id": 10, "horas": "4"}, {"tramo_id": 11, "horas": "5"}]},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["horas_facturables"] == "9" and body["replay"] is False
    assert fake.ajustes_recibidos == {10: Decimal("4"), 11: Decimal("5")}   # router arma el dict


async def test_finalizar_sin_ajustes_pasa_none():
    fake = _FakeOperacion()
    async with _cliente(_app(fake)) as c:
        r = await c.post("/api/v1/operacion/1/finalizar", json={})
    assert r.status_code == 200, r.text
    assert fake.ajustes_recibidos is None   # sin ajustes → None (usa el reloj)


async def test_finalizar_409_no_abierta():
    async with _cliente(_app(_FakeOperacion(error=SesionNoAbierta(1, "ANULADA")))) as c:
        r = await c.post("/api/v1/operacion/1/finalizar", json={})
    assert r.status_code == 409, r.text


# --------------------------- anular (admin) ---------------------------

async def test_anular_403_vendedor():
    async with _cliente(_app(_FakeOperacion(), rol="vendedor")) as c:
        r = await c.post("/api/v1/operacion/1/anular")
    assert r.status_code == 403, r.text


async def test_anular_200_admin():
    async with _cliente(_app(_FakeOperacion(), rol="admin")) as c:
        r = await c.post("/api/v1/operacion/1/anular")
    assert r.status_code == 200, r.text
    assert r.json()["estado"] == "ANULADA"


# --------------------------- tablero + gate ---------------------------

async def test_tablero_200_forma():
    async with _cliente(_app(_FakeOperacion())) as c:
        r = await c.get("/api/v1/operacion/tablero")
    assert r.status_code == 200, r.text
    fila = r.json()[0]
    assert fila["maquina"] == "Vibrocompactador" and fila["operador"] == "Juan Pérez"


async def test_obtener_detalle_200_forma():
    async with _cliente(_app(_FakeOperacion())) as c:
        r = await c.get("/api/v1/operacion/1")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == 1 and len(body["tramos"]) == 1
    assert body["tramos"][0]["operador"] == "Juan Pérez"
    assert body["tramos"][0]["horas_propuestas"] == "2.5"


async def test_obtener_detalle_404():
    async with _cliente(_app(_FakeOperacion(error=SesionInexistente(9)))) as c:
        r = await c.get("/api/v1/operacion/9")
    assert r.status_code == 404, r.text


async def test_gateado_por_capacidad_maquinaria():
    async with _cliente(_app(_FakeOperacion(), caps=frozenset())) as c:
        r = await c.get("/api/v1/operacion/tablero")
    assert r.status_code == 404, r.text
