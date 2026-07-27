"""Motor de precios: una rama por esquema (ferrebot-logica-portar.md §3)."""
from decimal import Decimal

import pytest

from modules.inventario.precios import (
    EsquemaPrecio,
    FraccionPrecio,
    obtener_precio_para_cantidad,
    regla_para_cantidad,
    unidades_por_paquete,
)


def test_simple_entero():
    esquema = EsquemaPrecio(precio_venta=Decimal("11900"))
    total, pu = obtener_precio_para_cantidad(esquema, Decimal("2"))
    assert total == Decimal("23800.00")
    assert pu == Decimal("11900")


def test_simple_cantidad_no_coincide_con_ninguna_fraccion_cae_a_simple():
    # Tiene fracciones (1/2) pero piden 3 → ni escalonado ni fracción: simple.
    esquema = EsquemaPrecio(
        precio_venta=Decimal("1000"),
        fracciones=(FraccionPrecio(decimal=Decimal("0.5"), precio_total=Decimal("600")),),
    )
    total, pu = obtener_precio_para_cantidad(esquema, Decimal("3"))
    assert total == Decimal("3000.00")
    assert pu == Decimal("1000")


def test_fraccion_sola_media():
    esquema = EsquemaPrecio(
        precio_venta=Decimal("1000"),
        fracciones=(
            FraccionPrecio(decimal=Decimal("0.25"), precio_total=Decimal("350")),
            FraccionPrecio(decimal=Decimal("0.5"), precio_total=Decimal("600")),
        ),
    )
    total, pu = obtener_precio_para_cantidad(esquema, Decimal("0.5"))
    assert total == Decimal("600.00")     # precio de la fracción, NO precio_venta*0.5
    assert pu == Decimal("1000")          # precio unitario del producto


def test_fraccion_tolerancia():
    # 0.249 cae dentro de la tolerancia 0.01 de la fracción 0.25.
    esquema = EsquemaPrecio(
        precio_venta=Decimal("1000"),
        fracciones=(FraccionPrecio(decimal=Decimal("0.25"), precio_total=Decimal("350")),),
    )
    total, _ = obtener_precio_para_cantidad(esquema, Decimal("0.249"))
    assert total == Decimal("350.00")


def test_escalonado_bajo_umbral():
    esquema = EsquemaPrecio(
        precio_venta=Decimal("5000"),
        precio_umbral=Decimal("10"),
        precio_bajo_umbral=Decimal("5000"),
        precio_sobre_umbral=Decimal("4500"),
    )
    total, pu = obtener_precio_para_cantidad(esquema, Decimal("5"))
    assert pu == Decimal("5000")          # 5 < 10 → precio bajo umbral
    assert total == Decimal("25000.00")


def test_escalonado_sobre_umbral_inclusive():
    esquema = EsquemaPrecio(
        precio_venta=Decimal("5000"),
        precio_umbral=Decimal("10"),
        precio_bajo_umbral=Decimal("5000"),
        precio_sobre_umbral=Decimal("4500"),
    )
    total, pu = obtener_precio_para_cantidad(esquema, Decimal("10"))
    assert pu == Decimal("4500")          # cantidad == umbral aplica sobre_umbral
    assert total == Decimal("45000.00")


def test_escalonado_tiene_prioridad_sobre_fraccion():
    # Con umbral definido, no se evalúan fracciones aunque existan.
    esquema = EsquemaPrecio(
        precio_venta=Decimal("5000"),
        precio_umbral=Decimal("10"),
        precio_bajo_umbral=Decimal("5000"),
        precio_sobre_umbral=Decimal("4500"),
        fracciones=(FraccionPrecio(decimal=Decimal("0.5"), precio_total=Decimal("3000")),),
    )
    total, pu = obtener_precio_para_cantidad(esquema, Decimal("0.5"))
    assert pu == Decimal("5000")          # escalonado: 0.5 < 10 → bajo umbral
    assert total == Decimal("2500.00")    # 5000 * 0.5, no la fracción


def test_umbral_incompleto_no_activa_escalonado():
    # precio_umbral sin sus precios bajo/sobre → no es escalonado válido, cae a simple.
    esquema = EsquemaPrecio(
        precio_venta=Decimal("1000"),
        precio_umbral=Decimal("10"),
    )
    total, pu = obtener_precio_para_cantidad(esquema, Decimal("12"))
    assert total == Decimal("12000.00")
    assert pu == Decimal("1000")


# --- Esquema 3: sub-unidad / granel (puntillas GRM, lija esmeril Cms) ---------

@pytest.mark.parametrize("unidad,esperado", [
    ("GRM", Decimal("500")), ("grm", Decimal("500")), ("Gramos", Decimal("500")),
    ("Cms", Decimal("100")), ("CMS", Decimal("100")),
    ("MLT", Decimal("1000")), ("ml", Decimal("1000")), ("Mililitros", Decimal("1000")),
    ("Unidad", None), ("Galón", None), ("kg", None), ("", None), (None, None),
])
def test_unidades_por_paquete(unidad, esperado):
    assert unidades_por_paquete(unidad) == esperado


def test_granel_cobra_por_sub_unidad_sin_dividir_nada():
    # Puntilla tras 0072: `precio_venta` YA está por gramo ($15). El motor no divide por convención;
    # la caja de 500 g sale de multiplicar, igual que cualquier otro producto.
    esquema = EsquemaPrecio(precio_venta=Decimal("15"), unidad_medida="GRM")
    total, pu = obtener_precio_para_cantidad(esquema, Decimal("500"))
    assert total == Decimal("7500.00")
    assert pu == Decimal("15")
    assert obtener_precio_para_cantidad(esquema, Decimal("250"))[0] == Decimal("3750.00")
    assert obtener_precio_para_cantidad(esquema, Decimal("100"))[0] == Decimal("1500.00")


def test_lija_por_centimetro():
    # Lija esmeril: $220 el cm (el rollo de 100 cm vale $22.000). Cualquier cantidad de cm, no solo
    # múltiplos: el cliente pide 11 cm y se le cobran 11.
    esquema = EsquemaPrecio(precio_venta=Decimal("220"), unidad_medida="Cms")
    assert obtener_precio_para_cantidad(esquema, Decimal("100"))[0] == Decimal("22000.00")
    assert obtener_precio_para_cantidad(esquema, Decimal("11"))[0] == Decimal("2420.00")


# --- Esquema 1: empaque entero (el bulto de cemento, la caja de puntilla) -----

def _cemento() -> EsquemaPrecio:
    """Bulto de 50 kg a $28.000; el kilo suelto a $1.500. Un solo inventario, dos precios."""
    return EsquemaPrecio(
        precio_venta=Decimal("1500"), unidad_medida="Kg",
        contenido_paquete=Decimal("50"), precio_paquete=Decimal("28000"),
    )


def test_empaque_cobra_el_precio_del_bulto_y_el_unitario_efectivo():
    total, pu = obtener_precio_para_cantidad(_cemento(), Decimal("50"), por_empaque=True)
    assert total == Decimal("28000.00")           # no 50 × 1.500 = 75.000
    assert pu == Decimal("560")                   # 28.000 / 50: la línea de la factura cuadra
    assert regla_para_cantidad(_cemento(), Decimal("50"), por_empaque=True) == "empaque"


def test_empaque_es_fijo_por_empaque():
    assert obtener_precio_para_cantidad(_cemento(), Decimal("150"), por_empaque=True)[0] == \
        Decimal("84000.00")                       # 3 bultos


def test_sin_pedirlo_la_misma_cantidad_se_cobra_suelta():
    # El precio de bulto NO se adivina: 50 kg sueltos son 50 kg sueltos. Nadie quiere un descuento
    # sorpresa por llevar justo el contenido de una bolsa.
    total, pu = obtener_precio_para_cantidad(_cemento(), Decimal("50"))
    assert total == Decimal("75000.00")
    assert pu == Decimal("1500")


def test_empaque_pedido_sobre_un_producto_que_no_lo_tiene_cae_a_simple():
    # Guarda de último recurso del motor (el servicio ya lo rechaza antes con LineaInvalida): sin
    # precio de empaque no se inventa uno.
    esquema = EsquemaPrecio(precio_venta=Decimal("11900"))
    assert not esquema.vende_por_empaque
    assert obtener_precio_para_cantidad(esquema, Decimal("2"), por_empaque=True)[0] == \
        Decimal("23800.00")


def test_empaque_tiene_prioridad_sobre_escalonado():
    # Si el vendedor pidió el bulto, manda el bulto. Caso defensivo: en datos reales no coexisten,
    # pero el orden de esquemas debe ser estable.
    esquema = EsquemaPrecio(
        precio_venta=Decimal("1500"), unidad_medida="Kg",
        contenido_paquete=Decimal("50"), precio_paquete=Decimal("28000"),
        precio_umbral=Decimal("10"), precio_bajo_umbral=Decimal("1500"),
        precio_sobre_umbral=Decimal("1200"),
    )
    assert obtener_precio_para_cantidad(esquema, Decimal("50"), por_empaque=True)[0] == \
        Decimal("28000.00")


def test_esquema_de_lleva_empaque_y_unidad():
    # Regresión: `esquema_de` alimenta GET /productos/{id}/precio, que el POS consulta por línea. Si
    # omite el empaque, el POS cotiza un precio distinto al que la venta va a cobrar.
    from modules.inventario.models import Producto
    from modules.inventario.service import esquema_de

    prod = Producto(
        nombre="Cemento Gris", unidad_medida="Kg", precio_venta=Decimal("1500"),
        contenido_paquete=Decimal("50"), precio_paquete=Decimal("28000"),
        iva=19, permite_fraccion=False, activo=True, fracciones=[],
    )
    esquema = esquema_de(prod)
    assert esquema.unidad_medida == "Kg" and esquema.vende_por_empaque
    assert obtener_precio_para_cantidad(esquema, Decimal("50"), por_empaque=True)[0] == \
        Decimal("28000.00")
    # Y sin pedir el empaque, el mismo esquema cobra los 50 kg sueltos.
    assert obtener_precio_para_cantidad(esquema, Decimal("50"))[0] == Decimal("75000.00")
