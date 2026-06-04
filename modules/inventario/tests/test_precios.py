"""Motor de precios: una rama por esquema (ferrebot-logica-portar.md §3)."""
from decimal import Decimal

from modules.inventario.precios import (
    EsquemaPrecio,
    FraccionPrecio,
    obtener_precio_para_cantidad,
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
