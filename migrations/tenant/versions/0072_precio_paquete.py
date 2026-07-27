"""El precio del empaque, y `precio_venta` con un solo significado.

La ferretería vende el mismo cemento de dos formas: el bulto de 50 kg a $28.000 y el kilo suelto a
$1.500. Son dos precios contra un solo montón de mercancía, y hasta hoy solo cabía uno.

Peor: `contenido_paquete` (0069) le cambiaba el significado a `precio_venta`. El motor lo leía como
"precio del paquete" y dividía (`precios.py`), así que ponerle 50 al cemento habría hecho que el
kilo se cobrara a $30. Esa convención venía de las puntillas, donde `precio_venta` SÍ es el precio
de la caja de 500 g.

Aquí se parte en dos datos explícitos:

- `precio_venta`   → SIEMPRE una unidad de venta (un kilo, un gramo, una unidad). Sin excepciones.
- `precio_paquete` → el empaque completo. NULL = ese producto no se vende por empaque.

El backfill mueve los granel que hoy tienen el precio del empaque en `precio_venta` (los únicos con
`contenido_paquete`: caja de puntilla 500 g, rollo de lija 100 cm, tarro de tinte 1000 ml): el valor
actual pasa a `precio_paquete` y `precio_venta` queda dividido. En Punto Rojo dan exacto ($15/g,
$200/cm, $26/ml) y ninguno tiene precio especial ni escalonado, así que no hay más números que
mover; el redondeo a centavos solo podría morder en un tenant futuro con divisiones no exactas.

Aditiva. Se aplica a TODAS las empresas vía `tools.migrate_tenants`.

Revision ID: 0072_precio_paquete
Revises: 0071_gastos_tipo_y_recurrentes
Create Date: 2026-07-26
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0072_precio_paquete"
down_revision: str | None = "0071_gastos_tipo_y_recurrentes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("productos", sa.Column("precio_paquete", sa.Numeric(12, 2)))
    # SOLO los granel: son los únicos donde `precio_venta` significa el precio del EMPAQUE, porque
    # el motor dividía por la convención de `unidad_medida` (GRM→500, Cms→100, MLT→1000).
    #
    # NO basta con `contenido_paquete IS NOT NULL`. Ese campo es de 0069 y cualquiera pudo llenarlo
    # desde el dashboard sobre un producto por kilo —cuyo `precio_venta` YA es por kilo— y ahí
    # dividir destruye el precio. Pasó: en Punto Rojo el dueño había puesto el tamaño de bolsa a
    # nueve polvos y esta migración les dejó el cemento a $60 el kilo en vez de $1.500.
    op.execute(
        """
        UPDATE productos
           SET precio_paquete = precio_venta,
               precio_venta = ROUND(precio_venta / contenido_paquete, 2)
         WHERE contenido_paquete IS NOT NULL AND contenido_paquete > 0
           AND precio_paquete IS NULL
           AND lower(btrim(unidad_medida))
               IN ('grm', 'gramos', 'cms', 'mlt', 'ml', 'mililitros')
        """
    )


def downgrade() -> None:
    # Devuelve el precio del empaque a `precio_venta` (el significado viejo) antes de soltar la
    # columna, solo donde el upgrade lo movió: los granel.
    op.execute(
        """
        UPDATE productos SET precio_venta = precio_paquete
         WHERE precio_paquete IS NOT NULL AND contenido_paquete IS NOT NULL
           AND lower(btrim(unidad_medida))
               IN ('grm', 'gramos', 'cms', 'mlt', 'ml', 'mililitros')
        """
    )
    op.drop_column("productos", "precio_paquete")
