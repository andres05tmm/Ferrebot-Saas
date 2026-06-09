"""Grandfather del pack `pos` para los tenants retail conocidos (ADR 0008 §D4).

Al sacar el POS del núcleo (ventas/inventario/caja/gastos/compras/proveedores pasan a vivir tras el
flag `pos`), un tenant retail existente que NO tenga `pos` activo se quedaría sin sus tabs en el
dashboard y con sus routers POS en 404. Esta migración de DATOS activa `pos` (override en
`empresa_features`) EXPLÍCITAMENTE para los tenants retail conocidos —hoy, Punto Rojo (`puntorojo`)—.

NO se usa heurística: clasificar un tenant como retail es una decisión, no una adivinanza. Si existe
algún tenant fuera del set conocido {puntorojo, clinica-demo}, la migración lo deja LISTADO con un
WARNING (no falla) para que se revise a mano y, si es retail, se le active `pos` con
`python -m tools.set_feature <slug> pos`. Idempotente (ON CONFLICT). Un tenant NUEVO declara `pos` en
su manifiesto/plan; esto solo cubre a los preexistentes.

Revision ID: 0004_grandfather_pos
Revises: 0003_wa_numeros
Create Date: 2026-06-09
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0004_grandfather_pos"
down_revision: str | None = "0003_wa_numeros"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # `pos` explícito para los tenants RETAIL conocidos (no heurística). Si `puntorojo` no existe en
    # este entorno, no inserta nada (idempotente y seguro en DBs nuevas/de prueba).
    op.execute(
        "INSERT INTO empresa_features (empresa_id, feature, habilitada) "
        "SELECT id, 'pos', true FROM empresas WHERE slug = 'puntorojo' "
        "ON CONFLICT (empresa_id, feature) DO NOTHING"
    )
    # No adivinar: si hay tenants fuera de {puntorojo, clinica-demo}, quedan LISTADOS en el log de la
    # migración para revisión manual (¿retail? → `tools.set_feature <slug> pos`). No falla la migración.
    op.execute(
        """
        DO $$
        DECLARE sin_clasificar text;
        BEGIN
            SELECT string_agg(slug, ', ' ORDER BY slug) INTO sin_clasificar
            FROM empresas WHERE slug NOT IN ('puntorojo', 'clinica-demo');
            IF sin_clasificar IS NOT NULL THEN
                RAISE WARNING 'grandfather_pos: tenants sin clasificar (revisar si son retail y activar pos a mano): %', sin_clasificar;
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    # Best-effort (migración de datos): retira los overrides de `pos`. En dev devuelve el estado previo;
    # en prod no se espera downgrade de un grandfather.
    op.execute("DELETE FROM empresa_features WHERE feature = 'pos'")
