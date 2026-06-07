"""Respaldo de producción — partes PURAS (sin Postgres, sin pg_dump).

Cubre lo testeable sin integración: el parseo de `.env.prod`, el formato del timestamp UTC, el
nombre de DB desde una URL y el plan de backup (listado de tenants → objetivos/archivos). La parte
de pg_dump/pg_restore es integración (se prueba a mano contra el Docker local; ver docs/runbook.md).
"""
from datetime import datetime, timezone

from tools._prodenv import _limpiar_valor, parsear_env
from tools.backup_db import (
    Objetivo,
    marca_tiempo,
    nombre_db_de_url,
    planear_backup,
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
