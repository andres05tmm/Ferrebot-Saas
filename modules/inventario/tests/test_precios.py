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
    ("Unidad", None), ("Galón", None), ("", None), (None, None),
])
def test_unidades_por_paquete(unidad, esperado):
    assert unidades_por_paquete(unidad) == esperado


def test_granel_grm_cobra_por_gramo():
    # Puntilla: la caja trae 500 g y precio_venta es el precio de la caja. "500 puntilla" = 500 g = 1 caja.
    esquema = EsquemaPrecio(precio_venta=Decimal("7500"), unidad_medida="GRM")
    total, pu = obtener_precio_para_cantidad(esquema, Decimal("500"))
    assert total == Decimal("7500.00")            # antes el bug: 500 * 7500 = 3.75M
    assert pu == Decimal("15")                    # 7500 / 500 = $15 por gramo


def test_granel_grm_menudeo_y_multicaja():
    esquema = EsquemaPrecio(precio_venta=Decimal("7500"), unidad_medida="GRM")
    assert obtener_precio_para_cantidad(esquema, Decimal("250"))[0] == Decimal("3750.00")    # media caja
    assert obtener_precio_para_cantidad(esquema, Decimal("1000"))[0] == Decimal("15000.00")  # 2 cajas
    assert obtener_precio_para_cantidad(esquema, Decimal("100"))[0] == Decimal("1500.00")    # 100 g


def test_granel_cms_cobra_por_centimetro():
    # Lija esmeril: se cobra por cm; precio_venta está expresado por 100 cm. Esmeril 36 = $220/cm.
    esquema = EsquemaPrecio(precio_venta=Decimal("22000"), unidad_medida="Cms")
    assert obtener_precio_para_cantidad(esquema, Decimal("100"))[0] == Decimal("22000.00")  # 1 m
    assert obtener_precio_para_cantidad(esquema, Decimal("10"))[0] == Decimal("2200.00")    # 10 cm
    assert obtener_precio_para_cantidad(esquema, Decimal("30"))[0] == Decimal("6600.00")    # 30 cm
    # Se acopla a CUALQUIER cantidad de cm que pida el cliente (no solo múltiplos de 100/10).
    assert obtener_precio_para_cantidad(esquema, Decimal("11"))[0] == Decimal("2420.00")


def test_granel_etiqueta_regla():
    assert regla_para_cantidad(EsquemaPrecio(precio_venta=Decimal("7500"), unidad_medida="GRM"),
                               Decimal("500")) == "subunidad"
    assert regla_para_cantidad(EsquemaPrecio(precio_venta=Decimal("7500")), Decimal("3")) == "simple"


def test_escalonado_tiene_prioridad_sobre_subunidad():
    # Un producto con umbral Y unidad de granel: el escalonado gana (se evalúa primero). Caso defensivo:
    # en datos reales no coexisten, pero el orden de esquemas debe ser estable.
    esquema = EsquemaPrecio(
        precio_venta=Decimal("7500"), unidad_medida="GRM",
        precio_umbral=Decimal("10"), precio_bajo_umbral=Decimal("7500"),
        precio_sobre_umbral=Decimal("7000"),
    )
    total, pu = obtener_precio_para_cantidad(esquema, Decimal("5"))
    assert pu == Decimal("7500")                  # escalonado bajo umbral, NO ÷500
    assert total == Decimal("37500.00")
