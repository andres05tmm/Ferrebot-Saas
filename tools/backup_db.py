"""Respaldo de PRODUCCIÓN: `pg_dump -Fc` del control DB + cada tenant, con restore PROBADO.

Railway no trae backups nativos en este plan; esto los genera. Es SOLO LECTURA sobre prod (`pg_dump`
no escribe en el origen). Carga `.env.prod` (URLs públicas de Railway) vía `tools._prodenv`.

Uso:
    python -m tools.backup_db                                  # vuelca control + tenants → backups/<ts>/
    python -m tools.backup_db --podar 8                        # backup + borra carpetas > 8 semanas
    python -m tools.backup_db --force                          # ignora BACKUP_ENABLED (corrida manual)
    python -m tools.backup_db --verify <archivo.dump> --scratch postgresql://.../scratch_verify

Respaldo automático: el flujo de backup está GATED por `BACKUP_ENABLED` (settings.backup_enabled,
apagado por defecto). Sin `--force`, si está en off imprime un aviso y termina con éxito (return 0)
para no alarmar al scheduler. El restore-verify (`--verify`) NO pasa por el gate.

Versión de pg_dump (gotcha): el servidor de Railway (PG 18.x) es MÁS NUEVO que tu `pg_dump` local, y
pg_dump aborta si su versión es menor que la del servidor. Apunta `PG_DUMP`/`PG_RESTORE` a una imagen
Docker para fijar la versión (no necesita montar volúmenes: el dump viaja por stdout/stdin):

    PG_DUMP="docker run --rm postgres:18 pg_dump"
    PG_RESTORE="docker run --rm -i postgres:18 pg_restore"   # -i: lee el .dump por stdin

El driver y el patrón espejan tools/migrate_tenants.py y tools/provision_tenant.py (psycopg + to_libpq).
"""
from __future__ import annotations

import argparse
import os
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

import psycopg
from psycopg.rows import dict_row

from core.config import get_settings
from core.db.urls import tenant_url, to_libpq
from tools._prodenv import cargar_env_prod

# Comandos externos, configurables por env para apuntarlos a Docker (ver docstring del módulo).
_PG_DUMP_DEFAULT = "pg_dump"
_PG_RESTORE_DEFAULT = "pg_restore"

# Tablas clave para verificar un restore: se cuentan las que existan en la base scratch (control y
# tenant tienen tablas distintas; cada dump trae unas u otras). "un backup no probado no es un backup".
_TABLAS_CLAVE = (
    # control DB
    "empresas", "planes", "branding", "secretos_empresa", "config_empresa",
    # tenant DB
    "usuarios", "productos", "ventas", "ventas_detalle", "clientes", "movimientos_inventario",
)

_DIR_BACKUPS = Path("backups")

# Retención por defecto de la copia off-site cuando se corre sin `--podar` (el wrapper sí pasa `--podar 8`).
_OFFSITE_KEEP_DEFAULT = 8


class BackupError(RuntimeError):
    """Fallo de respaldo/restauración con mensaje accionable (incluye la pista de Docker)."""


@dataclass(frozen=True)
class Objetivo:
    """Una base a respaldar: su nombre, la URL de conexión y el archivo .dump destino (relativo)."""

    db_name: str
    url: str
    archivo: str


# --------------------------- helpers PUROS (testeables) -------------------

def marca_tiempo(ahora: datetime | None = None) -> str:
    """Timestamp UTC apto para nombre de carpeta (sin ':' — Windows). Ej: `20260607T123000Z`."""
    ahora = ahora or datetime.now(timezone.utc)
    return ahora.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def nombre_db_de_url(url: str) -> str:
    """Nombre de la base de una URL postgres (primer segmento del path, sin query)."""
    path = urlsplit(url).path.lstrip("/")
    return path.split("/")[0] if path else ""


def _parsear_marca(nombre: str) -> datetime | None:
    """Parsea un nombre de carpeta `YYYYMMDDTHHMMSSZ` → datetime UTC; None si no encaja."""
    try:
        return datetime.strptime(nombre, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def podar_backups(
    dir_backups: Path, keep_semanas: int, ahora: datetime | None = None
) -> list[Path]:
    """Carpetas de backup más viejas que `keep_semanas` (no borra; devuelve la lista).

    Parsea el nombre (marca_tiempo `YYYYMMDDTHHMMSSZ`); ignora nombres que no parseen (p. ej. `logs/`).
    PURO y testeable: con `ahora` y fechas fijas el resultado es determinista."""
    ahora = ahora or datetime.now(timezone.utc)
    limite = ahora - timedelta(weeks=keep_semanas)
    if not dir_backups.exists():
        return []
    viejas: list[Path] = []
    for hijo in sorted(dir_backups.iterdir()):
        if not hijo.is_dir():
            continue
        ts = _parsear_marca(hijo.name)
        if ts is not None and ts < limite:
            viejas.append(hijo)
    return viejas


def copiar_offsite(
    src_dir: Path, dest_root: Path, keep_semanas: int, ahora: datetime | None = None
) -> Path:
    """Copia la carpeta de backup `src_dir` a `dest_root/<nombre>` (shutil.copytree) y poda en
    `dest_root` las copias más viejas que `keep_semanas` (reusa `podar_backups`).

    Devuelve la ruta copiada. Lanza `FileNotFoundError` si `dest_root` no existe/no está montada
    (p. ej. Google Drive for Desktop sin sincronizar): el llamador lo trata como off-site omitido."""
    if not dest_root.is_dir():
        raise FileNotFoundError(f"destino off-site no montado: {dest_root}")
    destino = dest_root / src_dir.name
    shutil.copytree(src_dir, destino, dirs_exist_ok=True)
    for carpeta in podar_backups(dest_root, keep_semanas, ahora):
        shutil.rmtree(carpeta)
    return destino


def _nombre_archivo(db_name: str) -> str:
    return f"{db_name}.dump"


def planear_backup(control_url: str, tenants_base: str, tenant_db_names: list[str]) -> list[Objetivo]:
    """Objetivos a respaldar: control DB + cada tenant, con sus nombres de archivo. PURO (no toca PG).

    El control va primero; cada tenant se compone como `{tenants_base}/{db_name}` (igual que provision)."""
    control_db = nombre_db_de_url(control_url)
    objetivos = [Objetivo(control_db, control_url, _nombre_archivo(control_db))]
    for db_name in tenant_db_names:
        objetivos.append(Objetivo(db_name, tenant_url(tenants_base, db_name), _nombre_archivo(db_name)))
    return objetivos


def tamano_humano(n_bytes: int) -> str:
    """Tamaño legible (B/KB/MB/GB)."""
    valor = float(n_bytes)
    for unidad in ("B", "KB", "MB", "GB"):
        if valor < 1024 or unidad == "GB":
            return f"{valor:.0f} {unidad}" if unidad == "B" else f"{valor:.1f} {unidad}"
        valor /= 1024
    return f"{valor:.1f} GB"


# --------------------------- acceso al control DB -------------------------

def listar_tenant_db_names(control_url: str) -> list[str]:
    """`db_name` de cada empresa (mismo patrón que migrate_tenants: lee el control DB)."""
    with psycopg.connect(to_libpq(control_url), row_factory=dict_row) as conn:
        filas = conn.execute(
            "SELECT t.db_name FROM empresas e JOIN tenant_databases t ON t.empresa_id = e.id "
            "WHERE e.estado IN ('activa', 'suspendida') ORDER BY e.id"
        ).fetchall()
    return [f["db_name"] for f in filas]


# --------------------------- subprocess (pg_dump/restore) -----------------

def _hint_docker() -> str:
    return (
        "\nUsa Docker para fijar la versión del cliente (servidor PG 18.x):\n"
        '  PG_DUMP="docker run --rm postgres:18 pg_dump"\n'
        '  PG_RESTORE="docker run --rm -i postgres:18 pg_restore"'
    )


def _ejecutar(cmd: list[str], *, stdout=None, stdin=None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, stdout=stdout, stdin=stdin, stderr=subprocess.PIPE, check=False)
    except FileNotFoundError as exc:
        raise BackupError(f"No se encontró '{cmd[0]}'.{_hint_docker()}") from exc


def _solo_host(url: str) -> str:
    """host:puerto/db de la URL, SIN credenciales (para mensajes/logs; nunca el password)."""
    return url.rsplit("@", 1)[-1]


def _pg_dump(dump_cmd: list[str], url: str, destino: Path) -> int:
    """Vuelca `url` a `destino` en formato custom (-Fc), por stdout (no requiere montar volúmenes en
    Docker). Devuelve el tamaño del archivo. Borra el archivo parcial si pg_dump falla."""
    destino.parent.mkdir(parents=True, exist_ok=True)
    args = dump_cmd + ["-Fc", "--no-owner", "--no-privileges", "-d", to_libpq(url)]
    with destino.open("wb") as f:
        cp = _ejecutar(args, stdout=f)
    if cp.returncode != 0:
        destino.unlink(missing_ok=True)
        err = cp.stderr.decode(errors="replace").strip()
        hint = _hint_docker() if "version" in err.lower() else ""
        raise BackupError(f"pg_dump falló para {_solo_host(url)}:\n{err}{hint}")
    return destino.stat().st_size


# --------------------------- comandos -------------------------------------

def backup_all(*, pg_dump: str, dir_backups: Path = _DIR_BACKUPS, ahora: datetime | None = None) -> Path:
    """Respalda control DB + todos los tenants a `dir_backups/<timestamp>/`. Devuelve esa carpeta."""
    settings = get_settings()
    dump_cmd = shlex.split(pg_dump)
    destino_dir = dir_backups / marca_tiempo(ahora)
    tenant_dbs = listar_tenant_db_names(settings.control_database_url)
    objetivos = planear_backup(
        settings.control_database_url, settings.tenants_direct_url_base, tenant_dbs
    )
    print(f"Respaldo → {destino_dir}  ({len(objetivos)} bases)")
    total = 0
    for obj in objetivos:
        tam = _pg_dump(dump_cmd, obj.url, destino_dir / obj.archivo)
        total += tam
        print(f"  ✓ {obj.archivo}  ({tamano_humano(tam)})")
    print(f"Listo: {len(objetivos)} archivos, {tamano_humano(total)} en {destino_dir}")
    return destino_dir


def _contar_tablas(url: str, tablas: tuple[str, ...]) -> dict[str, int]:
    """Cuenta filas de las `tablas` que EXISTAN en `url` (omite las ausentes)."""
    conteos: dict[str, int] = {}
    with psycopg.connect(to_libpq(url), row_factory=dict_row) as conn:
        existentes = {
            r["table_name"]
            for r in conn.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema='public'"
            ).fetchall()
        }
        for tabla in tablas:
            if tabla in existentes:
                conteos[tabla] = conn.execute(f'SELECT count(*) AS n FROM "{tabla}"').fetchone()["n"]
    return conteos


def restore_verify(
    dump_path: Path, scratch_url: str, *, pg_restore: str, tablas: tuple[str, ...] = _TABLAS_CLAVE
) -> dict[str, int]:
    """Restaura `dump_path` en la base scratch y cuenta tablas clave (la prueba de que sirve).

    Restaura con --clean --if-exists (idempotente sobre una scratch ya usada). Lee el .dump por stdin
    para no montar volúmenes en Docker. Imprime y devuelve los conteos."""
    if not dump_path.exists():
        raise BackupError(f"No existe el dump: {dump_path}")
    restore_cmd = shlex.split(pg_restore)
    args = restore_cmd + [
        "--clean", "--if-exists", "--no-owner", "--no-privileges", "-d", to_libpq(scratch_url)
    ]
    with dump_path.open("rb") as f:
        cp = _ejecutar(args, stdin=f)
    if cp.returncode != 0:
        # --clean sobre una base vacía emite avisos (DROP de objetos inexistentes) → no es fatal.
        # Si fue un fallo real, los conteos de abajo lo delatan (0 o tabla ausente).
        err = cp.stderr.decode(errors="replace").strip()
        if "version" in err.lower() and "unsupported" in err.lower():
            raise BackupError(f"pg_restore: versión incompatible.\n{err}{_hint_docker()}")
        print(f"Avisos de pg_restore (no fatales):\n{err}", file=sys.stderr)
    conteos = _contar_tablas(scratch_url, tablas)
    print(f"Restore verificado en {_solo_host(scratch_url)} — conteos:")
    for tabla, n in conteos.items():
        print(f"  {tabla}: {n}")
    if not conteos:
        raise BackupError("El restore no dejó ninguna tabla clave: el backup NO sirve.")
    return conteos


def _correr_respaldo(args: argparse.Namespace) -> int:
    """Rama de respaldo: carga `.env.prod`, aplica el gate BACKUP_ENABLED y respalda (+ poda opcional).

    El gate va DESPUÉS de `cargar_env_prod()` para que `backup_enabled` salga de `.env.prod` (no del
    `.env` local). Sin `--force`, si está apagado termina con éxito (return 0) para no alarmar al
    scheduler. La poda (`--podar`) corre solo si el backup procedió. El off-site (BACKUP_OFFSITE_DIR)
    se intenta al final: si la carpeta no está montada, avisa y sigue (el backup local ya está)."""
    pg_dump = os.environ.get("PG_DUMP", _PG_DUMP_DEFAULT)
    cargar_env_prod()   # el respaldo SÍ corre contra prod (URLs públicas de Railway + BACKUP_ENABLED)
    settings = get_settings()
    if not args.force and not settings.backup_enabled:
        print("backup deshabilitado (BACKUP_ENABLED=off)")
        return 0
    destino = backup_all(pg_dump=pg_dump, dir_backups=Path(args.dir))
    if args.podar is not None:
        viejas = podar_backups(Path(args.dir), args.podar)
        for carpeta in viejas:
            shutil.rmtree(carpeta)
            print(f"  ✗ podado {carpeta} (> {args.podar} semanas)")
        print(f"Poda: {len(viejas)} carpeta(s) eliminada(s) (retención {args.podar} semanas)")
    if settings.backup_offsite_dir:
        _copiar_offsite_seguro(destino, settings.backup_offsite_dir, args.podar)
    return 0


def _copiar_offsite_seguro(destino: Path, offsite_dir: str, keep_semanas: int | None) -> None:
    """Copia `destino` a la carpeta off-site reusando la retención de `--podar` (default si es None).

    NUNCA hace fallar el backup local: si el off-site lanza (Drive no montado, permisos, copia
    parcial), imprime un AVISO y vuelve. Reusa `copiar_offsite` para la copia + poda en destino."""
    keep = keep_semanas if keep_semanas is not None else _OFFSITE_KEEP_DEFAULT
    try:
        copia = copiar_offsite(destino, Path(offsite_dir), keep)
        print(f"Off-site: copiado a {copia} (retención {keep} semanas)")
    except (OSError, shutil.Error) as exc:
        print(f"AVISO: off-site omitido: {exc}")


def main(argv: list[str] | None = None) -> int:
    # La consola no interactiva de Task Scheduler usa cp1252: los símbolos → / ✓ revientan con
    # UnicodeEncodeError. Forzar UTF-8 (defensivo: en consolas raras `reconfigure` puede no existir).
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(
        description="Respaldo de producción (pg_dump control+tenants) + restore probado."
    )
    parser.add_argument("--verify", metavar="DUMP", help="Restaura un .dump a la base scratch y cuenta tablas")
    parser.add_argument("--scratch", help="URL de la base scratch para --verify (p. ej. el Docker local)")
    parser.add_argument("--dir", default=str(_DIR_BACKUPS), help="Carpeta raíz de los backups")
    parser.add_argument("--podar", type=int, metavar="SEMANAS",
                        help="Tras el backup, borra las carpetas más viejas que SEMANAS")
    parser.add_argument("--force", action="store_true",
                        help="Ignora BACKUP_ENABLED=off (para corridas manuales)")
    args = parser.parse_args(argv)

    pg_restore = os.environ.get("PG_RESTORE", _PG_RESTORE_DEFAULT)

    try:
        if args.verify:
            # El restore-verify va contra la scratch EXPLÍCITA (no prod): no se carga .env.prod ni gate.
            if not args.scratch:
                parser.error("--verify requiere --scratch <url> (base de pruebas, p. ej. Docker local)")
            restore_verify(Path(args.verify), args.scratch, pg_restore=pg_restore)
        else:
            return _correr_respaldo(args)
    except (BackupError, FileNotFoundError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
