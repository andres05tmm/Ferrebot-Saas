"""Respaldo de producción — partes PURAS (sin Postgres, sin pg_dump).

Cubre lo testeable sin integración: el parseo de `.env.prod`, el formato del timestamp UTC, el
nombre de DB desde una URL y el plan de backup (listado de tenants → objetivos/archivos). La parte
de pg_dump/pg_restore es integración (se prueba a mano contra el Docker local; ver docs/runbook.md).
"""
from datetime import datetime, timezone
from types import SimpleNamespace

import tools.backup_db as backup_db
from tools._prodenv import _limpiar_valor, parsear_env
from tools.backup_db import (
    Objetivo,
    main,
    marca_tiempo,
    nombre_db_de_url,
    planear_backup,
    podar_backups,
    tamano_humano,
)


# --------------------------- parseo de .env.prod --------------------------

def test_parsear_env_basico():
    texto = (
        "CONTROL_DATABASE_URL=postgresql://u:p@host:5432/railway\n"
        "TENANTS_DIRECT_URL_BASE=postgresql://u:p@host:5432\n"
    )
    datos = parsear_env(texto)
    assert datos["CONTROL_DATABASE_URL"] == "postgresql://u:p@host:5432/railway"
    assert datos["TENANTS_DIRECT_URL_BASE"] == "postgresql://u:p@host:5432"


def test_parsear_env_ignora_comentarios_y_vacias():
    texto = "# comentario\n\n  \nADMIN_DATABASE_URL=postgresql://u:p@h/x   # inline\n"
    datos = parsear_env(texto)
    assert list(datos) == ["ADMIN_DATABASE_URL"]
    assert datos["ADMIN_DATABASE_URL"] == "postgresql://u:p@h/x"   # se quitó el comentario inline


def test_limpiar_valor_respeta_almohadilla_pegada_y_comillas():
    # Un '#' pegado (sin espacio) es parte del valor (p. ej. un password); ' #' sí es comentario.
    assert _limpiar_valor("pa#ss") == "pa#ss"
    assert _limpiar_valor("valor  # comentario") == "valor"
    assert _limpiar_valor('"valor # con almohadilla"') == "valor # con almohadilla"
    assert _limpiar_valor("'otro'") == "otro"


# --------------------------- timestamp UTC --------------------------------

def test_marca_tiempo_formato_utc_sin_dos_puntos():
    ts = marca_tiempo(datetime(2026, 6, 7, 12, 30, 5, tzinfo=timezone.utc))
    assert ts == "20260607T123005Z"
    assert ":" not in ts                  # apto para nombre de carpeta en Windows


def test_marca_tiempo_convierte_a_utc():
    # Una hora con offset se normaliza a UTC antes de formatear.
    from datetime import timedelta
    bogota = timezone(timedelta(hours=-5))
    ts = marca_tiempo(datetime(2026, 6, 7, 7, 30, 0, tzinfo=bogota))   # 12:30 UTC
    assert ts == "20260607T123000Z"


# --------------------------- nombre de DB desde URL -----------------------

def test_nombre_db_de_url():
    assert nombre_db_de_url("postgresql://u:p@host:5432/railway") == "railway"
    assert nombre_db_de_url("postgresql+psycopg://u:p@host:5432/ferrebot_pr") == "ferrebot_pr"
    assert nombre_db_de_url("postgresql://u:p@host:5432/railway?sslmode=require") == "railway"


# --------------------------- plan de backup -------------------------------

def test_planear_backup_control_primero_y_un_objetivo_por_tenant():
    objetivos = planear_backup(
        "postgresql://u:p@host:5432/railway",
        "postgresql://u:p@host:5432",
        ["ferrebot_puntorojo", "ferrebot_ferreteria2"],
    )
    assert [o.db_name for o in objetivos] == ["railway", "ferrebot_puntorojo", "ferrebot_ferreteria2"]
    assert [o.archivo for o in objetivos] == [
        "railway.dump", "ferrebot_puntorojo.dump", "ferrebot_ferreteria2.dump",
    ]
    # cada tenant se compone como {base}/{db_name}
    assert objetivos[1].url == "postgresql://u:p@host:5432/ferrebot_puntorojo"
    assert objetivos[0].url == "postgresql://u:p@host:5432/railway"   # control = su propia URL
    assert all(isinstance(o, Objetivo) for o in objetivos)


def test_planear_backup_sin_tenants_solo_control():
    objetivos = planear_backup("postgresql://u:p@host:5432/railway", "postgresql://u:p@host:5432", [])
    assert len(objetivos) == 1 and objetivos[0].db_name == "railway"


# --------------------------- tamaño legible -------------------------------

def test_tamano_humano():
    assert tamano_humano(512) == "512 B"
    assert tamano_humano(2048) == "2.0 KB"
    assert tamano_humano(5 * 1024 * 1024) == "5.0 MB"


# --------------------------- gate BACKUP_ENABLED --------------------------

def _stub_flujo(monkeypatch, tmp_path, *, backup_enabled: bool) -> list[dict]:
    """Aísla `main` de prod: cargar_env_prod no-op, settings fijos y `backup_all` espía.

    Devuelve la lista de llamadas a backup_all (vacía = no se invocó a pg_dump)."""
    llamadas: list[dict] = []
    monkeypatch.setattr(backup_db, "cargar_env_prod", lambda *a, **k: None)
    monkeypatch.setattr(backup_db, "get_settings", lambda: SimpleNamespace(backup_enabled=backup_enabled))
    monkeypatch.setattr(backup_db, "backup_all", lambda **k: (llamadas.append(k), tmp_path)[1])
    return llamadas


def test_backup_deshabilitado_retorna_0_sin_pg_dump(monkeypatch, tmp_path):
    llamadas = _stub_flujo(monkeypatch, tmp_path, backup_enabled=False)
    code = main(["--dir", str(tmp_path)])
    assert code == 0              # éxito: no alarma al scheduler
    assert llamadas == []         # gate cortó ANTES de backup_all/pg_dump


def test_backup_force_procede_aunque_deshabilitado(monkeypatch, tmp_path):
    llamadas = _stub_flujo(monkeypatch, tmp_path, backup_enabled=False)
    code = main(["--dir", str(tmp_path), "--force"])
    assert code == 0
    assert len(llamadas) == 1     # --force ignora el gate y respalda


def test_backup_habilitado_procede(monkeypatch, tmp_path):
    llamadas = _stub_flujo(monkeypatch, tmp_path, backup_enabled=True)
    code = main(["--dir", str(tmp_path)])
    assert code == 0
    assert len(llamadas) == 1


# --------------------------- retención (podar) ----------------------------

def test_podar_backups_solo_las_que_exceden_keep_semanas(tmp_path):
    ahora = datetime(2026, 6, 7, 12, 0, 0, tzinfo=timezone.utc)
    # keep=8 → límite = 2026-04-12 12:00 UTC; lo anterior se poda.
    casos = {
        "20260531T120000Z": False,   # ~1 semana → se conserva
        "20260405T120000Z": True,    # ~9 semanas → se poda
        "20260118T120000Z": True,    # ~20 semanas → se poda
    }
    for nombre in casos:
        (tmp_path / nombre).mkdir()
    (tmp_path / "logs").mkdir()                          # no parsea → ignorado
    (tmp_path / "basura").mkdir()                        # no parsea → ignorado
    (tmp_path / "20260118T120000Z.dump").write_text("x")  # archivo, no carpeta → ignorado

    viejas = podar_backups(tmp_path, keep_semanas=8, ahora=ahora)

    assert {p.name for p in viejas} == {n for n, poda in casos.items() if poda}


def test_podar_backups_dir_inexistente_devuelve_vacio(tmp_path):
    assert podar_backups(tmp_path / "no-existe", keep_semanas=8) == []
