"""Provisionador idempotente de un paso desde un manifiesto de tenant (ADR 0007 §D5).

Orquesta toda la coreografía de alta de un cliente:
  1) carga + valida el manifiesto (falla cerrado: si no valida, NO escribe nada);
  2) BASE — reusa `provision_tenant.provision_tenant_full` (CREATE DATABASE → control con URL cifrada
     → upgrade head → admin → secretos/config/branding → plan/empresa_features). No duplica su SQL;
  3) PACKS — corre el loader de cada pack activo (registry) sobre la BD del tenant, con UN solo commit
     al final (si un pack falla, rollback de los packs y se relanza el error);
  4) CANAL — si hay `canal.whatsapp`, mapea `wa_numeros` reusando `tools.seed_wa_numero.seed`;
  5) VERIFICA — smoke read-only (conteos por tabla + wa_numeros) e imprime UN resumen de una línea.

NO hay transacción global, y es a propósito: `provision_tenant_full` hace `CREATE DATABASE` (no
transaccionable) y toca el control DB; los packs van en la BD del tenant (otra conexión); `wa_numeros`
en el control DB. La garantía es IDEMPOTENCIA, no atomicidad: re-correr el comando completo tras un
fallo parcial es seguro y converge al mismo estado (todo upsert por clave natural).

    python -m tools.provision_from_manifest --from tools/onboarding/clinica-demo.yaml
"""
from __future__ import annotations

import argparse
import sys
from collections.abc import Callable

import psycopg
from psycopg.rows import dict_row

from core.config import get_settings
from core.db.urls import tenant_url, to_libpq
from core.logging import configure_logging, get_logger, tenant_id_var
from core.tenancy.catalogo import capacidades_completas
from tools.manifest import cargar_manifiesto, validar
from tools.manifest.packs.registry import packs_activos
from tools.manifest.schema import Manifiesto
from tools.provision_tenant import _db_name, _features_efectivas, provision_tenant_full
from tools.seed_wa_numero import seed as mapear_wa_numero

log = get_logger("provision_from_manifest")


def _datos_base(m: Manifiesto) -> dict:
    """Mapea las secciones del manifiesto al dict `datos` que espera `provision_tenant_full`."""
    return {
        "slug": m.identidad.slug,
        "nombre": m.identidad.nombre,
        "nit": m.identidad.nit,
        "admin": {"nombre": m.admin.nombre, "telegram_id": m.admin.telegram_id, "email": m.admin.email},
        "plan": {"nombre": m.plan.nombre, "features": list(m.plan.features)} if m.plan else None,
        "features_override": dict(m.features_override),
        "secretos": dict(m.secretos),
        "config": dict(m.config),
        "branding": {
            "color_primario": m.branding.color_primario,
            "nombre_comercial": m.branding.nombre_comercial,
            "logo_url": m.branding.logo_url,
            "dominio": m.branding.dominio,
            "tema": m.branding.tema,
        },
    }


def _efectivas(m: Manifiesto) -> frozenset[str]:
    """Set EFECTIVO de capacidades (igual que `validacion`): NÚCLEO ∪ (plan ± overrides)."""
    plan_features = list(m.plan.features) if m.plan else []
    return capacidades_completas(_features_efectivas(plan_features, m.features_override))


def _seccion_pack(m: Manifiesto, flag: str):
    """Sección del manifiesto que alimenta un pack: 'pack_agenda' → m.packs.agenda, etc."""
    return getattr(m.packs, flag.removeprefix("pack_"))


def _cargar_packs(m: Manifiesto, conn_url: str, efectivas: frozenset[str]) -> None:
    """Corre el loader de cada pack ACTIVO sobre la BD del tenant. UN commit al final; rollback si falla."""
    activos = packs_activos(efectivas)
    if not activos:
        return
    with psycopg.connect(to_libpq(conn_url), row_factory=dict_row) as conn:
        try:
            for pack in activos:
                if pack.loader is None:
                    continue  # pack estructural (p. ej. `pos`): sus tablas las crea la migración, sin datos
                seccion = _seccion_pack(m, pack.flag)
                if seccion is None:
                    continue  # flag activo sin datos declarados: válido, el negocio los carga luego
                pack.loader(seccion, conn)
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def _count(conn, tabla: str) -> int:
    return conn.execute(f"SELECT count(*) AS n FROM {tabla}").fetchone()["n"]


def _resumen(conn_url: str, efectivas: frozenset[str], slug: str, phone_number_id: str | None) -> str:
    """Smoke read-only: conteos por tabla del tenant (+ wa) en una línea."""
    partes: list[str] = []
    with psycopg.connect(to_libpq(conn_url), row_factory=dict_row) as conn:
        if "pack_agenda" in efectivas:
            partes.append(f"{_count(conn, 'servicios')} servicios")
            partes.append(f"{_count(conn, 'recursos')} recursos")
            partes.append(f"{_count(conn, 'disponibilidad')} disponibilidad")
        if "pack_faq" in efectivas:
            partes.append(f"{_count(conn, 'conocimiento')} faq")
        if "pos" in efectivas:
            partes.append(f"{_count(conn, 'productos')} productos")
        if "pack_pedidos" in efectivas:
            partes.append(f"{_count(conn, 'zonas_domicilio')} zonas")
        # Tablas fiscales/POS: base del tenant (las migraciones aplican a TODAS las empresas,
        # tenancy.md §7), así que se cuentan siempre — verifica que el esquema fiscal quedó aplicado
        # en el alta, aunque en un tenant recién provisionado estén en cero.
        partes.append(f"{_count(conn, 'facturas_electronicas')} facturas_electronicas")
        partes.append(f"{_count(conn, 'webhooks_matias_recibidos')} webhooks_matias")
    if phone_number_id:
        partes.append(f"wa:{phone_number_id}")
    return f"provision_manifest: {slug} OK -> " + ", ".join(partes)


def provision_from_manifest_obj(
    manifiesto: Manifiesto, *, on_resumen: Callable[[str], None] | None = None
) -> int:
    """Coreografía del alta (base→packs→wa_numero→verifica) sobre un manifiesto YA validado. Idempotente.

    Recibe el manifiesto PARSEADO (no una ruta): así el job del worker lo usa con el objeto en memoria y
    NUNCA escribe un manifiesto con secretos a un archivo temporal en disco (ADR 0010 §Guardarraíles v1).
    El llamador valida ANTES (`provision_from_manifest` carga+valida; el job re-parsea+re-valida).

    `on_resumen`: si se pasa, recibe la línea-resumen del provisionador (la consume el job para su estado);
    si no, se imprime (comportamiento del CLI). Devuelve el `empresa_id`.
    """
    slug = manifiesto.identidad.slug

    # 2) BASE (reusa provision_tenant_full; idempotente).
    empresa_id = provision_tenant_full(_datos_base(manifiesto))
    tenant_id_var.set(empresa_id)  # liga el tenant_id al logging estructurado
    log.info("manifest_base_ok", slug=slug, empresa_id=empresa_id)

    efectivas = _efectivas(manifiesto)
    settings = get_settings()
    conn_url = tenant_url(settings.tenants_direct_url_base, _db_name(slug))

    # 3) PACKS activos (BD del tenant; un commit al final).
    _cargar_packs(manifiesto, conn_url, efectivas)

    # 4) CANAL — wa_numeros en el control DB (conexión aparte, reusa seed_wa_numero).
    phone_number_id: str | None = None
    wa = manifiesto.canal.whatsapp
    if wa is not None:
        rc = mapear_wa_numero(wa.phone_number_id, slug, numero=wa.numero, waba_id=wa.waba_id)
        if rc != 0:
            raise RuntimeError(f"no se pudo mapear wa_numeros (slug={slug}, rc={rc})")
        phone_number_id = wa.phone_number_id

    # 5) VERIFICA + resumen de una línea.
    resumen = _resumen(conn_url, efectivas, slug, phone_number_id)
    log.info("manifest_provision_ok", slug=slug, empresa_id=empresa_id)
    if on_resumen is not None:
        on_resumen(resumen)
    else:
        print(resumen)
    return empresa_id


def provision_from_manifest(path: str) -> int:
    """Aprovisiona una empresa completa desde su manifiesto (RUTA) y devuelve su `empresa_id`. Idempotente.

    Falla cerrado: carga + valida el manifiesto ANTES de tocar la BD; si no valida, lanza y no escribe nada.
    """
    manifiesto = cargar_manifiesto(path)
    validar(manifiesto)  # ErrorManifiesto → aborta sin escribir
    return provision_from_manifest_obj(manifiesto)


def check_manifest(path: str) -> None:
    """Carga + valida un manifiesto SIN tocar ninguna base (ADR 0011 F1b §--check).

    Es el primer escalón del flujo Cowork (docs/runbook-onboarding-cowork.md): el operador escribe el
    YAML y corre `--check` hasta que valide, antes de aplicar por el panel o `railway ssh`. Reúsa el
    MISMO `cargar_manifiesto`+`validar` que el provisionador (un solo ground truth); lanza si no valida.
    """
    manifiesto = cargar_manifiesto(path)
    validar(manifiesto)  # ErrorManifiesto agrupado → el operador corrige contra el insumo


def main(argv: list[str] | None = None) -> int:
    # La consola de Windows usa cp1252: forzar UTF-8 para que acentos/símbolos no revienten.
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    configure_logging()
    parser = argparse.ArgumentParser(description="Aprovisionar una empresa desde un manifiesto (ADR 0007).")
    parser.add_argument("--from", dest="archivo", required=True, help="Ruta al manifiesto (YAML o JSON)")
    parser.add_argument(
        "--check", action="store_true",
        help="Solo validar el manifiesto (imprime VALIDO/errores). NO provisiona ni toca ninguna base.",
    )
    args = parser.parse_args(argv)

    # --check: validación pura (sin BD). Exit 0 = VALIDO; exit 1 = errores agrupados a stderr.
    if args.check:
        try:
            check_manifest(args.archivo)
        except Exception as exc:  # noqa: BLE001 — CLI: cualquier fallo → exit 1 con mensaje claro
            print(f"INVALIDO: {exc}", file=sys.stderr)
            return 1
        print("VALIDO")
        return 0

    try:
        provision_from_manifest(args.archivo)
    except Exception as exc:  # noqa: BLE001 — CLI: cualquier fallo → exit!=0 con mensaje claro
        log.error("manifest_provision_fallo", error=str(exc))
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
