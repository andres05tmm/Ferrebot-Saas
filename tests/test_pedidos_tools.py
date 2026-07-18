"""Herramientas de agente del pack Pedidos (`ai/pedidos_tools.py`) contra base efímera real.

Verifica el flujo completo (ver_menu → armar_pedido → confirmar_pedido → estado_mi_pedido), que los
errores de dominio vuelven usables (sugerencias del buscador, stock, cocina cerrada, sin borrador) y
—lo crítico— el GUARDARRAÍL: el teléfono sale del Contexto del canal; el modelo no puede ver ni
tocar pedidos ajenos. Catálogo gateado por el flag `pack_pedidos`.
"""
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ai.envelope import Contexto, ErrorTool, Resultado
from ai.pedidos_tools import PedidosDeps, ejecutar, exponer_catalogo
from core.llm.base import ToolCall
from modules.pedidos.repository import SqlPedidosRepository
from modules.pedidos.service import PedidosService

TEL_A = "3001112233"
TEL_B = "3009998877"


def _deps(s: AsyncSession) -> PedidosDeps:
    return PedidosDeps(pedidos=PedidosService(SqlPedidosRepository(s)))


def _ctx(telefono: str | None = TEL_A, *, con_flag: bool = True) -> Contexto:
    capacidades = frozenset({"pack_pedidos"}) if con_flag else frozenset()
    return Contexto(
        tenant_id=1, usuario_id=0, rol="cliente", origen="whatsapp",
        capacidades=capacidades, cliente_telefono=telefono,
    )


def _call(herramienta: str, **arguments) -> ToolCall:
    return ToolCall(id="t", name=herramienta, arguments=arguments)


async def _seed_producto(s: AsyncSession, *, nombre: str, precio: str, stock: str = "10") -> int:
    pid = (
        await s.execute(
            text(
                "INSERT INTO productos (nombre, unidad_medida, precio_venta, iva, "
                "permite_fraccion, activo) VALUES (:n, 'unidad', :p, 19, false, true) RETURNING id"
            ),
            {"n": nombre, "p": precio},
        )
    ).scalar_one()
    await s.execute(
        text("INSERT INTO inventario (producto_id, stock_actual, stock_minimo) VALUES (:pid, :s, 0)"),
        {"pid": pid, "s": stock},
    )
    await s.commit()
    return pid


async def _abrir_cocina_todo_el_dia(s: AsyncSession) -> None:
    """El test no controla la hora real del runtime de tools (now_co): cocina 24h."""
    repo = SqlPedidosRepository(s)
    config = await repo.obtener_config()
    from datetime import time
    config.hora_apertura = time(0, 0)
    config.hora_cierre = time(23, 59)
    await s.commit()


async def test_flujo_completo_armar_confirmar_estado(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await _seed_producto(s, nombre="Hamburguesa", precio="18000")
        await _abrir_cocina_todo_el_dia(s)
        deps = _deps(s)

        menu = await ejecutar(_call("ver_menu"), _ctx(), deps)
        assert isinstance(menu, Resultado) and "Hamburguesa" in menu.resumen

        armado = await ejecutar(
            _call("armar_pedido", items=[{"producto": "hamburguesa", "cantidad": 2}]), _ctx(), deps
        )
        assert isinstance(armado, Resultado)
        assert armado.data["subtotal"] == "36000.00"
        assert "dirección" in armado.resumen     # guía el siguiente paso

        confirmado = await ejecutar(
            _call("confirmar_pedido", direccion="Cra 1 # 2-3", metodo_pago="efectivo", nombre="Ana"),
            _ctx(), deps,
        )
        assert isinstance(confirmado, Resultado) and confirmado.evento == "pedido_confirmado"
        await s.commit()

        estado = await ejecutar(_call("estado_mi_pedido"), _ctx(), deps)
        assert isinstance(estado, Resultado) and "confirmado" in estado.resumen

        # Otro teléfono NO ve el pedido de A (acotado por contexto, no por args).
        ajeno = await ejecutar(_call("estado_mi_pedido"), _ctx(TEL_B), deps)
        assert isinstance(ajeno, Resultado) and ajeno.data["pedido"] is None


async def test_producto_desconocido_es_error_recuperable(tenant):
    """Lo que no resuelve el buscador vuelve como error recuperable (el agente repregunta u ofrece
    escalar); si el fuzzy aporta candidatos van en el detalle (la regla conservadora de palabra
    común puede dejarlo sin sugerencias — eso también es correcto: mejor nada que inventar)."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await _seed_producto(s, nombre="Hamburguesa doble", precio="22000")
        await _abrir_cocina_todo_el_dia(s)
        err = await ejecutar(
            _call("armar_pedido", items=[{"producto": "pizza hawaiana", "cantidad": 1}]),
            _ctx(), _deps(s),
        )
    assert isinstance(err, ErrorTool) and err.error == "producto_no_encontrado" and err.recuperable
    assert "pizza hawaiana" in err.detail


async def test_confirmar_sin_borrador_y_stock(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await _seed_producto(s, nombre="Gaseosa", precio="5000", stock="1")
        await _abrir_cocina_todo_el_dia(s)
        deps = _deps(s)

        sin = await ejecutar(
            _call("confirmar_pedido", direccion="Cra 1 # 2-3", metodo_pago="efectivo"), _ctx(), deps
        )
        assert isinstance(sin, ErrorTool) and sin.error == "sin_pedido"

        stock = await ejecutar(
            _call("armar_pedido", items=[{"producto": "Gaseosa", "cantidad": 4}]), _ctx(), deps
        )
        assert isinstance(stock, ErrorTool) and stock.error == "stock_insuficiente"


async def test_confirmar_telegram_exige_nombre_y_telefono(tenant):
    """Identidad opaca (tg:...): sin nombre+teléfono reales NO se confirma (el domiciliario debe
    poder llamar); con ellos se confirma y quedan persistidos en el pedido."""
    tg = "tg:987654"
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await _seed_producto(s, nombre="Almuerzo", precio="19000")
        await _abrir_cocina_todo_el_dia(s)
        deps = _deps(s)
        await ejecutar(
            _call("armar_pedido", items=[{"producto": "Almuerzo", "cantidad": 1}]), _ctx(tg), deps
        )

        falta = await ejecutar(
            _call("confirmar_pedido", direccion="Calle 27 #24-15", metodo_pago="efectivo"),
            _ctx(tg), deps,
        )
        assert isinstance(falta, ErrorTool) and falta.error == "faltan_datos_entrega"
        assert falta.recuperable

        ok = await ejecutar(
            _call(
                "confirmar_pedido", direccion="Calle 27 #24-15", metodo_pago="efectivo",
                nombre="Laura Pérez", telefono_contacto="300 123 4567",
            ),
            _ctx(tg), deps,
        )
        assert isinstance(ok, Resultado)
        assert "domiciliario" in ok.resumen
        pedido = await SqlPedidosRepository(s).ultimo_de(tg)
        assert pedido.cliente_nombre == "Laura Pérez"
        assert pedido.telefono_contacto == "3001234567"   # normalizado a dígitos


async def test_confirmar_whatsapp_defaultea_contacto_al_propio_numero(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await _seed_producto(s, nombre="Combo", precio="20000")
        await _abrir_cocina_todo_el_dia(s)
        deps = _deps(s)
        await ejecutar(
            _call("armar_pedido", items=[{"producto": "Combo", "cantidad": 1}]), _ctx(), deps
        )
        ok = await ejecutar(
            _call("confirmar_pedido", direccion="Cra 9 #10-11", metodo_pago="efectivo"),
            _ctx(), deps,
        )
        assert isinstance(ok, Resultado)
        pedido = await SqlPedidosRepository(s).ultimo_de(TEL_A)
        assert pedido.telefono_contacto == TEL_A   # su propio número: no hay que pedirlo


async def test_armar_tras_confirmar_no_duplica_el_pedido(tenant):
    """Guardarraíl anti-duplicado: 'ya pagué' tras confirmar NO debe crear un pedido gemelo.
    Solo `pedido_adicional=true` (intención explícita de OTRA comida) permite armar de nuevo."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await _seed_producto(s, nombre="Bandeja", precio="23000")
        await _abrir_cocina_todo_el_dia(s)
        deps = _deps(s)
        await ejecutar(
            _call("armar_pedido", items=[{"producto": "Bandeja", "cantidad": 1}]), _ctx(), deps
        )
        ok = await ejecutar(
            _call("confirmar_pedido", direccion="Cra 1 # 2-3", metodo_pago="transferencia"),
            _ctx(), deps,
        )
        assert isinstance(ok, Resultado)

        # El modelo intenta re-armar (malinterpretó "ya hice la transferencia") → bloqueado.
        dup = await ejecutar(
            _call("armar_pedido", items=[{"producto": "Bandeja", "cantidad": 1}]), _ctx(), deps
        )
        assert isinstance(dup, ErrorTool) and dup.error == "pedido_ya_confirmado"
        assert dup.recuperable
        n = (await s.execute(text("SELECT COUNT(*) FROM pedidos"))).scalar()
        assert n == 1                               # sigue habiendo UN solo pedido

        # Intención explícita de un pedido ADICIONAL → sí se permite.
        extra = await ejecutar(
            _call("armar_pedido", items=[{"producto": "Bandeja", "cantidad": 2}],
                  pedido_adicional=True),
            _ctx(), deps,
        )
        assert isinstance(extra, Resultado)


def test_catalogo_gateado_por_flag():
    assert exponer_catalogo(_ctx(con_flag=False)) == []
    nombres = [spec.name for spec in exponer_catalogo(_ctx())]
    assert nombres == ["ver_menu", "armar_pedido", "confirmar_pedido", "estado_mi_pedido"]


async def test_sin_telefono_falla_cerrado(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        deps = _deps(s)
        r = await ejecutar(
            _call("armar_pedido", items=[{"producto": "x", "cantidad": 1}]),
            _ctx(telefono=None), deps,
        )
        assert isinstance(r, ErrorTool) and r.error == "contexto_invalido"

        invalido = await ejecutar(_call("armar_pedido", items=[]), _ctx(), deps)
        assert isinstance(invalido, ErrorTool) and invalido.error == "validacion"
