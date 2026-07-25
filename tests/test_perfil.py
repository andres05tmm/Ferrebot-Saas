"""Perfil de usuario: lectura/edición y el historial de acciones SIEMPRE acotado al propio usuario."""
import uuid
from datetime import timedelta
from decimal import Decimal

from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from core.config import get_settings
from core.config.timezone import rango_dia_co, today_co
from core.db.urls import tenant_url, to_async
from core.tenancy.identidades_repo import email_de_usuario
from modules.fiados.repository import SqlFiadosRepository
from modules.fiados.service import FiadosService
from modules.perfil.repository import SqlPerfilRepository
from tests.conftest import create_database, drop_database


async def _usuario(s: AsyncSession, nombre: str) -> int:
    return (
        await s.execute(
            text("INSERT INTO usuarios (nombre, rol) VALUES (:n, 'vendedor') RETURNING id"),
            {"n": nombre},
        )
    ).scalar_one()


async def _venta(s: AsyncSession, *, vendedor_id: int, consecutivo: int, total: str) -> None:
    await s.execute(
        text(
            "INSERT INTO ventas (consecutivo, vendedor_id, subtotal, impuestos, total, metodo_pago) "
            "VALUES (:c, :v, :t, 0, :t, 'efectivo')"
        ),
        {"c": consecutivo, "v": vendedor_id, "t": total},
    )


async def test_perfil_obtener_y_actualizar(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid = await _usuario(s, "Marta")
        repo = SqlPerfilRepository(s)

        fila = await repo.obtener(uid)
        assert fila.nombre == "Marta" and fila.rol == "vendedor"
        assert fila.avatar_url is None and fila.color is None

        await repo.actualizar(uid, nombre="Marta P.", color="#1971C2", avatar_url="https://x/foto.jpg")
        fila = await repo.obtener(uid)
        assert (fila.nombre, fila.color, fila.avatar_url) == ("Marta P.", "#1971C2", "https://x/foto.jpg")

        # Actualización parcial: solo color; el resto queda intacto.
        await repo.actualizar(uid, color="#C8200E")
        fila = await repo.obtener(uid)
        assert fila.nombre == "Marta P." and fila.color == "#C8200E"

        assert await repo.obtener(999_999) is None


async def test_acciones_solo_del_propio_usuario(tenant, seed_producto):
    """El feed y el resumen de A no incluyen NADA de B (ventas, gastos, abonos)."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid_a, producto_id = await seed_producto(s)   # usuario "Vendedor" + producto con inventario
        uid_b = await _usuario(s, "Otro")
        await _venta(s, vendedor_id=uid_a, consecutivo=1, total="10000")
        await _venta(s, vendedor_id=uid_a, consecutivo=2, total="5000")
        await _venta(s, vendedor_id=uid_b, consecutivo=3, total="7000")
        await s.execute(
            text(
                "INSERT INTO gastos (categoria, monto, concepto, usuario_id) "
                "VALUES ('transporte', 3000, 'domicilio', :u)"
            ),
            {"u": uid_a},
        )
        # Compra atribuida por su movimiento ENTRADA (así la escribe el módulo de compras).
        compra_id = (
            await s.execute(text("INSERT INTO compras (total) VALUES (20000) RETURNING id"))
        ).scalar_one()
        await s.execute(
            text(
                "INSERT INTO movimientos_inventario (producto_id, tipo, cantidad, referencia, usuario_id) "
                "VALUES (:p, 'ENTRADA', 5, :r, :u)"
            ),
            {"p": producto_id, "r": f"compra:{compra_id}", "u": uid_a},
        )
        # Abono de fiado por el SERVICIO (verifica el threading de usuario_id de la 0065).
        cid = (
            await s.execute(text("INSERT INTO clientes (nombre, saldo_fiado) VALUES ('Cli', 0) RETURNING id"))
        ).scalar_one()
        fsvc = FiadosService(SqlFiadosRepository(s))
        fiado = (await fsvc.crear(cliente_id=cid, venta_id=None, monto=Decimal("8000"))).fiado
        await fsvc.abonar(fiado_id=fiado.id, monto=Decimal("2000"), usuario_id=uid_a)
        await s.commit()

    desde, _ = rango_dia_co()
    async with AsyncSession(tenant.engine) as s:
        repo = SqlPerfilRepository(s)

        acciones = await repo.acciones(uid_a, desde=desde, limite=50, offset=0)
        tipos = sorted(a.tipo for a in acciones)
        assert tipos == ["abono", "compra", "gasto", "venta", "venta"]
        # Nada del usuario B en el feed de A: la venta 3 no aparece.
        assert all("N.º 3" not in a.detalle for a in acciones)

        resumen = await repo.resumen(uid_a, desde=desde)
        assert (resumen.ventas, resumen.gastos, resumen.abonos, resumen.compras) == (2, 1, 1, 1)
        assert resumen.total_vendido == Decimal("15000.00")

        # El usuario B solo ve lo suyo.
        acciones_b = await repo.acciones(uid_b, desde=desde, limite=50, offset=0)
        assert [a.tipo for a in acciones_b] == ["venta"]
        assert (await repo.resumen(uid_b, desde=desde)).total_vendido == Decimal("7000.00")


async def test_acciones_rango_y_paginacion(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid = await _usuario(s, "Paginada")
        for c in range(1, 6):
            await _venta(s, vendedor_id=uid, consecutivo=c, total="1000")
        # Una venta vieja, fuera del rango de hoy.
        ayer = rango_dia_co(today_co() - timedelta(days=1))[0]
        await s.execute(
            text(
                "INSERT INTO ventas (consecutivo, vendedor_id, fecha, subtotal, impuestos, total, metodo_pago) "
                "VALUES (99, :v, :f, 1000, 0, 1000, 'efectivo')"
            ),
            {"v": uid, "f": ayer},
        )
        await s.commit()

    desde_hoy, _ = rango_dia_co()
    async with AsyncSession(tenant.engine) as s:
        repo = SqlPerfilRepository(s)
        pagina1 = await repo.acciones(uid, desde=desde_hoy, limite=3, offset=0)
        pagina2 = await repo.acciones(uid, desde=desde_hoy, limite=3, offset=3)
        assert len(pagina1) == 3 and len(pagina2) == 2   # la de ayer queda fuera
        # Rango de 2 días la incluye.
        desde_ayer, _ = rango_dia_co(today_co() - timedelta(days=1), today_co())
        todas = await repo.acciones(uid, desde=desde_ayer, limite=50, offset=0)
        assert len(todas) == 6


async def test_email_de_usuario_con_varias_identidades_no_revienta(monkeypatch):
    """Regresión Sentry (MultipleResultsFound): un usuario puede tener VARIAS identidades — la
    grandfather vieja + el email nuevo. Debe devolver UNA (activa primero, más reciente), no fallar."""
    name = f"test_control_perfil_{uuid.uuid4().hex[:12]}"
    url = tenant_url(get_settings().tenants_direct_url_base, name)
    # El env de alembic de control lee CONTROL_DATABASE_URL: apuntarlo a la base efímera (patrón
    # de test_migracion_control_*) para no tocar el control DB real.
    monkeypatch.setenv("CONTROL_DATABASE_URL", url)
    get_settings.cache_clear()
    create_database(name)
    engine = create_async_engine(
        to_async(url), poolclass=NullPool, connect_args={"statement_cache_size": 0}
    )
    try:
        command.upgrade(Config("migrations/control/alembic.ini"), "head")
        async with AsyncSession(engine) as s:
            eid = (
                await s.execute(text(
                    "INSERT INTO empresas (nombre, nit, slug, estado) "
                    "VALUES ('E','NIT','e','activa') RETURNING id"
                ))
            ).scalar_one()
            await s.execute(text(
                "INSERT INTO identidades (email, empresa_id, usuario_id, rol, activo) VALUES "
                "('viejo@x.co', :e, 1, 'admin', false), ('nuevo@x.co', :e, 1, 'admin', true)"
            ), {"e": eid})
            await s.commit()

        async with AsyncSession(engine) as s:
            assert await email_de_usuario(s, eid, 1) == "nuevo@x.co"   # la activa gana
            assert await email_de_usuario(s, eid, 99) is None
    finally:
        await engine.dispose()
        get_settings.cache_clear()
        drop_database(name)
