"""Gastos del vertical construcción (Fase 3, spec 09): imputación a obra + bandeja de revisión del bot.

Un gasto imputado a obra es un gasto NORMAL de caja con `obra_id`: sigue posteando su egreso (invariante
"nada mueve caja sin movimiento"). Los gastos que entran por el bot con baja confianza quedan con
`requiere_revision = true` y aparecen en la bandeja `/gastos/revision`; aprobarlos baja el flag.

Cubre: egreso del gasto imputado a obra, la bandeja lista solo pendientes, aprobar (idempotente) baja el
flag, y aislamiento multi-tenant de la bandeja.
"""
from decimal import Decimal

import httpx
from fastapi import FastAPI
from httpx import ASGITransport
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth import Principal, get_current_user
from core.auth.features import get_capacidades
from core.db.session import get_tenant_db
from modules.caja.repository import SqlCajaRepository
from modules.caja.router import gastos_router
from modules.caja.service import CajaService

# `gastos.obra_id`/`gastos.maquina_id` son FKs a `obras`/`maquinas` (tenant 0048): registra esos modelos
# en la metadata del ORM para que las FKs resuelvan al correr este archivo en aislamiento.
import modules.obra.models  # noqa: E402,F401  (side-effect: registra la tabla `obras`)
import modules.maquinaria.models  # noqa: E402,F401  (side-effect: registra la tabla `maquinas`)


def _svc(s: AsyncSession) -> CajaService:
    return CajaService(SqlCajaRepository(s))


async def _usuario(s: AsyncSession, *, rol: str = "vendedor") -> int:
    return (
        await s.execute(text("INSERT INTO usuarios (nombre, rol) VALUES ('V', :r) RETURNING id"), {"r": rol})
    ).scalar_one()


async def _seed_obra(s: AsyncSession) -> int:
    cid = (
        await s.execute(text("INSERT INTO clientes (nombre, saldo_fiado) VALUES ('Alcaldía', 0) RETURNING id"))
    ).scalar_one()
    return (
        await s.execute(text("INSERT INTO obras (cliente_id, nombre) VALUES (:c, 'Vía') RETURNING id"), {"c": cid})
    ).scalar_one()


# ---- Imputación a obra: sigue moviendo caja (invariante) -------------------
async def test_gasto_imputado_a_obra_postea_egreso_y_persiste_obra_id(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid = await _usuario(s)
        obra_id = await _seed_obra(s)
        await _svc(s).abrir(usuario_id=uid, saldo_inicial=Decimal("0"))
        res = await _svc(s).registrar_gasto(
            usuario_id=uid, categoria="mantenimiento", monto=Decimal("80000"),
            concepto="repuesto retroexcavadora", obra_id=obra_id,
            categoria_gasto="REPUESTOS", metodo_pago="TRANSFERENCIA_BANCOLOMBIA",
            numero_referencia="M-9911",
        )
        await s.commit()

    assert res.gasto.obra_id == obra_id
    assert res.gasto.categoria_gasto == "REPUESTOS"
    assert res.gasto.origen_registro == "MANUAL"          # server_default cuando no lo da el caller
    assert res.gasto.requiere_revision is False
    # Invariante de caja: el gasto (aunque imputado a obra) posteó SU egreso.
    async with AsyncSession(tenant.engine) as s:
        egresos = (
            await s.execute(text("SELECT count(*) FROM caja_movimientos WHERE tipo='egreso'"))
        ).scalar_one()
        obra_col = (await s.execute(text("SELECT obra_id FROM gastos"))).scalar_one()
    assert egresos == 1
    assert obra_col == obra_id


# ---- Bandeja de revisión ---------------------------------------------------
async def test_gasto_del_bot_baja_confianza_marca_revision(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid = await _usuario(s)
        await _svc(s).abrir(usuario_id=uid, saldo_inicial=Decimal("0"))
        res = await _svc(s).registrar_gasto(
            usuario_id=uid, categoria="otros", monto=Decimal("35000"), concepto="almuerzos cuadrilla",
            categoria_gasto="ALMUERZOS", origen_registro="TELEGRAM_BOT",
            telegram_user_id="55501", telegram_message_id="7788", requiere_revision=True,
        )
        await s.commit()
    assert res.gasto.origen_registro == "TELEGRAM_BOT"
    assert res.gasto.requiere_revision is True
    assert res.gasto.telegram_user_id == "55501"


async def test_bandeja_lista_solo_pendientes_y_aprobar_baja_flag(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid = await _usuario(s)
        await _svc(s).abrir(usuario_id=uid, saldo_inicial=Decimal("0"))
        # Uno del bot pendiente de revisión + uno manual limpio.
        pendiente = await _svc(s).registrar_gasto(
            usuario_id=uid, categoria="otros", monto=Decimal("35000"), concepto="del bot",
            origen_registro="TELEGRAM_BOT", requiere_revision=True,
        )
        await _svc(s).registrar_gasto(
            usuario_id=uid, categoria="transporte", monto=Decimal("15000"), concepto="taxi",
        )
        await s.commit()
        gid = pendiente.gasto.id

    async with AsyncSession(tenant.engine) as s:
        bandeja = await _svc(s).listar_revision()
    assert [g.id for g in bandeja] == [gid]                # solo el pendiente, no el manual limpio

    # Aprobar baja el flag; es idempotente (aprobar dos veces no cambia el resultado).
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        g1 = await _svc(s).aprobar_gasto(gid)
        await s.commit()
        assert g1.requiere_revision is False
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        g2 = await _svc(s).aprobar_gasto(gid)              # replay: sigue en False
        await s.commit()
        assert g2.requiere_revision is False

    async with AsyncSession(tenant.engine) as s:
        assert await _svc(s).listar_revision() == []       # ya no hay pendientes


async def test_aprobar_gasto_inexistente_falla(tenant):
    from modules.caja.errors import GastoInexistente
    import pytest

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        with pytest.raises(GastoInexistente):
            await _svc(s).aprobar_gasto(999999)


# ---- Aislamiento multi-tenant de la bandeja --------------------------------
async def test_aislamiento_bandeja_revision_entre_tenants(tenant_factory):
    a = await tenant_factory()
    b = await tenant_factory()
    async with AsyncSession(a.engine, expire_on_commit=False) as s:
        uid = await _usuario(s)
        await _svc(s).abrir(usuario_id=uid, saldo_inicial=Decimal("0"))
        await _svc(s).registrar_gasto(
            usuario_id=uid, categoria="otros", monto=Decimal("1000"), concepto="del bot",
            origen_registro="TELEGRAM_BOT", requiere_revision=True,
        )
        await s.commit()

    async with AsyncSession(a.engine) as s:
        assert len(await _svc(s).listar_revision()) == 1
    async with AsyncSession(b.engine) as s:
        assert await _svc(s).listar_revision() == []       # la empresa B no ve la bandeja de A


# ---- HTTP: bandeja + aprobar (RBAC admin) ----------------------------------
def _app(tenant, *, user_id: int, rol: str) -> FastAPI:
    app = FastAPI()
    app.include_router(gastos_router, prefix="/api/v1")

    async def _db():
        async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise

    app.dependency_overrides[get_current_user] = lambda: Principal(user_id=user_id, tenant="pim", rol=rol)
    app.dependency_overrides[get_tenant_db] = _db
    app.dependency_overrides[get_capacidades] = lambda: frozenset({"caja"})
    return app


# ---- Rechazo de la bandeja (F2.2): reversa por movimiento INVERSO, nunca delete -------------------
# INVARIANTE (carve-out, test-primero): el gasto ya posteó su egreso al crearse; rechazarlo debe
# insertar un INGRESO de caja por el monto exacto (el arqueo vuelve al valor pre-gasto) y marcar
# `anulado_en` (idempotencia). Jamás se borra ni edita un movimiento de caja existente.

async def _gasto_bandeja(s: AsyncSession, uid: int, *, monto="35000", obra_id=None):
    res = await _svc(s).registrar_gasto(
        usuario_id=uid, categoria="otros", monto=Decimal(monto), concepto="del bot",
        origen_registro="TELEGRAM_BOT", requiere_revision=True, obra_id=obra_id,
    )
    return res.gasto


async def test_rechazar_postea_ingreso_inverso_y_el_arqueo_vuelve_al_pre_gasto(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid = await _usuario(s)
        await _svc(s).abrir(usuario_id=uid, saldo_inicial=Decimal("100000"))
        gasto = await _gasto_bandeja(s, uid, monto="35000")
        await s.commit()
        gid = gasto.id

    # Pre-condición: el gasto bajó el esperado a 65.000.
    async with AsyncSession(tenant.engine) as s:
        assert (await _svc(s).arqueo(uid)).saldo_esperado == Decimal("65000.00")

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        rechazado = await _svc(s).rechazar_gasto(gid, usuario_id=uid, motivo="monto ilegible")
        await s.commit()
    assert rechazado.anulado_en is not None
    assert rechazado.motivo_rechazo == "monto ilegible"
    assert rechazado.requiere_revision is False

    async with AsyncSession(tenant.engine) as s:
        # La reversa es un movimiento INVERSO (ingreso), no un delete del egreso original.
        filas = (
            await s.execute(text("SELECT tipo, monto FROM caja_movimientos ORDER BY id"))
        ).all()
        assert [(f[0], f[1]) for f in filas] == [("egreso", Decimal("35000.00")), ("ingreso", Decimal("35000.00"))]
        # El arqueo vuelve EXACTAMENTE al valor pre-gasto (invariante).
        assert (await _svc(s).arqueo(uid)).saldo_esperado == Decimal("100000.00")


async def test_rechazar_es_idempotente(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid = await _usuario(s)
        await _svc(s).abrir(usuario_id=uid, saldo_inicial=Decimal("0"))
        gasto = await _gasto_bandeja(s, uid)
        await s.commit()
        gid = gasto.id

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        r1 = await _svc(s).rechazar_gasto(gid, usuario_id=uid)
        await s.commit()
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        r2 = await _svc(s).rechazar_gasto(gid, usuario_id=uid)   # replay: no segunda reversa
        await s.commit()
    assert r2.anulado_en == r1.anulado_en

    async with AsyncSession(tenant.engine) as s:
        ingresos = (
            await s.execute(text("SELECT count(*) FROM caja_movimientos WHERE tipo='ingreso'"))
        ).scalar_one()
    assert ingresos == 1                                   # una sola reversa


async def test_rechazar_gasto_no_pendiente_falla_409(tenant):
    import pytest
    from modules.caja.errors import GastoNoPendiente

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid = await _usuario(s)
        await _svc(s).abrir(usuario_id=uid, saldo_inicial=Decimal("0"))
        # Manual limpio (nunca estuvo en bandeja) y uno de bandeja YA aprobado: ninguno se puede rechazar.
        manual = await _svc(s).registrar_gasto(
            usuario_id=uid, categoria="transporte", monto=Decimal("15000"), concepto="taxi",
        )
        aprobado = await _gasto_bandeja(s, uid)
        await s.commit()
        await _svc(s).aprobar_gasto(aprobado.id)
        await s.commit()

    for gid in (manual.gasto.id, aprobado.id):
        async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
            with pytest.raises(GastoNoPendiente):
                await _svc(s).rechazar_gasto(gid, usuario_id=uid)


async def test_rechazar_sin_caja_abierta_falla(tenant):
    import pytest
    from modules.caja.errors import CajaNoAbierta

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid = await _usuario(s)
        await _svc(s).abrir(usuario_id=uid, saldo_inicial=Decimal("0"))
        gasto = await _gasto_bandeja(s, uid)
        await s.commit()
        await _svc(s).cerrar(usuario_id=uid, saldo_contado=Decimal("0"))
        await s.commit()

    # Sin caja abierta la reversa no tiene dónde postear su ingreso.
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        with pytest.raises(CajaNoAbierta):
            await _svc(s).rechazar_gasto(gasto.id, usuario_id=uid)


async def test_rechazado_sale_de_listados_bandeja_y_gasto_real_de_obra(tenant):
    from modules.obra.repository import SqlObrasRepository

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid = await _usuario(s)
        obra_id = await _seed_obra(s)
        await _svc(s).abrir(usuario_id=uid, saldo_inicial=Decimal("0"))
        gasto = await _gasto_bandeja(s, uid, monto="80000", obra_id=obra_id)
        await s.commit()
        gid = gasto.id

    async with AsyncSession(tenant.engine) as s:
        agregados = await SqlObrasRepository(s).agregados_gasto_batch([obra_id])
        assert agregados[obra_id].total_gastos == Decimal("80000.00")   # pre: el gasto cuenta

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await _svc(s).rechazar_gasto(gid, usuario_id=uid)
        await s.commit()

    async with AsyncSession(tenant.engine) as s:
        # Fuera de la bandeja, de los listados y del gasto real de la obra.
        assert await _svc(s).listar_revision() == []
        listados = await SqlCajaRepository(s).listar_gastos()
        assert gid not in [g.id for g in listados]
        agregados = await SqlObrasRepository(s).agregados_gasto_batch([obra_id])
        assert agregados.get(obra_id) is None or agregados[obra_id].total_gastos == Decimal("0")


async def test_editar_imputacion_solo_mientras_pendiente(tenant):
    import pytest
    from modules.caja.errors import GastoNoPendiente

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid = await _usuario(s)
        obra_id = await _seed_obra(s)
        await _svc(s).abrir(usuario_id=uid, saldo_inicial=Decimal("0"))
        gasto = await _gasto_bandeja(s, uid)     # sin obra ni categoría (el bot no supo imputar)
        await s.commit()
        gid = gasto.id

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        editado = await _svc(s).editar_imputacion(
            gid, {"obra_id": obra_id, "categoria_gasto": "COMBUSTIBLE", "concepto": "ACPM retro"}
        )
        await s.commit()
    assert editado.obra_id == obra_id
    assert editado.categoria_gasto == "COMBUSTIBLE"
    assert editado.concepto == "ACPM retro"

    # Tras aprobar, la imputación queda definitiva (cambiar plata o destino = rechazar y recrear).
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await _svc(s).aprobar_gasto(gid)
        await s.commit()
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        with pytest.raises(GastoNoPendiente):
            await _svc(s).editar_imputacion(gid, {"concepto": "otro"})


async def test_http_rechazar_y_editar_imputacion(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid = await _usuario(s, rol="admin")
        obra_id = await _seed_obra(s)
        await _svc(s).abrir(usuario_id=uid, saldo_inicial=Decimal("0"))
        g1 = await _gasto_bandeja(s, uid)
        g2 = await _gasto_bandeja(s, uid, monto="9000")
        await s.commit()

    transport = ASGITransport(app=_app(tenant, user_id=uid, rol="admin"), raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        patch = await c.patch(f"/api/v1/gastos/{g1.id}/imputacion", json={"obra_id": obra_id})
        rech = await c.post(f"/api/v1/gastos/{g1.id}/rechazar", json={"motivo": "borroso"})
        sin_motivo = await c.post(f"/api/v1/gastos/{g2.id}/rechazar", json={})
    assert patch.status_code == 200, patch.text
    assert patch.json()["obra_id"] == obra_id
    assert rech.status_code == 200, rech.text
    assert rech.json()["anulado_en"] is not None
    assert rech.json()["motivo_rechazo"] == "borroso"
    assert sin_motivo.status_code == 200, sin_motivo.text

    # RBAC: el rechazo es acción de supervisión (admin); el vendedor no puede.
    transport = ASGITransport(app=_app(tenant, user_id=uid, rol="vendedor"), raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        prohibido = await c.post(f"/api/v1/gastos/{g2.id}/rechazar", json={})
    assert prohibido.status_code == 403, prohibido.text


async def test_http_bandeja_y_aprobar_admin_y_vendedor_403(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid = await _usuario(s, rol="admin")
        await _svc(s).abrir(usuario_id=uid, saldo_inicial=Decimal("0"))
        pendiente = await _svc(s).registrar_gasto(
            usuario_id=uid, categoria="otros", monto=Decimal("35000"), concepto="del bot",
            origen_registro="TELEGRAM_BOT", requiere_revision=True,
        )
        await s.commit()
        gid = pendiente.gasto.id

    transport = ASGITransport(app=_app(tenant, user_id=uid, rol="admin"), raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        lista = await c.get("/api/v1/gastos/revision")
        aprob = await c.post(f"/api/v1/gastos/{gid}/aprobar")
    assert lista.status_code == 200, lista.text
    assert [g["id"] for g in lista.json()] == [gid]
    assert aprob.status_code == 200, aprob.text
    assert aprob.json()["requiere_revision"] is False

    # Un vendedor no accede a la bandeja de revisión (acción de supervisión → admin).
    transport = ASGITransport(app=_app(tenant, user_id=uid, rol="vendedor"), raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        prohibido = await c.get("/api/v1/gastos/revision")
    assert prohibido.status_code == 403, prohibido.text
