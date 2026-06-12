"""Switch del número Kapso demo entre tenants en UN comando (plan superficie pública §6).

Re-apunta el número demo de un negocio a otro y deja el canal limpio para la siguiente demo:

    python -m tools.switch_demo <vertical|slug> [--list] [--phone-number-id ...]

Hace, en orden: (1) resuelve el tenant destino (acepta el alias de vertical `barberia` → `barberia-demo`
o el slug completo); (2) upsert de `wa_numeros` re-apuntando el número (reusa `seed_wa_numero`);
(3) limpia `MemoriaWa` en Redis — las conversaciones (`wa:conv:{empresa_id}:*`) del tenant que deja Y
del que recibe, para que el agente NO arrastre el hilo del negocio anterior; (4) imprime tenant activo,
nombre del negocio y packs encendidos. `--list` muestra el mapeo actual sin tocar nada. Idempotente:
switch al mismo tenant = no-op (no reescribe ni limpia).

El número demo por defecto es `1176767388843502` (+57 320 6213221), overrideable por `--phone-number-id`
o la env `KAPSO_DEMO_PHONE_NUMBER_ID`. El borrado en Redis es por patrón con SCAN (`scan_iter`); NUNCA
`FLUSHDB`.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from typing import Any, Protocol

import psycopg
from psycopg.rows import dict_row

from core.config import get_settings
from core.db.urls import to_libpq
from core.logging import configure_logging, get_logger
from core.tenancy.catalogo import capacidades_completas
from tools.seed_wa_numero import upsert_wa_numero

log = get_logger("switch_demo")

DEFAULT_PHONE_NUMBER_ID = "1176767388843502"  # +57 320 6213221, el único número demo (Kapso)
ENV_PHONE = "KAPSO_DEMO_PHONE_NUMBER_ID"


class SwitchError(Exception):
    """Destino inválido: no existe un tenant para el vertical/slug pedido."""


@dataclass(frozen=True, slots=True)
class EmpresaRef:
    """Referencia mínima de un tenant para el switch (nombre = comercial si lo hay, si no el legal)."""

    id: int
    slug: str
    nombre: str


@dataclass(frozen=True, slots=True)
class ResultadoSwitch:
    destino: EmpresaRef
    anterior: EmpresaRef | None
    cambiado: bool
    conversaciones_limpiadas: int
    capacidades: frozenset[str]


class ControlRepo(Protocol):
    """Puerto al control DB; se inyecta un fake en tests (sin DB real)."""

    def buscar_por_slug(self, slug: str) -> EmpresaRef | None: ...
    def empresa_actual(self, phone_number_id: str) -> EmpresaRef | None: ...
    def reapuntar(self, phone_number_id: str, empresa_id: int) -> None: ...
    def capacidades(self, empresa_id: int) -> frozenset[str]: ...


# --- Lógica (pura sobre los puertos: testeable con fakes) ---

def resolver_destino(repo: ControlRepo, ident: str) -> EmpresaRef:
    """Resuelve `ident` a un tenant: primero como slug exacto, luego como alias de vertical (`-demo`)."""
    empresa = repo.buscar_por_slug(ident)
    if empresa is None and not ident.endswith("-demo"):
        empresa = repo.buscar_por_slug(f"{ident}-demo")
    if empresa is None:
        raise SwitchError(
            f"No existe un tenant '{ident}' (ni '{ident}-demo') en el control DB."
        )
    return empresa


def limpiar_memoria(redis_client: Any, empresa_ids: set[int]) -> int:
    """Borra `wa:conv:{empresa_id}:*` por SCAN para cada empresa. Devuelve cuántas llaves borró.

    SCAN + DELETE por patrón acotado a la empresa: nunca toca otras familias de llaves ni `FLUSHDB`.
    """
    total = 0
    for empresa_id in empresa_ids:
        llaves = list(redis_client.scan_iter(match=f"wa:conv:{empresa_id}:*"))
        if llaves:
            redis_client.delete(*llaves)
            total += len(llaves)
    return total


def ejecutar_switch(
    repo: ControlRepo, redis_client: Any, *, phone_number_id: str, ident: str
) -> ResultadoSwitch:
    """Re-apunta el número al tenant destino y limpia la memoria de origen+destino. Idempotente."""
    destino = resolver_destino(repo, ident)
    anterior = repo.empresa_actual(phone_number_id)

    if anterior is not None and anterior.id == destino.id:
        log.info("switch_demo_no_op", phone_number_id=phone_number_id, slug=destino.slug)
        return ResultadoSwitch(
            destino=destino, anterior=anterior, cambiado=False,
            conversaciones_limpiadas=0,
            capacidades=capacidades_completas(repo.capacidades(destino.id)),
        )

    repo.reapuntar(phone_number_id, destino.id)
    ids = {destino.id} | ({anterior.id} if anterior is not None else set())
    limpiadas = limpiar_memoria(redis_client, ids)
    log.info(
        "switch_demo_cambiado", phone_number_id=phone_number_id, slug=destino.slug,
        desde=anterior.slug if anterior else None, conversaciones_limpiadas=limpiadas,
    )
    return ResultadoSwitch(
        destino=destino, anterior=anterior, cambiado=True,
        conversaciones_limpiadas=limpiadas,
        capacidades=capacidades_completas(repo.capacidades(destino.id)),
    )


def consultar(
    repo: ControlRepo, *, phone_number_id: str
) -> tuple[EmpresaRef | None, frozenset[str]]:
    """Lee el mapeo actual del número (para `--list`). No escribe nada."""
    actual = repo.empresa_actual(phone_number_id)
    if actual is None:
        return None, frozenset()
    return actual, capacidades_completas(repo.capacidades(actual.id))


# --- Adaptador real al control DB ---

class PsycopgControlRepo:
    """Implementación de `ControlRepo` sobre una conexión psycopg al control DB (row_factory=dict_row)."""

    _SELECT = (
        "SELECT e.id, e.slug, e.nombre, b.nombre_comercial "
        "FROM empresas e LEFT JOIN branding b ON b.empresa_id = e.id "
    )

    def __init__(self, conn: psycopg.Connection) -> None:
        self._c = conn

    @staticmethod
    def _ref(row: dict | None) -> EmpresaRef | None:
        if row is None:
            return None
        return EmpresaRef(
            id=row["id"], slug=row["slug"], nombre=row["nombre_comercial"] or row["nombre"]
        )

    def buscar_por_slug(self, slug: str) -> EmpresaRef | None:
        return self._ref(self._c.execute(self._SELECT + "WHERE e.slug = %s", (slug,)).fetchone())

    def empresa_actual(self, phone_number_id: str) -> EmpresaRef | None:
        row = self._c.execute(
            self._SELECT
            + "JOIN wa_numeros w ON w.empresa_id = e.id "
            + "WHERE w.phone_number_id = %s AND w.estado = 'activo'",
            (phone_number_id,),
        ).fetchone()
        return self._ref(row)

    def reapuntar(self, phone_number_id: str, empresa_id: int) -> None:
        upsert_wa_numero(self._c, phone_number_id, empresa_id)

    def capacidades(self, empresa_id: int) -> frozenset[str]:
        """Features efectivas = features del plan ± overrides (espeja `ControlCapacidades.efectivas`)."""
        plan = self._c.execute(
            "SELECT p.limites FROM empresas e JOIN planes p ON p.id = e.plan_id WHERE e.id = %s",
            (empresa_id,),
        ).fetchone()
        efectivas: set[str] = set()
        if plan is not None and plan["limites"] is not None:
            limites = plan["limites"] if isinstance(plan["limites"], dict) else json.loads(plan["limites"])
            efectivas = set(limites.get("features", []))
        for row in self._c.execute(
            "SELECT feature, habilitada FROM empresa_features WHERE empresa_id = %s", (empresa_id,)
        ).fetchall():
            if row["habilitada"]:
                efectivas.add(row["feature"])
            else:
                efectivas.discard(row["feature"])
        return frozenset(efectivas)


# --- Presentación (stdout para el operador, como los otros tools de CLI) ---

def _fmt_packs(caps: frozenset[str]) -> str:
    return ", ".join(sorted(caps)) if caps else "(ninguno)"


def _imprimir_estado(phone_number_id: str, actual: EmpresaRef | None, caps: frozenset[str]) -> None:
    print(f"Número demo  phone_number_id={phone_number_id}")
    if actual is None:
        print("  Tenant activo: (sin mapear)")
        return
    print(f"  Tenant activo: {actual.slug} — {actual.nombre}")
    print(f"  Packs encendidos: {_fmt_packs(caps)}")


def _imprimir_switch(phone_number_id: str, res: ResultadoSwitch) -> None:
    print(f"Switch del número demo  phone_number_id={phone_number_id}")
    antes = f"{res.anterior.slug} — {res.anterior.nombre}" if res.anterior else "(sin mapear)"
    print(f"  Antes:  {antes}")
    if not res.cambiado:
        print(f"  Sin cambios: el número ya estaba en {res.destino.slug} (no-op, nada que limpiar).")
    else:
        print(f"  Ahora:  {res.destino.slug} — {res.destino.nombre}")
        print(f"  Memoria WhatsApp limpiada: {res.conversaciones_limpiadas} conversación(es)")
    print(f"  Tenant activo: {res.destino.slug} — {res.destino.nombre}")
    print(f"  Packs encendidos: {_fmt_packs(res.capacidades)}")


# --- Wiring / CLI ---

def run(repo: ControlRepo, redis_client: Any, argv: list[str]) -> int:
    """Núcleo del CLI con deps inyectadas (repo + redis): parsea args, ejecuta e imprime. Testeable."""
    parser = argparse.ArgumentParser(
        prog="python -m tools.switch_demo",
        description="Re-apunta el número Kapso demo a otro tenant y limpia su memoria en Redis.",
    )
    parser.add_argument("destino", nargs="?", help="vertical (barberia) o slug completo (barberia-demo)")
    parser.add_argument("--list", action="store_true", dest="listar",
                        help="muestra el mapeo actual sin tocar nada")
    parser.add_argument("--phone-number-id", default=None,
                        help=f"override del número demo (default: env {ENV_PHONE} o {DEFAULT_PHONE_NUMBER_ID})")
    args = parser.parse_args(argv)
    phone_number_id = args.phone_number_id or os.environ.get(ENV_PHONE) or DEFAULT_PHONE_NUMBER_ID

    if args.listar:
        actual, caps = consultar(repo, phone_number_id=phone_number_id)
        _imprimir_estado(phone_number_id, actual, caps)
        return 0

    if not args.destino:
        print("ERROR: indica un vertical/slug destino, o usa --list para ver el mapeo.", file=sys.stderr)
        return 2

    try:
        res = ejecutar_switch(repo, redis_client, phone_number_id=phone_number_id, ident=args.destino)
    except SwitchError as exc:
        log.error("switch_demo_destino_invalido", destino=args.destino, error=str(exc))
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    _imprimir_switch(phone_number_id, res)
    return 0


def _cliente_redis() -> Any:
    """Cliente Redis sincrónico (perezoso): importa `redis` solo al invocar; SCAN/DELETE, nunca FLUSHDB."""
    import redis

    return redis.from_url(get_settings().redis_url, decode_responses=True)


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    argv = sys.argv[1:] if argv is None else argv
    control_url = to_libpq(get_settings().control_database_url)
    with psycopg.connect(control_url, row_factory=dict_row, autocommit=True) as conn:
        return run(PsycopgControlRepo(conn), _cliente_redis(), argv)


if __name__ == "__main__":
    sys.exit(main())
