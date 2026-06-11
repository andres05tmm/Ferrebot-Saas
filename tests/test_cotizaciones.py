"""Pack ventas/cotizaciones (ADR 0017) — motor + herramientas contra base efímera real.

Cubre: el precio sale del motor REAL (escalonado por cantidad incluido), mostrar_stock gatea el
stock, carrito único por teléfono (agregar upserta y recotiza), quitar, emitir con vigencia,
vencimiento perezoso al listar, marcado del dashboard, y los guardarraíles (teléfono del contexto,
flag, producto no resuelto → error recuperable).
"""
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ai.cotizaciones_tools import CotizacionesDeps, ejecutar, exponer_catalogo
from ai.envelope import Contexto, ErrorTool, Resultado
from core.config.timezone import today_co
from core.llm.base import ToolCall
from modules.cotizaciones.errors import CarritoVacio, EstadoInvalido, ProductoNoResuelto
from modules.cotizaciones.repository import SqlCotizacionesRepository
from modules.cotizaciones.service import CotizacionesService, ItemCotizar

TEL_A = "3001112233"
TEL_B = "3009998877"


async def _seed_producto(
    s: AsyncSession, *, nombre: str, precio: str, stock: str = "50",
    umbral: str | None = None, bajo: str | None = None, sobre: str | None = None,
) -> int:
    pid = (
        await s.execute(
            text(
                "INSERT INTO productos (nombre, unidad_medida, precio_venta, iva, permite_fraccion, "
                "activo, precio_umbral, precio_bajo_umbral, precio_sobre_umbral) "
                "VALUES (:n, 'unidad', :p, 19, false, true, :u, :b, :so) RETURNING id"
            ),
            {"n": nombre, "p": precio, "u": umbral, "b": bajo, "so": sobre},
        )
    ).scalar_one()
    await s.execute(
        text("INSERT INTO inventario (producto_id, stock_actual, stock_minimo) VALUES (:pid, :s, 0)"),
        {"pid": pid, "s": stock},
    )
    await s.commit()
    return pid


def _svc(s: AsyncSession) -> CotizacionesService:
    return CotizacionesService(SqlCotizacionesRepository(s))


def _deps(s: AsyncSession) -> CotizacionesDeps:
    return CotizacionesDeps(cotizaciones=_svc(s))


def _ctx(telefono: str | None = TEL_A, *, con_flag: bool = True) -> Contexto:
    capacidades = frozenset({"pack_ventas"}) if con_flag else frozenset()
    return Contexto(
        tenant_id=1, usuario_id=0, rol="cliente", origen="whatsapp",
        capacidades=capacidades, cliente_telefono=telefono,
    )


def _call(herramienta: str, **arguments) -> ToolCall:
    return ToolCall(id="t", name=herramienta, arguments=arguments)


# --- precio real (el agente jamás calcula) -------------------------------------
async def test_cotizar_aplica_precio_escalonado_y_stock(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await _seed_producto(
            s, nombre="Cemento gris", precio="32000", stock="80",
            umbral="10", bajo="32000", sobre="30000",   # ≥10 unidades → precio mayorista
        )
        svc = _svc(s)

        unidad = await svc.cotizar("cemento gris", Decimal("1"))
        mayorista = await svc.cotizar("cemento gris", Decimal("10"))

    assert unidad.precio_unitario == Decimal("32000.00") and unidad.stock == Decimal("80.000")
    assert mayorista.precio_unitario == Decimal("30000.00")
    assert mayorista.total == Decimal("300000.00")


async def test_mostrar_stock_apagado_oculta_existencias(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await _seed_producto(s, nombre="Drywall", precio="45000")
        repo = SqlCotizacionesRepository(s)
        config = await repo.obtener_config()
        config.mostrar_stock = False
        await s.commit()

        p = await CotizacionesService(repo).cotizar("drywall")
        assert p.stock is None

        r = await ejecutar(_call("cotizar_producto", producto="drywall"), _ctx(), _deps(s))
        assert isinstance(r, Resultado) and "disponibles" not in r.resumen


async def test_producto_no_resuelto_lanza_con_sugerencias(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await _seed_producto(s, nombre="Cemento gris", precio="32000")
        with pytest.raises(ProductoNoResuelto):
            await _svc(s).cotizar("vigas de acero")

        err = await ejecutar(
            _call("cotizar_producto", producto="vigas de acero"), _ctx(), _deps(s)
        )
        assert isinstance(err, ErrorTool) and err.error == "producto_no_encontrado" and err.recuperable


# --- carrito ---------------------------------------------------------------------
async def test_carrito_agrega_upserta_quita_y_emite(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await _seed_producto(s, nombre="Cemento gris", precio="32000", umbral="10",
                             bajo="32000", sobre="30000")
        await _seed_producto(s, nombre="Drywall", precio="45000")
        svc = _svc(s)

        c = await svc.agregar(TEL_A, [ItemCotizar("cemento gris", Decimal("2"))])
        assert c.total == Decimal("64000.00")

        # Mismo producto otra cantidad → upsert + RECOTIZA (cruza el umbral mayorista).
        c = await svc.agregar(TEL_A, [ItemCotizar("cemento gris", Decimal("10"))])
        assert len(c.items) == 1 and c.total == Decimal("300000.00")

        c = await svc.agregar(TEL_A, [ItemCotizar("drywall", Decimal("1"))])
        assert len(c.items) == 2 and c.total == Decimal("345000.00")

        c = await svc.quitar(TEL_A, "drywall")
        assert len(c.items) == 1 and c.total == Decimal("300000.00")

        emitida = await svc.emitir(TEL_A, hoy=today_co())
        await s.commit()
        assert emitida.estado == "emitida"
        assert emitida.vigencia_hasta == today_co() + timedelta(days=3)   # vigencia default

        # Emitida la anterior, el próximo agregar abre un carrito NUEVO.
        nueva = await svc.agregar(TEL_A, [ItemCotizar("drywall", Decimal("1"))])
        assert nueva.id != emitida.id and nueva.estado == "abierta"


async def test_carrito_es_del_telefono_que_escribe(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await _seed_producto(s, nombre="Drywall", precio="45000")
        deps = _deps(s)
        await ejecutar(
            _call("agregar_a_cotizacion", items=[{"producto": "drywall", "cantidad": 2}]),
            _ctx(TEL_A), deps,
        )
        ajeno = await ejecutar(_call("ver_mi_cotizacion"), _ctx(TEL_B), deps)
        assert isinstance(ajeno, Resultado) and ajeno.data["cotizacion"] is None

        propio = await ejecutar(_call("ver_mi_cotizacion"), _ctx(TEL_A), deps)
        assert isinstance(propio, Resultado) and propio.data["total"] == "90000.00"


async def test_emitir_sin_carrito_y_vencimiento_y_marcado(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await _seed_producto(s, nombre="Drywall", precio="45000")
        svc = _svc(s)
        with pytest.raises(CarritoVacio):
            await svc.emitir(TEL_A, hoy=today_co())

        c = await svc.agregar(TEL_A, [ItemCotizar("drywall", Decimal("1"))])
        emitida = await svc.emitir(TEL_A, hoy=today_co() - timedelta(days=10))  # emitida hace 10 días
        await s.commit()

        # Listar hoy → el barrido perezoso la marca vencida.
        lista = await svc.listar(hoy=today_co())
        await s.commit()
        assert next(x for x in lista if x.id == emitida.id).estado == "vencida"

        with pytest.raises(EstadoInvalido):                 # vencida no se acepta
            await svc.marcar(emitida.id, "aceptada")

        c2 = await svc.agregar(TEL_B, [ItemCotizar("drywall", Decimal("2"))])
        emitida2 = await svc.emitir(TEL_B, hoy=today_co())
        aceptada = await svc.marcar(emitida2.id, "aceptada")
        assert aceptada.estado == "aceptada"


def test_catalogo_gateado_por_flag():
    assert exponer_catalogo(_ctx(con_flag=False)) == []
    nombres = [spec.name for spec in exponer_catalogo(_ctx())]
    assert nombres == [
        "cotizar_producto", "agregar_a_cotizacion", "quitar_de_cotizacion",
        "ver_mi_cotizacion", "emitir_cotizacion",
    ]


async def test_sin_telefono_falla_cerrado(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        r = await ejecutar(
            _call("agregar_a_cotizacion", items=[{"producto": "x", "cantidad": 1}]),
            _ctx(telefono=None), _deps(s),
        )
        assert isinstance(r, ErrorTool) and r.error == "contexto_invalido"
