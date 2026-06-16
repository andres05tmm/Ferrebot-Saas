"""Política de límites por empresa (ai/limites.py + integración en el despachador) — Fase 0.

Decisión pura `evaluar_venta` + parsing de config + integración por `Dispatcher.ejecutar`. Sin BD.
"""
from decimal import Decimal

from ai.dispatcher import Recursos
from ai.envelope import ErrorTool, Resultado
from ai.limites import (
    Escalar,
    LimitesEmpresa,
    PedirConfirmacion,
    Permitir,
    evaluar_venta,
    limites_desde_overrides,
)
from ai.ports import Umbrales
from ai.rieles import Confirmar
from core.llm.base import ToolCall
from tests.evals._harness import construir, ctx_eval


# --- decisión pura ----------------------------------------------------------
_LIM = LimitesEmpresa(venta_monto_max=Decimal("50000"), descuento_max_pct=Decimal("10"))


def test_dentro_de_limites_permite():
    d = evaluar_venta(total=Decimal("40000"), descuento_pct=Decimal("0"), limites=_LIM,
                      rol="vendedor", confirmado=False)
    assert isinstance(d, Permitir)


def test_monto_excede_modo_confirmar_pide_confirmacion():
    d = evaluar_venta(total=Decimal("60000"), descuento_pct=Decimal("0"), limites=_LIM,
                      rol="vendedor", confirmado=False)
    assert isinstance(d, PedirConfirmacion)


def test_confirmado_deja_pasar():
    d = evaluar_venta(total=Decimal("60000"), descuento_pct=Decimal("0"), limites=_LIM,
                      rol="vendedor", confirmado=True)
    assert isinstance(d, Permitir)


def test_descuento_excede_pide_confirmacion():
    d = evaluar_venta(total=Decimal("10000"), descuento_pct=Decimal("25"), limites=_LIM,
                      rol="vendedor", confirmado=False)
    assert isinstance(d, PedirConfirmacion)


def test_modo_escalar_vendedor_no_puede_admin_si():
    lim = LimitesEmpresa(venta_monto_max=Decimal("50000"), modo="escalar", rol_minimo="admin")
    assert isinstance(evaluar_venta(total=Decimal("60000"), descuento_pct=Decimal("0"),
                                    limites=lim, rol="vendedor", confirmado=False), Escalar)
    assert isinstance(evaluar_venta(total=Decimal("60000"), descuento_pct=Decimal("0"),
                                    limites=lim, rol="admin", confirmado=False), Permitir)


def test_sin_topes_no_evalua():
    assert LimitesEmpresa().activos is False


# --- parsing de config_empresa ---------------------------------------------
def test_limites_desde_overrides():
    lim = limites_desde_overrides({
        "venta_monto_max": "500000", "venta_descuento_max_pct": "15",
        "limite_modo": "escalar", "limite_rol_minimo": "admin",
    })
    assert lim.venta_monto_max == Decimal("500000.00") and lim.descuento_max_pct == Decimal("15.00")
    assert lim.modo == "escalar" and lim.rol_minimo == "admin"


def test_valores_invalidos_o_negativos_son_sin_tope():
    lim = limites_desde_overrides({"venta_monto_max": "-5", "venta_descuento_max_pct": "abc"})
    assert lim.venta_monto_max is None and lim.descuento_max_pct is None and lim.modo == "confirmar"


# --- integración por el despachador -----------------------------------------
def _recursos_con_limites(base: Recursos, lim: LimitesEmpresa) -> Recursos:
    class _Store:
        async def cargar(self, _e):
            return Umbrales(confirmar_mutaciones=False, limites=lim)
    return Recursos(deps=base.deps, catalogo=base.catalogo, umbrales=_Store())


def _toolcall(precio=None):
    item = {"producto_id": 7, "cantidad": Decimal("3")}      # vinilo 20000 x3 = 60000
    if precio is not None:
        item |= {"precio_unitario": precio, "precio_dicho_por_usuario": True}
    return ToolCall(id="t", name="registrar_venta",
                    arguments={"items": [item], "metodo_pago": "efectivo"})


async def test_venta_sobre_monto_pide_confirmacion_y_confirmada_ejecuta():
    h = construir()
    rec = _recursos_con_limites(h.recursos, LimitesEmpresa(venta_monto_max=Decimal("50000")))
    r1 = await h.dispatcher.ejecutar(_toolcall(), ctx_eval(confirmado=False), rec)
    assert isinstance(r1, Confirmar)
    r2 = await h.dispatcher.ejecutar(_toolcall(), ctx_eval(confirmado=True), rec)
    assert isinstance(r2, Resultado)            # el "sí" (misma key) ejecuta


async def test_venta_modo_escalar_vendedor_es_limite_excedido():
    h = construir()
    rec = _recursos_con_limites(
        h.recursos, LimitesEmpresa(venta_monto_max=Decimal("50000"), modo="escalar")
    )
    r = await h.dispatcher.ejecutar(_toolcall(), ctx_eval(rol="vendedor"), rec)
    assert isinstance(r, ErrorTool) and r.error == "limite_excedido" and r.recuperable is False


async def test_venta_dentro_de_limites_no_friccion():
    h = construir()
    rec = _recursos_con_limites(h.recursos, LimitesEmpresa(venta_monto_max=Decimal("100000")))
    r = await h.dispatcher.ejecutar(_toolcall(), ctx_eval(), rec)
    assert isinstance(r, Resultado)
