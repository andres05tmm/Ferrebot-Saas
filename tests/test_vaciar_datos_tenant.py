"""Vaciar datos de negocio de un tenant — partes PURAS y de control de flujo (sin Postgres).

Cubre lo testeable sin integración: qué tablas se conservan (base fija + `*config*`), la detección
del control DB (falla cerrado), y las ramas de `vaciar` (abortos, DRY-RUN, TRUNCATE) contra una
conexión falsa que registra el SQL. El TRUNCATE real es integración (se prueba a mano contra el
Docker local; ver el docstring del tool y docs/runbook.md).
"""
import re
from types import SimpleNamespace

import pytest

import tools.vaciar_datos_tenant as vdt
from tools.vaciar_datos_tenant import _es_control_db, _preservar, main, vaciar


# --------------------------- conjunto a conservar -------------------------

def test_preservar_incluye_base_fija_y_toda_tabla_config():
    todas = {
        "usuarios", "parametros_legales", "alembic_version",   # base fija
        "cobranza_config", "agenda_config", "config_empresa",  # *config* → se suman
        "ventas", "caja", "conversaciones_bot",                # negocio → NO se conservan
    }
    preservar = _preservar(todas)
    assert preservar == {
        "usuarios", "parametros_legales", "alembic_version",
        "cobranza_config", "agenda_config", "config_empresa",
    }
    assert not ({"ventas", "caja", "conversaciones_bot"} & preservar)


def test_preservar_sin_tablas_config_es_solo_la_base():
    assert _preservar({"usuarios", "ventas"}) == {
        "usuarios", "parametros_legales", "alembic_version",
    }


# --------------------------- detección del control DB ---------------------

@pytest.mark.parametrize("marcador", ["empresas", "tenant_databases", "identidades"])
def test_es_control_db_detecta_cada_marcador(marcador):
    assert _es_control_db({marcador, "usuarios"}) is True


def test_es_control_db_niega_un_tenant_normal():
    assert _es_control_db({"usuarios", "ventas", "caja"}) is False


# --------------------------- conexión falsa (sin Postgres) ----------------

class _Res:
    """Resultado mínimo tipo cursor: soporta fetchall()/fetchone()."""

    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class FakeConn:
    """Conexión falsa: responde el catálogo y los COUNT, registra el SQL ejecutado.

    Tras un TRUNCATE, los COUNT de las tablas truncadas devuelven 0 (simula el borrado), para que
    el chequeo `restante` de `vaciar` vea 0 filas como en la BD real.
    """

    def __init__(self, tablas: set[str], conteos: dict[str, int]):
        self.tablas = tablas
        self.conteos = dict(conteos)
        self.ejecutadas: list[str] = []
        self.commits = 0
        self._truncado = False

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql: str, *args):
        self.ejecutadas.append(sql)
        if "information_schema" in sql:
            return _Res([{"table_name": t} for t in self.tablas])
        if sql.lstrip().upper().startswith("SELECT COUNT"):
            tabla = re.search(r'FROM "([^"]+)"', sql).group(1)
            n = 0 if self._truncado else self.conteos.get(tabla, 0)
            return _Res([{"n": n}])
        if sql.lstrip().upper().startswith("TRUNCATE"):
            self._truncado = True
            return _Res([])
        return _Res([])

    def commit(self):
        self.commits += 1


def _stub_conn(monkeypatch, conn: FakeConn):
    """psycopg.connect(...) → la conexión falsa (ignora DSN y row_factory)."""
    monkeypatch.setattr(vdt.psycopg, "connect", lambda *a, **k: conn)


# --------------------------- ramas de vaciar ------------------------------

def test_vaciar_aborta_si_parece_control_db(monkeypatch, capsys):
    conn = FakeConn({"empresas", "tenant_databases", "usuarios"}, {})
    _stub_conn(monkeypatch, conn)
    assert vaciar("postgresql://x/y", confirmar=True) == 1
    assert not any("TRUNCATE" in s for s in conn.ejecutadas)   # nunca tocó datos
    assert "CONTROL DB" in capsys.readouterr().err


def test_vaciar_aborta_si_no_hay_usuarios(monkeypatch, capsys):
    conn = FakeConn({"ventas", "caja"}, {"ventas": 3})         # sin `usuarios` → no es un tenant
    _stub_conn(monkeypatch, conn)
    assert vaciar("postgresql://x/y", confirmar=True) == 1
    assert not any("TRUNCATE" in s for s in conn.ejecutadas)
    assert "usuarios" in capsys.readouterr().err


def test_vaciar_dry_run_no_trunca(monkeypatch, capsys):
    conn = FakeConn({"usuarios", "ventas", "caja"}, {"ventas": 5, "caja": 2})
    _stub_conn(monkeypatch, conn)
    assert vaciar("postgresql://x/y", confirmar=False) == 0
    assert not any("TRUNCATE" in s for s in conn.ejecutadas)   # DRY-RUN: no escribe
    assert conn.commits == 0
    assert "DRY-RUN" in capsys.readouterr().out


def test_vaciar_confirmar_trunca_solo_el_negocio_y_conserva_lo_demas(monkeypatch):
    conn = FakeConn(
        {"usuarios", "parametros_legales", "alembic_version", "cobranza_config",
         "ventas", "caja", "conversaciones_bot"},
        {"ventas": 5, "caja": 2, "conversaciones_bot": 3},
    )
    _stub_conn(monkeypatch, conn)
    assert vaciar("postgresql://x/y", confirmar=True) == 0

    truncate = next(s for s in conn.ejecutadas if s.startswith("TRUNCATE"))
    assert "RESTART IDENTITY CASCADE" in truncate
    # Se truncan las 3 de negocio, entrecomilladas; ninguna preservada aparece.
    for t in ("ventas", "caja", "conversaciones_bot"):
        assert f'"{t}"' in truncate
    for t in ("usuarios", "parametros_legales", "alembic_version", "cobranza_config"):
        assert f'"{t}"' not in truncate
    assert conn.commits == 1


def test_vaciar_confirmar_sin_datos_es_noop_exitoso(monkeypatch):
    # Todo lo que hay es preservado: no hay objetivo → sale 0 sin TRUNCATE ni commit.
    conn = FakeConn({"usuarios", "alembic_version", "cobranza_config"}, {})
    _stub_conn(monkeypatch, conn)
    assert vaciar("postgresql://x/y", confirmar=True) == 0
    assert not any("TRUNCATE" in s for s in conn.ejecutadas)
    assert conn.commits == 0


# --------------------------- wiring de main -------------------------------

def _stub_main(monkeypatch) -> list[dict]:
    """Aísla `main`: settings fijos, URL trivial y `vaciar` espía. Devuelve sus llamadas."""
    llamadas: list[dict] = []
    monkeypatch.setattr(vdt, "get_settings",
                        lambda: SimpleNamespace(tenants_direct_url_base="postgresql://u:p@h:5432"))
    monkeypatch.setattr(vdt, "tenant_url", lambda base, db: f"{base}/{db}")
    monkeypatch.setattr(vdt, "_db_name", lambda slug: f"ferrebot_{slug}")
    monkeypatch.setattr(
        vdt, "vaciar",
        lambda conn_url, *, confirmar: (llamadas.append({"url": conn_url, "confirmar": confirmar}), 0)[1],
    )
    return llamadas


def test_main_local_dry_run_por_defecto(monkeypatch):
    llamadas = _stub_main(monkeypatch)
    assert main(["--slug", "pim"]) == 0
    assert llamadas == [{"url": "postgresql://u:p@h:5432/ferrebot_pim", "confirmar": False}]


def test_main_confirmar_propaga_el_flag(monkeypatch):
    llamadas = _stub_main(monkeypatch)
    assert main(["--slug", "pim", "--confirmar"]) == 0
    assert llamadas[0]["confirmar"] is True


def test_main_prod_carga_env_prod_antes_de_settings(monkeypatch):
    _stub_main(monkeypatch)
    cargas: list[bool] = []
    monkeypatch.setattr("tools._prodenv.cargar_env_prod", lambda *a, **k: cargas.append(True))
    assert main(["--slug", "pim", "--prod", "--confirmar"]) == 0
    assert cargas == [True]          # --prod cargó .env.prod


def test_main_reporta_excepcion_como_exit_1(monkeypatch, capsys):
    _stub_main(monkeypatch)
    def _boom(*a, **k):
        raise RuntimeError("db caída")
    monkeypatch.setattr(vdt, "vaciar", _boom)
    assert main(["--slug", "pim", "--confirmar"]) == 1
    assert "db caída" in capsys.readouterr().err
