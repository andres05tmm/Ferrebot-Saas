"""Consulta de valor de vinilos/cuñetes por TIPO, no por color — docs/goal-mejoras-lija-vinilo.md (Bug 2).

El valor de la familia vinilo depende del TIPO (T1/T2/T3), no del color: todos los "Vinilo Davinci T1"
valen lo mismo sin importar el color. Antes, ante "cuánto vale el vinilo" la consulta enumeraba ~10
colores y preguntaba "¿cuál?" (ruido inútil). Ahora `_consultar_producto` colapsa por tipo:

  - si los candidatos comparten tipo y valor → responde el valor SIN listar colores;
  - si hay varios tipos → pregunta por TIPO (nunca por color);
  - si los candidatos NO declaran tipo (p. ej. martillos) → enumera como antes (no se toca).

Contrato del handler con fakes (cero red, cero PG), al estilo de test_tools_consulta.py.
"""
from decimal import Decimal

from ai.envelope import Resultado
from ai.tools import POR_NOMBRE, Deps
from modules.ventas.service import FraccionBusqueda, ProductoBusqueda


# --------------------------- Fakes / helpers ------------------------------
class _FakeVentaService:
    def __init__(self, productos):
        self._productos = list(productos)
        self.texto_recibido = "<no-llamado>"

    async def buscar_producto_por_nombre(self, texto):
        self.texto_recibido = texto
        return list(self._productos)


def _ctx():
    from ai.envelope import Contexto
    return Contexto(tenant_id=1, usuario_id=42, rol="vendedor", origen="bot",
                    capacidades=frozenset({"bot_telegram", "ventas"}))


def _deps(svc):
    return Deps(ventas=svc, caja=None, fiados=None, clientes=None)


def _frac(etiqueta, total):
    return FraccionBusqueda(etiqueta=etiqueta, precio_total=Decimal(total))


def _prod(id_, nombre, precio, fracciones=(), unidad="Galón", stock="3"):
    return ProductoBusqueda(id=id_, nombre=nombre, precio=Decimal(precio), stock=Decimal(stock),
                            unidad_medida=unidad, fracciones=tuple(fracciones))


async def _consultar(nombre, productos):
    from ai.tools import ConsultarProductoArgs
    svc = _FakeVentaService(productos)
    res = await POR_NOMBRE["consultar_producto"].handler(
        ConsultarProductoArgs(nombre=nombre), _ctx(), _deps(svc)
    )
    return res


# Catálogo de prueba: todos los T1 valen 50.000 (varios colores), T2 40.000, T3 22.000.
_T1 = [
    _prod(207, "Vinilo Davinci T1 Blanco", "50000", [_frac("1/2", "26000"), _frac("1/4", "14000")]),
    _prod(222, "Vinilo Davinci T1 Negro", "50000"),                 # sin fracciones cargadas
    _prod(217, "Vinilo Davinci T1 Lila", "50000", [_frac("1/2", "26000")]),
]
_T2 = [_prod(253, "Vinilo Davinci T2 Blanco", "40000"), _prod(278, "Vinilo Davinci T2 Verde", "40000")]
_T3 = [_prod(290, "Vinilo Davinci T3 Blanco", "22000"), _prod(294, "Vinilo Davinci T3 Coral", "22000")]


# --------------------------- mismo tipo → valor sin colores ----------------
async def test_un_solo_tipo_responde_valor_sin_listar_colores():
    res = await _consultar("vinilo t1", _T1)
    assert isinstance(res, Resultado)
    # Responde el valor del tipo (común a todos los colores) sin enumerarlos.
    assert "50000" in res.resumen
    assert "Vinilo Davinci T1" in res.resumen
    for color in ("Blanco", "Negro", "Lila"):
        assert color not in res.resumen          # NUNCA lista colores en la consulta de valor
    assert res.data["precio"] == "50000"
    assert res.data["tipo"] == "Vinilo Davinci T1"
    # Toma las fracciones del candidato más completo (no del primero, que podría no tenerlas).
    assert res.data["fracciones"] == [
        {"etiqueta": "1/2", "precio_total": "26000"},
        {"etiqueta": "1/4", "precio_total": "14000"},
    ]
    assert "1/2" in res.resumen and "26000" in res.resumen
    # Los colores quedan en data (sirven para registrar la venta, no para cotizar).
    assert {c["id"] for c in res.data["candidatos"]} == {207, 222, 217}


async def test_cunete_por_tipo_unico_responde_valor():
    cunetes = [_prod(314, "CUÑETE VINILO T 2", "170000", unidad="Unidad"),
               _prod(900, "Cuñete Vinilo Tipo 2 Davinci", "170000", unidad="Unidad")]
    res = await _consultar("cuñete vinilo t2", cunetes)
    assert isinstance(res, Resultado)
    assert "170000" in res.resumen
    assert "Blanco" not in res.resumen and "Davinci" not in res.resumen.replace("Cuñete Vinilo", "")


# --------------------------- varios tipos → preguntar por tipo -------------
async def test_varios_tipos_pregunta_por_tipo_no_por_color():
    res = await _consultar("vinilo", _T1 + _T2 + _T3)
    assert isinstance(res, Resultado)
    # Pregunta por TIPO (1/2/3) con su valor; jamás por color.
    for fragmento in ("Tipo 1", "Tipo 2", "Tipo 3", "50000", "40000", "22000"):
        assert fragmento in res.resumen
    for color in ("Blanco", "Negro", "Lila", "Verde", "Coral"):
        assert color not in res.resumen
    tipos = {o["tipo"] for o in res.data["opciones_por_tipo"]}
    assert tipos == {"1", "2", "3"}


# --------------------------- no se rompe lo que ya funciona ----------------
async def test_producto_unico_sin_tipo_sigue_simple():
    res = await _consultar("martillo", [_prod(7, "Martillo carpintero", "12000", unidad="Unidad")])
    assert isinstance(res, Resultado)
    assert res.data["id"] == 7 and "12000" in res.resumen


async def test_ambiguo_sin_tipo_enumera_candidatos_como_antes():
    # Dos productos DISTINTOS que comparten precio pero NO son variantes de color de un tipo:
    # sin token de tipo, el handler debe seguir enumerando (no colapsar).
    res = await _consultar("martillo", [
        _prod(1, "Martillo carpintero", "12000", unidad="Unidad"),
        _prod(2, "Martillo de bola", "12000", unidad="Unidad"),
    ])
    assert isinstance(res, Resultado)
    assert "Martillo carpintero" in res.resumen and "Martillo de bola" in res.resumen
    assert "¿Cuál?" in res.resumen
    assert "opciones_por_tipo" not in res.data and "tipo" not in res.data


async def test_familia_con_un_candidato_sin_tipo_no_colapsa():
    # Si entre los candidatos hay uno SIN tipo (p. ej. "Vinilo ICO"), no se colapsa: se enumera,
    # para no inventar un valor de tipo que no aplica a todos.
    mezcla = _T1 + [_prod(320, "Vinilo ICO blanco", "55000", unidad="Unidad")]
    res = await _consultar("vinilo", mezcla)
    assert isinstance(res, Resultado)
    assert "Vinilo ICO blanco" in res.resumen          # cayó al camino de enumerar
    assert "opciones_por_tipo" not in res.data
