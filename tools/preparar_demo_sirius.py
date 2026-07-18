"""Prepara el tenant demo de Siriuss para la reunión (idempotente; local o prod vía `railway ssh`).

    python -m tools.preparar_demo_sirius <slug> [--password <clave> --email <email>]

Hace, en orden (todo idempotente):
  1. Stock 50 a todo producto activo sin stock (movimientos de inventario con idempotency_key
     `seed-<slug>-stock-<producto_id>` — el menú del agente filtra stock>0).
  2. Horario de cocina ampliado 07:00–21:00 (que la demo nunca choque con "cocina cerrada";
     el horario real vive en la FAQ).
  3. Resiembra los pedidos vivos del kanban (`seed_demo_transaccional.resembrar_demo`) y les
     pone barrio/dirección/zona con totales coherentes (el seeder genérico no trae domicilio).
  4. Con `--password`, fija la clave de la identidad `--email` (hash bcrypt en el control DB).

La clave viaja por CLI solo en demos; jamás se loguea.
"""
from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import text

from core.auth.passwords import hash_password
from core.config import get_settings
from core.config.timezone import now_co
from core.db.session import control_session, tenant_session
from core.db.urls import tenant_url
from core.logging import configure_logging, get_logger
from core.tenancy.capacidades import ControlCapacidades
from core.tenancy.control_repo import resolve_tenant_by_slug
from core.tenancy.identidades_repo import set_password_hash
from modules.inventario.repository import SqlInventarioRepository
from modules.inventario.service import InventarioService
from tools.provision_tenant import _db_name
from tools.seed_demo_transaccional import resembrar_demo

log = get_logger("preparar_demo_sirius")

_BARRIOS = ["Manga", "Getsemaní", "Bocagrande", "Centro"]
_DIRECCIONES = [
    "Calle 27 #24-15", "Calle del Pozo #10-42",
    "Carrera 1 #6-90, Edificio Morros", "Calle de la Factoría #36-18",
]


async def preparar(slug: str, password: str | None, email: str | None) -> None:
    from decimal import Decimal

    settings = get_settings()
    async with control_session() as cs:
        tenant = await resolve_tenant_by_slug(cs, slug)
        if tenant is None:
            raise ValueError(f"empresa '{slug}' no existe")
        capacidades = await ControlCapacidades(cs).efectivas(tenant.id)

    # 1-2. Stock + horario.
    async for s in tenant_session(tenant):
        svc = InventarioService(SqlInventarioRepository(s))
        ids = [r[0] for r in (await s.execute(
            text("SELECT id FROM productos WHERE activo ORDER BY id"))).all()]
        con_stock = 0
        for pid in ids:
            actual = (await s.execute(
                text("SELECT stock_actual FROM inventario WHERE producto_id=:p"), {"p": pid}
            )).scalar()
            if not actual or actual <= 0:
                await svc.ajustar(producto_id=pid, delta=Decimal(50), motivo=f"seed demo {slug}",
                                  usuario_id=None, idempotency_key=f"seed-{slug}-stock-{pid}")
            con_stock += 1
        await s.execute(text("UPDATE pedido_config SET hora_apertura='07:00', hora_cierre='21:00'"))
    log.info("demo_stock_y_horario", slug=slug, productos=con_stock)

    # 3. Kanban vivo con barrios (resiembra + domicilio coherente).
    conteos = await asyncio.to_thread(
        resembrar_demo, tenant_url(settings.tenants_direct_url_base, _db_name(slug)),
        capacidades, now_co(),
    )
    async for s in tenant_session(tenant):
        activos = (await s.execute(text(
            "SELECT id, total, costo_domicilio FROM pedidos "
            "WHERE estado NOT IN ('entregado','cancelado') ORDER BY id"
        ))).all()
        for i, (pid, total, dom) in enumerate(activos):
            barrio = _BARRIOS[i % len(_BARRIOS)]
            zona = (await s.execute(text(
                "SELECT id, tarifa FROM zonas_domicilio WHERE nombre=:n"), {"n": barrio})).first()
            if zona is None:
                continue
            await s.execute(text(
                "UPDATE pedidos SET direccion=:d, zona_id=:z, costo_domicilio=:c, total=:t WHERE id=:p"
            ), {"d": f"{_DIRECCIONES[i % len(_DIRECCIONES)]}, {barrio}", "z": zona[0],
                "c": zona[1], "t": (total - (dom or 0)) + zona[1], "p": pid})
    log.info("demo_kanban_resembrado", slug=slug, **conteos)

    # 4. Clave de la identidad demo (opcional).
    if password and email:
        async with control_session() as cs:
            fila = (await cs.execute(text(
                "SELECT id FROM identidades WHERE empresa_id=:e AND email=:m"),
                {"e": tenant.id, "m": email})).first()
            if fila is None:
                raise ValueError(f"identidad '{email}' no existe en '{slug}'")
            await set_password_hash(cs, fila[0], hash_password(password))
        log.info("demo_password_fijada", slug=slug, email=email)


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    parser = argparse.ArgumentParser(description="Prepara el tenant demo Siriuss (idempotente).")
    parser.add_argument("slug")
    parser.add_argument("--password", default=None, help="clave a fijar (solo demos)")
    parser.add_argument("--email", default=None, help="identidad a la que fijar la clave")
    args = parser.parse_args(argv)
    if bool(args.password) != bool(args.email):
        print("error: --password y --email van juntos", file=sys.stderr)
        return 1
    try:
        asyncio.run(preparar(args.slug, args.password, args.email))
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"OK. demo '{args.slug}' preparada (stock + horario + kanban + clave)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
