"""Lija normal (por hoja) vs lija esmeril (por cm) — docs/goal-mejoras-lija-vinilo.md (Bug 1).

Dos verdades de dominio que el bot confundía:

  - La palabra "esmeril" decide el PRODUCTO: "lija 60" es lija normal (por hoja); "lija esmeril 60"
    es otro producto que se cobra por centímetro. Apuntan a ids distintos del catálogo.
  - La lija esmeril cobra `cm × (precio_venta / 100)` porque su `precio_venta` está expresado por
    100 cm (`unidad_medida = "Cms"`, motor de precios). El catálogo NO se toca; solo la resolución.

Se prueba la canonización del slug (notación de grano) en aislamiento y, de punta a punta, la
resolución + cálculo del bypass sobre un catálogo en memoria que espeja el real de Punto Rojo.
La desambiguación (preguntar el N°/los cm cuando faltan) se valida como "el bypass DEFIERE y nunca
registra" (la pregunta la hace el modelo); ver también el replay (categoría lija_desambiguacion).
"""
from decimal import Decimal

import pytest

from ai.bypass import normalizar_slug
from ai.envelope import Resultado
from modules.inventario.precios import EsquemaPrecio, obtener_precio_para_cantidad
from modules.ventas.service import ProductoPrecio
from tests.evals._harness import construir, ctx_eval

# pytest-asyncio en modo "auto" (pyproject): las corutinas se ejecutan sin marca explícita.


# --- Catálogo en memoria que espeja el real (ids reales de Punto Rojo) --------
LIJA_N60 = ProductoPrecio(id=15, nombre="Lija N°60", precio_venta=Decimal("2000"), iva=0, activo=True)
LIJA_N100 = ProductoPrecio(id=17, nombre="Lija N°100", precio_venta=Decimal("2000"), iva=0, activo=True)
ESMERIL_N36 = ProductoPrecio(id=29, nombre="Lija Esmeril N°36", precio_venta=Decimal("22000"),
                             iva=0, activo=True, unidad_medida="Cms")
ESMERIL_N60 = ProductoPrecio(id=30, nombre="Lija Esmeril N°60", precio_venta=Decimal("20000"),
                             iva=0, activo=True, unidad_medida="Cms")
ESMERIL_N80 = ProductoPrecio(id=31, nombre="Lija Esmeril N°80", precio_venta=Decimal("20000"),
                             iva=0, activo=True, unidad_medida="Cms")
CATALOGO_LIJA = (LIJA_N60, LIJA_N100, ESMERIL_N36, ESMERIL_N60, ESMERIL_N80)


async def _registrar(frase: str):
    """Pasa la frase por el bypass real (harness en memoria); devuelve (got, header)."""
    h = construir(CATALOGO_LIJA)
    res = await h.bypass.intentar(frase, ctx_eval(key=frase), h.recursos)
    return res, (h.ventas_repo.ultimo_header if isinstance(res, Resultado) else None)


# --- Canonización del slug (notación de grano) -------------------------------
@pytest.mark.parametrize(
    "entrada, esperado",
    [
        ("lija 60", "lija n 60"),                    # pelado → forma del catálogo
        ("lija esmeril 60", "lija esmeril n 60"),    # conserva "esmeril" (producto distinto)
        ("lija #60", "lija n 60"),                   # almohadilla
        ("Lija N°60", "lija n 60"),                  # como lo guarda el catálogo
        ("Lija Esmeril N°36", "lija esmeril n 36"),
        ("lija n60", "lija n 60"),                   # n pegado
        ("lija 1000", "lija n 1000"),                # grano de varios dígitos
    ],
)
def test_canoniza_notacion_de_grano(entrada, esperado):
    assert normalizar_slug(entrada) == esperado


def test_no_toca_frases_sin_lija():
    # La canonización solo dispara en frases de "lija": no toca otros productos con números.
    assert normalizar_slug("vinilo t1 verde") == "vinilo t1 verde"
    assert normalizar_slug("tornillo drywall 6x1") == "tornillo drywall 6x1"
    assert normalizar_slug("lija") == "lija"          # sin número → sin cambio


# --- Fórmula de la lija esmeril (motor de precios, por cm) --------------------
@pytest.mark.parametrize(
    "precio_venta, cm, total",
    [
        ("20000", "10", "2000"),     # 10 cm N°60 = 2.000
        ("22000", "100", "22000"),   # 100 cm N°36 = 22.000
        ("20000", "50", "10000"),    # 50 cm N°80 = 10.000
    ],
)
def test_esmeril_cobra_por_centimetro(precio_venta, cm, total):
    esquema = EsquemaPrecio(precio_venta=Decimal(precio_venta), unidad_medida="Cms")
    total_linea, _ = obtener_precio_para_cantidad(esquema, Decimal(cm))
    assert total_linea == Decimal(total)


# --- Resolución + cálculo de punta a punta por el bypass ---------------------
@pytest.mark.parametrize(
    "frase, producto_id, total",
    [
        ("10 cm lija esmeril 60", 30, "2000"),
        ("100 cm lija esmeril 36", 29, "22000"),
        ("50 cm lija esmeril 80", 31, "10000"),
    ],
)
async def test_esmeril_se_registra_por_cm(frase, producto_id, total):
    res, header = await _registrar(frase)
    assert isinstance(res, Resultado), f"{frase!r} debió registrar, no {res!r}"
    cm = Decimal(frase.split()[0])                      # la cantidad ES los centímetros pedidos
    assert [(l.producto_id, l.cantidad) for l in header.lineas] == [(producto_id, cm)]
    assert header.total == Decimal(total)


@pytest.mark.parametrize(
    "frase, producto_id",
    [("1 lija 60", 15), ("1 lija 100", 17)],
)
async def test_lija_normal_es_por_hoja(frase, producto_id):
    # Sin "esmeril" → producto normal (por unidad), $2.000 la hoja; jamás cae en el producto esmeril.
    res, header = await _registrar(frase)
    assert isinstance(res, Resultado)
    assert header.lineas[0].producto_id == producto_id
    assert header.total == Decimal("2000")


@pytest.mark.parametrize("frase", ["lija esmeril 60", "lija esmeril", "10 cm lija esmeril"])
async def test_falta_grano_o_cm_no_registra(frase):
    # Falta el N° o los cm → el bypass DEFIERE (None); nunca registra a ciegas (lo pregunta el modelo).
    res, header = await _registrar(frase)
    assert res is None, f"{frase!r} debió deferir, no registrar ({res!r})"
