"""Da de alta (o actualiza) el mapeo de un número de WhatsApp de Kapso → empresa en el control DB.

Evita hardcodear el `phone_number_id` (específico del entorno) en una migración. Upsert por
`phone_number_id`: si ya existe, reapunta la empresa/estado.

    python -m tools.seed_wa_numero <phone_number_id> <slug_empresa> [--numero +57300...] [--waba-id ...]

El `phone_number_id` lo da Kapso (dashboard / payload del webhook). No es secreto.
"""
import argparse
import sys

import psycopg
from psycopg.rows import dict_row

from core.config import get_settings
from core.db.urls import to_libpq
from core.logging import configure_logging, get_logger

log = get_logger("seed_wa_numero")


def upsert_wa_numero(
    conn: psycopg.Connection,
    phone_number_id: str,
    empresa_id: int,
    *,
    numero: str | None = None,
    waba_id: str | None = None,
) -> None:
    """Upsert del mapeo `phone_number_id` → empresa (estado `activo`). SQL único, reusado por el switch.

    `numero`/`waba_id` solo se sobreescriben si se pasan (COALESCE): re-apuntar la empresa de un número
    ya existente no borra su número legible/WABA. La conexión maneja su propia transacción/commit.
    """
    conn.execute(
        """INSERT INTO wa_numeros (phone_number_id, empresa_id, numero, waba_id, estado)
           VALUES (%s, %s, %s, %s, 'activo')
           ON CONFLICT (phone_number_id)
           DO UPDATE SET empresa_id = EXCLUDED.empresa_id,
                         numero = COALESCE(EXCLUDED.numero, wa_numeros.numero),
                         waba_id = COALESCE(EXCLUDED.waba_id, wa_numeros.waba_id),
                         estado = 'activo'""",
        (phone_number_id, empresa_id, numero, waba_id),
    )


def seed(phone_number_id: str, slug: str, *, numero: str | None, waba_id: str | None) -> int:
    control_url = to_libpq(get_settings().control_database_url)
    with psycopg.connect(control_url, row_factory=dict_row, autocommit=True) as conn:
        empresa = conn.execute(
            "SELECT id FROM empresas WHERE slug = %s", (slug,)
        ).fetchone()
        if empresa is None:
            log.error("empresa_no_encontrada", slug=slug)
            return 1
        upsert_wa_numero(conn, phone_number_id, empresa["id"], numero=numero, waba_id=waba_id)
    log.info("wa_numero_mapeado", phone_number_id=phone_number_id, slug=slug)
    return 0


def main() -> int:
    configure_logging()
    parser = argparse.ArgumentParser(description="Mapea un phone_number_id de Kapso a una empresa.")
    parser.add_argument("phone_number_id", help="phone_number_id que envía Kapso")
    parser.add_argument("slug", help="slug de la empresa (control DB)")
    parser.add_argument("--numero", default=None, help="número legible (+57…), referencia")
    parser.add_argument("--waba-id", default=None, help="WABA id, referencia")
    args = parser.parse_args()
    return seed(args.phone_number_id, args.slug, numero=args.numero, waba_id=args.waba_id)


if __name__ == "__main__":
    sys.exit(main())
