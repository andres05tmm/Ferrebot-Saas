"""Tools del bot por feature FINA (ADR 0021): remapeo del catálogo y expansión del meta-pack.

Invariante de gating (test-primero): un tenant sin `ventas`/`caja` no debe tener en el catálogo del
modelo las tools que mutan ventas/caja; `pos` (meta-pack) las satisface todas por expansión.
"""
from ai.envelope import Contexto
from ai.tools import POR_NOMBRE


def _ctx(caps: frozenset[str]) -> Contexto:
    return Contexto(tenant_id=1, usuario_id=1, rol="vendedor", capacidades=caps)


def test_tools_declaran_su_feature_fina():
    assert POR_NOMBRE["registrar_venta"].feature == "ventas"
    assert POR_NOMBRE["consultar_ventas_dia"].feature == "ventas"
    assert POR_NOMBRE["consultar_producto"].feature == "ventas"
    assert POR_NOMBRE["registrar_gasto"].feature == "caja"
    assert POR_NOMBRE["crear_cliente"].feature is None            # clientes es núcleo
    assert POR_NOMBRE["registrar_fiado"].feature == "fiados"
    assert POR_NOMBRE["abonar_fiado"].feature == "fiados"


def test_contexto_sin_ventas_no_tiene_las_tools_de_venta():
    ctx = _ctx(frozenset({"pack_agenda", "caja"}))
    assert not ctx.tiene_capacidad("ventas")
    assert ctx.tiene_capacidad("caja")           # registrar_gasto sí está
    assert ctx.tiene_capacidad(None)             # núcleo (crear_cliente) siempre


def test_metapack_pos_satisface_las_finas_en_contexto():
    # Compat Punto Rojo: aunque el set llegue sin expandir (caché vieja), `pos` satisface las finas.
    ctx = _ctx(frozenset({"pos", "bot_telegram"}))
    for fina in ("ventas", "caja", "inventario"):
        assert ctx.tiene_capacidad(fina), fina
