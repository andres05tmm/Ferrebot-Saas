"""branding_preset: preset de marca por vertical + default de plataforma Melquiadez.

Añade `branding.preset` (TEXT, nullable): el nombre del preset por gremio (aurora/brasa/navaja/
brisa/lienzo/melquiadez) cuyos tokens resuelve `core.tenancy.branding_presets`. El default de
plataforma deja de ser `#C8200E` y pasa a `melquiadez` (oro viejo/tinta); por eso:

1. Se quita el `server_default` de `color_primario`: una fila sin color explícito ya NO nace roja,
   sino que hereda el primario del preset (el dashboard lo resuelve). Las filas existentes conservan
   su valor; el cambio solo afecta a inserciones futuras que omitan el color.
2. Se SIEMBRA el branding explícito de Punto Rojo (`#C8200E`) si no lo tuviera, para que el cambio de
   default NO altere su rojo de marca (ahora es branding explícito de PR, no el default de plataforma).

`preset` es branding, no un secreto ni datos de negocio: vive en el control DB junto a logo/color/
nombre (`control_repo.leer_branding`). Coexiste con `tema` (el nombre viejo del mismo concepto, que
`leer_branding` sigue leyendo como fallback del preset).

Revision ID: 0009_branding_preset
Revises: 0008_branding_tema
Create Date: 2026-06-12
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_branding_preset"
down_revision: str | None = "0008_branding_tema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("branding", sa.Column("preset", sa.Text, nullable=True))
    # El default de plataforma pasa de #C8200E a melquiadez: el color ya no se rellena por defecto.
    op.alter_column("branding", "color_primario", server_default=None)
    # Punto Rojo conserva su rojo como branding EXPLÍCITO (idempotente; no-op si ya lo tiene o si el
    # tenant no existe en este control DB —p. ej. una base efímera de test—).
    op.execute(
        """
        INSERT INTO branding (empresa_id, color_primario)
        SELECT e.id, '#C8200E' FROM empresas e WHERE e.slug = 'puntorojo'
        ON CONFLICT (empresa_id)
        DO UPDATE SET color_primario = COALESCE(branding.color_primario, '#C8200E')
        """
    )


def downgrade() -> None:
    op.alter_column("branding", "color_primario", server_default="#C8200E")
    op.drop_column("branding", "preset")
