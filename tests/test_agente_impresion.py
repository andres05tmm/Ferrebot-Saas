"""Agente local de impresión + fallback (R2 Restaurante Ronda 2, ADR 0033 D4–D6).

Invariantes (test-primero): el ciclo completo con impresora FALSA (trabajo → ESC/POS correcto →
ack → estado `impreso`); un CORTE de conexión a mitad de trabajo NO duplica la impresión al
reconectar (registro local + re-entrega del backend); el token de dispositivo autentica la
superficie de impresión y un token revocado deja de valer. El render es puro (Dummy printer).
"""
import asyncio
import uuid
from decimal import Decimal

import httpx
import pytest
from alembic import command
from alembic.config import Config
from escpos.printer import Dummy
from fastapi import FastAPI
from httpx import ASGITransport
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from core.config import get_settings
from core.config.timezone import now_co
from core.db.urls import tenant_url, to_async
from tests.conftest import create_database, drop_database


@pytest.fixture
async def control_engine(monkeypatch):
    """Control DB efímero migrado a head (patrón tests/test_bot_repos_control.py)."""
    name = f"test_control_imp_{uuid.uuid4().hex[:12]}"
    url = tenant_url(get_settings().tenants_direct_url_base, name)
    monkeypatch.setenv("CONTROL_DATABASE_URL", url)
    get_settings.cache_clear()
    create_database(name)
    engine = create_async_engine(
        to_async(url), poolclass=NullPool, connect_args={"statement_cache_size": 0}
    )
    try:
        command.upgrade(Config("migrations/control/alembic.ini"), "head")
        yield engine
    finally:
        await engine.dispose()
        get_settings.cache_clear()
        drop_database(name)
from core.tenancy.context import ResolvedTenant
from modules.impresion.render import render_comanda, render_comprobante, render_precuenta
from modules.pedidos.repository import SqlPedidosRepository
from modules.pedidos.service import ItemPedido, PedidosService
from tools.agente_impresion.agente import AgenteImpresion, RegistroImpresos
from tools.agente_impresion.config import ConfigAgente, Impresora

# ─── render ESC/POS (puro, sin BD) ──────────────────────────────────────────────────────────────

PAYLOAD_COMANDA = {
    "tipo": "comanda", "zona": "parrilla", "origen": "mesa", "cliente": "Mesa 4",
    "notas": "afán",
    "items": [
        {"nombre": "Hamburguesa", "cantidad": "2",
         "modificadores": [{"grupo": "Extras", "opcion": "sin cebolla", "delta_precio": "0.00"}]},
    ],
}


def test_render_comanda_80_y_58():
    for ancho in (80, 58):
        d = Dummy()
        render_comanda(d, PAYLOAD_COMANDA, ancho=ancho)
        salida = d.output
        assert b"Hamburguesa" in salida
        assert b"SIN CEBOLLA" in salida        # el modificador va DESTACADO en mayúsculas
        assert b"Mesa 4" in salida and b"NOTA" in salida
        assert b"\x1dV" in salida              # corte (GS V)


def test_render_precuenta_propina_ley_1935():
    d = Dummy()
    payload = {
        "tipo": "precuenta", "cliente": "Mesa 1", "total": "52000", "subtotal": "52000",
        "items": [{"nombre": "Churrasco", "cantidad": "1", "subtotal": "52000",
                   "precio_unitario": "52000", "modificadores": []}],
    }
    render_precuenta(d, payload, ancho=80, negocio="Brasa")
    texto = d.output.decode("cp437", errors="ignore")
    assert "TOTAL" in texto and "$52.000" in texto
    assert "Precios incluyen INC 8%" in texto
    # Ley 1935: sugerida 10%, VOLUNTARIA, jamás sumada (el total sigue siendo el total).
    assert "$5.200" in texto and "VOLUNTARIA" in texto
    assert "$57.200" not in texto   # total + propina NO aparece por ningún lado
    assert "no fiscal" in texto


def test_render_comprobante():
    d = Dummy()
    payload = {
        "tipo": "comprobante", "consecutivo": 42, "fecha": "2026-07-24", "metodo_pago": "efectivo",
        "total": "60000", "subtotal": "60000", "impuestos": "0",
        "items": [{"nombre": "Bandeja paisa", "cantidad": "2", "subtotal": "60000",
                   "precio_unitario": "30000"}],
    }
    render_comprobante(d, payload, ancho=80, negocio="Brasa")
    texto = d.output.decode("cp437", errors="ignore")
    assert "Bandeja paisa" in texto and "$60.000" in texto and "efectivo" in texto
    assert "no fiscal" in texto


# ─── dispositivos (control DB efímero) ──────────────────────────────────────────────────────────


async def test_dispositivo_emitir_validar_revocar(control_engine):
    from modules.impresion.dispositivos import (
        emitir_dispositivo,
        revocar_dispositivo,
        validar_token,
    )

    async with AsyncSession(control_engine, expire_on_commit=False) as cs:
        empresa_id = (
            await cs.execute(
                text(
                    "INSERT INTO empresas (slug, nombre, nit, estado) "
                    "VALUES ('resto-a', 'Resto A', '900123456-1', 'activa') RETURNING id"
                )
            )
        ).scalar_one()
        dispositivo_id, token = await emitir_dispositivo(cs, empresa_id, "Caja cocina")
        assert token.startswith("imp_") and len(token) > 20
        # El token vale para SU empresa y solo para ella.
        assert await validar_token(cs, empresa_id, token) == dispositivo_id
        assert await validar_token(cs, empresa_id + 1, token) is None
        assert await validar_token(cs, empresa_id, "imp_falso") is None
        # Revocado → deja de valer. Revocar acotado a la empresa.
        assert await revocar_dispositivo(cs, empresa_id + 1, dispositivo_id) is False
        assert await revocar_dispositivo(cs, empresa_id, dispositivo_id) is True
        assert await validar_token(cs, empresa_id, token) is None
        await cs.commit()


# ─── ciclo del agente contra la app ASGI real ───────────────────────────────────────────────────


async def _seed_y_confirmar(engine) -> int:
    async with AsyncSession(engine, expire_on_commit=False) as s:
        await s.execute(
            text(
                "INSERT INTO pedido_config (activo, hora_apertura, hora_cierre, minimo_pedido, "
                "tiempo_estimado_min, costo_domicilio_default) VALUES (true, '00:00', '23:59', 0, 45, 0)"
            )
        )
        zona = (
            await s.execute(
                text("INSERT INTO comanda_zonas (nombre, activo) VALUES ('parrilla', true) RETURNING id")
            )
        ).scalar_one()
        pid = (
            await s.execute(
                text(
                    "INSERT INTO productos (nombre, unidad_medida, precio_venta, iva, "
                    "permite_fraccion, activo, zona_comanda_id) "
                    "VALUES ('Hamburguesa', 'unidad', 18000, 0, false, true, :z) RETURNING id"
                ),
                {"z": zona},
            )
        ).scalar_one()
        await s.execute(
            text("INSERT INTO inventario (producto_id, stock_actual, stock_minimo) VALUES (:p, 50, 0)"),
            {"p": pid},
        )
        await s.commit()
        svc = PedidosService(SqlPedidosRepository(s))
        await svc.armar_pedido(
            "3001112233", [ItemPedido(producto="hamburguesa", cantidad=Decimal("1"))], ahora=now_co()
        )
        pedido, _ = await svc.confirmar_pedido(
            "3001112233", direccion="Cra 1", metodo_pago="efectivo"
        )
        await s.commit()
        return pedido.id


def _app_impresion(tenant, *, token_valido: str) -> FastAPI:
    """App real del router de impresión: auth por X-Device-Token (validador fake), tenant fijado."""
    from core.auth.features import get_capacidades
    from core.db.session import get_tenant_db
    from modules.impresion.router import get_validador_dispositivo
    from modules.impresion.router import router as impresion_router

    async def _db():
        async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise

    app = FastAPI()

    @app.middleware("http")
    async def _tenant_state(request, call_next):
        request.state.tenant = ResolvedTenant(
            id=7, slug="resto-a", nombre="Resto A", estado="activa",
            db_name=tenant.name, connection_url=tenant.url,
        )
        return await call_next(request)

    app.include_router(impresion_router, prefix="/api/v1")
    app.dependency_overrides[get_tenant_db] = _db
    app.dependency_overrides[get_capacidades] = lambda: frozenset({"impresion", "ventas"})

    def _validador():
        async def _validar(empresa_id: int, token: str):
            return 1 if (empresa_id == 7 and token == token_valido) else None

        return _validar

    app.dependency_overrides[get_validador_dispositivo] = _validador
    return app


class _PuenteHttp:
    """Cliente sync para el agente sobre el AsyncClient ASGI (el agente corre en un hilo)."""

    def __init__(self, cliente: httpx.AsyncClient, loop: asyncio.AbstractEventLoop) -> None:
        self._c = cliente
        self._loop = loop

    def get(self, url: str, headers: dict) -> httpx.Response:
        return asyncio.run_coroutine_threadsafe(self._c.get(url, headers=headers), self._loop).result()

    def post(self, url: str, headers: dict, json: dict) -> httpx.Response:
        return asyncio.run_coroutine_threadsafe(
            self._c.post(url, headers=headers, json=json), self._loop
        ).result()


def _config(tmp_path) -> ConfigAgente:
    return ConfigAgente(
        url="http://t", slug="resto-a", token="imp_secreto",
        impresoras={"*": Impresora(tipo="dummy", destino="", ancho=80)},
    )


async def test_ciclo_completo_impresora_falsa_y_token(tenant, tmp_path):
    pedido_id = await _seed_y_confirmar(tenant.engine)
    app = _app_impresion(tenant, token_valido="imp_secreto")
    salidas: list[Dummy] = []

    def _fabrica(_imp):
        d = Dummy()
        salidas.append(d)
        return d

    loop = asyncio.get_running_loop()
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://t"
    ) as c:
        # Token de dispositivo INVÁLIDO → 401 (la cola no se entrega).
        r = await c.get("/api/v1/impresion/cola", headers={"X-Device-Token": "imp_malo"})
        assert r.status_code == 401

        agente = AgenteImpresion(
            _config(tmp_path), RegistroImpresos(tmp_path / "impresos.txt"),
            http=_PuenteHttp(c, loop), fabrica_impresora=_fabrica,
        )
        n = await asyncio.to_thread(agente.ciclo)
        assert n == 1
        # ESC/POS correcto en la impresora falsa.
        assert b"Hamburguesa" in salidas[0].output and b"\x1dV" in salidas[0].output

    async with AsyncSession(tenant.engine) as s:
        fila = (
            await s.execute(
                text(
                    "SELECT estado, impreso_en FROM trabajos_impresion WHERE pedido_id = :p"
                ),
                {"p": pedido_id},
            )
        ).one()
        assert fila.estado == "impreso" and fila.impreso_en is not None


async def test_corte_de_conexion_no_duplica_impresion(tenant, tmp_path):
    pedido_id = await _seed_y_confirmar(tenant.engine)
    app = _app_impresion(tenant, token_valido="imp_secreto")
    salidas: list[Dummy] = []

    def _fabrica(_imp):
        d = Dummy()
        salidas.append(d)
        return d

    loop = asyncio.get_running_loop()
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://t"
    ) as c:
        puente = _PuenteHttp(c, loop)

        class _CorteEnAck(_PuenteHttp):
            def post(self, url: str, headers: dict, json: dict) -> httpx.Response:
                raise httpx.ConnectError("corte a mitad de trabajo")   # imprimió, no alcanzó a ackear

        corte = _CorteEnAck(c, loop)
        registro = RegistroImpresos(tmp_path / "impresos.txt")

        # 1) El agente imprime y el CORTE llega antes del ack.
        agente = AgenteImpresion(_config(tmp_path), registro, http=corte, fabrica_impresora=_fabrica)
        with pytest.raises(httpx.ConnectError):
            await asyncio.to_thread(agente.ciclo)
        assert len(salidas) == 1   # el papel salió una vez

        # 2) El backend re-entrega (venció la entrega) y el agente "reinicia" (nuevo proceso, mismo
        #    registro persistido): NO vuelve a imprimir — solo ackea.
        async with AsyncSession(tenant.engine) as s:
            await s.execute(
                text(
                    "UPDATE trabajos_impresion SET entregado_en = now() - interval '10 minutes' "
                    "WHERE pedido_id = :p"
                ),
                {"p": pedido_id},
            )
            await s.commit()
        agente2 = AgenteImpresion(
            _config(tmp_path), RegistroImpresos(tmp_path / "impresos.txt"),
            http=puente, fabrica_impresora=_fabrica,
        )
        n = await asyncio.to_thread(agente2.ciclo)
        assert n == 1
        assert len(salidas) == 1   # ✅ CERO impresiones nuevas: el registro local lo paró

    async with AsyncSession(tenant.engine) as s:
        estado = (
            await s.execute(
                text("SELECT estado FROM trabajos_impresion WHERE pedido_id = :p"), {"p": pedido_id}
            )
        ).scalar_one()
        assert estado == "impreso"
