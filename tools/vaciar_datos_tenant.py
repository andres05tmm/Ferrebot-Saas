"""Vacía los datos de NEGOCIO de un tenant y lo deja "como nuevo", conservando lo que no es dato demo.

Caso de uso: un tenant que se llenó con datos falsos (para ver el dashboard) y hay que entregarlo
limpio al cliente real. NO borra ni reprovisiona la empresa: solo TRUNCA las tablas de negocio de su
App DB. El CONTROL DB (empresas, planes, branding, secretos, config, feature flags, identidades de
login) NO se toca — eso mantiene al tenant existiendo y configurado.

QUÉ SE CONSERVA en la App DB (no es demo, un tenant recién provisionado lo tiene):
  - `usuarios`      — el login del admin cuelga de aquí (control.identidades.usuario_id → usuarios.id).
                      Se preserva TAL CUAL (sin RESTART IDENTITY) para no romper ese enlace.
  - `parametros_legales` — constantes legales de nómina (cimiento del vertical construcción).
  - `alembic_version`    — estado de migraciones.
  - Tablas de configuración (`*config*`: cobranza_config, cartera_config, agenda_config, …) — son
                      AJUSTES por tenant, no dato demo que se ve en el dashboard. Vaciarlas dejaría al
                      tenant sin config que un tenant provisionado sí tiene (y la app podría fallar por
                      una fila de config ausente).
Todo lo demás del schema `public` se TRUNCA (RESTART IDENTITY CASCADE): catálogo, obras, ventas,
partes de horas, movimientos, caja, fiados, facturas sandbox, conversaciones del bot, etc.

SEGURIDAD (falla cerrado):
  - Exige `--confirmar` para borrar; sin él es un DRY-RUN (lista qué tumbaría y termina).
  - Se NIEGA si la base conectada parece el control DB (tiene `empresas`/`tenant_databases`) o si no
    parece un tenant (falta `usuarios`).
  - `--prod` carga `.env.prod` (URLs públicas de Railway) ANTES de resolver settings, igual que
    tools/backup_db. Sin `--prod` va contra el `.env` local (Docker 5433).

    python -m tools.vaciar_datos_tenant --slug pim                 # DRY-RUN local (no borra)
    python -m tools.vaciar_datos_tenant --slug pim --confirmar     # borra en local
    python -m tools.vaciar_datos_tenant --slug pim --prod --confirmar   # borra en PRODUCCIÓN

Espeja el patrón de los demás tools (psycopg SYNC + dict_row + to_libpq + tenant_url + _db_name).
"""
from __future__ import annotations

import argparse
import sys

import psycopg
from psycopg.rows import dict_row

from core.config import get_settings
from core.db.urls import tenant_url, to_libpq
from tools.provision_tenant import _db_name

# Tablas de la App DB que NO son dato demo: se conservan intactas (ver docstring). A esta base fija se
# le suman en runtime todas las tablas de configuración (`*config*`), que son ajustes del tenant.
PRESERVAR_BASE: frozenset[str] = frozenset({"usuarios", "parametros_legales", "alembic_version"})


def _preservar(todas: set[str]) -> frozenset[str]:
    """Conjunto EFECTIVO a conservar: la base fija + toda tabla de configuración (`*config*`)."""
    return PRESERVAR_BASE | {t for t in todas if "config" in t}

# Marcadores para detectar por error el CONTROL DB (jamás truncar): si la base tiene estas tablas,
# es el plano de control, no un tenant. `usuarios` en cambio es marcador de tenant (debe existir).
_TABLAS_CONTROL = ("empresas", "tenant_databases", "identidades")


def tablas_public(conn) -> set[str]:
    """Nombres de las tablas BASE del schema `public` (excluye vistas)."""
    filas = conn.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema='public' AND table_type='BASE TABLE'"
    ).fetchall()
    return {f["table_name"] for f in filas}


def _es_control_db(tablas: set[str]) -> bool:
    """¿La base conectada parece el control DB? (tiene tablas exclusivas del plano de control)."""
    return any(t in tablas for t in _TABLAS_CONTROL)


def _conteos(conn, tablas: set[str]) -> dict[str, int]:
    """Filas por tabla (solo las no vacías, para un resumen legible). Ordenado por conteo desc."""
    conteos: dict[str, int] = {}
    for t in tablas:
        n = conn.execute(f'SELECT count(*) AS n FROM "{t}"').fetchone()["n"]
        if n:
            conteos[t] = n
    return dict(sorted(conteos.items(), key=lambda kv: kv[1], reverse=True))


def vaciar(conn_url: str, *, confirmar: bool) -> int:
    """Trunca las tablas de negocio de la App DB en `conn_url`. Devuelve 0 OK / 1 error o abortado.

    DRY-RUN si `confirmar=False`: imprime qué tumbaría y NO escribe. Con `confirmar=True` trunca todo
    el complemento de lo conservado en UNA sentencia (RESTART IDENTITY CASCADE resuelve el orden de FKs)
    dentro de una transacción.
    """
    with psycopg.connect(to_libpq(conn_url), row_factory=dict_row) as conn:
        todas = tablas_public(conn)
        if _es_control_db(todas):
            print("ABORTADO: la base conectada parece el CONTROL DB (tiene empresas/tenant_databases/"
                  "identidades). Este script solo vacía la App DB de un tenant.", file=sys.stderr)
            return 1
        if "usuarios" not in todas:
            print("ABORTADO: la base no tiene `usuarios`; no parece un tenant. No se toca nada.",
                  file=sys.stderr)
            return 1

        preservar = _preservar(todas)
        objetivo = sorted(todas - preservar)
        conservadas = sorted(todas & preservar)
        if not objetivo:
            print("No hay tablas para vaciar (todas están en la lista de conservadas).")
            return 0

        conteos = _conteos(conn, set(objetivo))
        total_filas = sum(conteos.values())
        print(f"App DB: {len(todas)} tablas | conservar {len(conservadas)}: {', '.join(conservadas)}")
        print(f"A VACIAR: {len(objetivo)} tablas, {total_filas} filas con datos "
              f"({len(conteos)} tablas no vacías):")
        for t, n in conteos.items():
            print(f"  {t}: {n}")
        if not conteos:
            print("  (todas las tablas objetivo ya están vacías)")

        if not confirmar:
            print("\nDRY-RUN: no se borró nada. Repite con --confirmar para vaciar de verdad.")
            return 0

        # Una sola sentencia: RESTART IDENTITY reinicia los serial/identity de las tablas truncadas;
        # CASCADE resuelve las FKs entre ellas. Las conservadas no se referencian hacia el objetivo,
        # así que CASCADE no las alcanza (usuarios/parametros_legales/alembic_version son parents u
        # hojas sin FK saliente hacia el negocio).
        lista = ", ".join(f'"{t}"' for t in objetivo)
        conn.execute(f"TRUNCATE {lista} RESTART IDENTITY CASCADE")
        conn.commit()
        restante = _conteos(conn, set(objetivo))
        print(f"\nLISTO: {len(objetivo)} tablas vaciadas. Filas restantes en objetivo: "
              f"{sum(restante.values())} (esperado 0).")
        return 0 if not restante else 1


def main(argv: list[str] | None = None) -> int:
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(
        description="Vaciar los datos de negocio de un tenant (deja login/params legales/migraciones)."
    )
    parser.add_argument("--slug", required=True, help="Slug del tenant (p. ej. pim → base ferrebot_pim)")
    parser.add_argument("--confirmar", action="store_true",
                        help="Ejecuta el TRUNCATE. Sin esto es DRY-RUN (no borra).")
    parser.add_argument("--prod", action="store_true",
                        help="Carga .env.prod (Railway) antes de resolver la URL. Default: .env local.")
    args = parser.parse_args(argv)

    if args.prod:
        from tools._prodenv import cargar_env_prod
        cargar_env_prod()

    settings = get_settings()
    conn_url = tenant_url(settings.tenants_direct_url_base, _db_name(args.slug))
    destino = conn_url.rsplit("@", 1)[-1]  # host/db SIN credenciales (para el aviso)
    entorno = "PRODUCCIÓN" if args.prod else "local"
    modo = "BORRAR" if args.confirmar else "DRY-RUN"
    print(f"[{entorno}] {modo} datos de negocio del tenant '{args.slug}' → {destino}\n")

    try:
        return vaciar(conn_url, confirmar=args.confirmar)
    except Exception as exc:  # noqa: BLE001 — CLI: cualquier fallo → exit 1 con mensaje claro
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
