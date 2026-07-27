"""La cuenta a la que entró la plata, y el sello de "esto no es una venta".

A las cuentas de la ferretería también entra plata personal y de la casa. El dueño no puede llevar
egresos (se mezclan), pero sí quiere llevar TODO lo que entra — y para eso necesita dos cosas que la
tabla no tenía:

1. **`cuenta_destino`** — a cuál de las dos cuentas llegó (*3891 o *6485). El parser de Bancolombia
   ya la extrae del correo desde hace rato, pero la ingesta la tiraba: nunca llegó a la base. Sin
   ella, "cuánto entró por cuenta" no se puede responder.

   No se reusa `referencia` porque ahí el bot viejo guardaba `llave or cuenta` — dos cosas distintas
   en una columna. Un GROUP BY sobre eso es un reporte que miente. `referencia` se queda con la
   llave, que también se estaba perdiendo.

2. **`descartado_en`** — el sello de "no es venta", que el dueño pone a mano.

   Es columna y no un valor nuevo del enum `conciliacion_estado` por tres razones: deshacer es
   `= NULL` (con un enum habría que recordar a qué estado volver), `ALTER TYPE ... ADD VALUE` es
   irreversible en Postgres y esto tiene `downgrade` limpio, y son preguntas ortogonales —
   `estado_conciliacion` responde "¿contra qué movimiento interno calza?" y `descartado_en`
   responde "¿esta plata es del negocio?". Mezclarlas obliga a que una respuesta borre la otra.

Aditiva y sin backfill: en producción la tabla está vacía. Se aplica a TODAS las empresas vía
`tools.migrate_tenants`.

Revision ID: 0073_bancos_cuenta_y_descarte
Revises: 0072_precio_paquete
Create Date: 2026-07-27
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0073_bancos_cuenta_y_descarte"
down_revision: str | None = "0072_precio_paquete"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("bancolombia_transferencias", sa.Column("cuenta_destino", sa.Text))
    op.add_column(
        "bancolombia_transferencias",
        sa.Column("descartado_en", sa.TIMESTAMP(timezone=True)),
    )
    # Sin índices a propósito: ~12k filas al año; el seqscan del agregado por cuenta corre en
    # milisegundos. Se agregan si alguna vez duele de verdad.


def downgrade() -> None:
    op.drop_column("bancolombia_transferencias", "descartado_en")
    op.drop_column("bancolombia_transferencias", "cuenta_destino")
