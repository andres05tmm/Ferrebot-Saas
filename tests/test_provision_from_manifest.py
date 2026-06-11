"""Provisionado de un paso desde un manifiesto (tools.provision_from_manifest). Requiere Postgres.

E2E contra un control DB efímero + una app DB efímera (patrón de test_provision_tenant): provisiona
desde un manifiesto MÍNIMO (1 servicio, 1 recurso con 1 día/1 franja, 1 faq, 1 wa_numero con sus
features encendidas), verifica empresa+plan en control, las filas de pack en el tenant y la fila de
wa_numeros, y RE-CORRE el comando entero comprobando que ningún conteo cambia (idempotencia E2E).

VALORES FALSOS; sin red. NUNCA toca Punto Rojo (slug único por test).
"""
import uuid

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from psycopg.rows import dict_row

import core.db.session as session_mod
from core.config import get_settings
from core.db.urls import tenant_url, to_libpq
from core.tenancy.cache import control_cache
from tests.conftest import create_database, drop_database
from tools.provision_from_manifest import provision_from_manifest
from tools.provision_tenant import _db_name

_MANIFIESTO = """\
version: 1
identidad:
  slug: {slug}
  nombre: "Mini SAS"
  nit: "{nit}"
admin:
  nombre: "Admin Mini"
plan:
  nombre: "Agente"
  features: ["pack_agenda", "pack_faq", "canal_whatsapp"]
branding:
  color_primario: "#123456"
  tema: "aurora"
packs:
  agenda:
    config:
      modo_confirmacion: "manual"
      persona: "Hola, soy el asistente de Mini."
    servicios:
      - {{ nombre: "Corte", duracion_min: 30, precio: 20000 }}
    recursos:
      - nombre: "Ana"
        tipo: "profesional"
        presta: ["Corte"]
        disponibilidad:
          - {{ dias: [0], franjas: ["09:00-12:00"] }}
  faq:
    entradas:
      - {{ titulo: "Horario", contenido: "Lunes a viernes.", orden: 1 }}
canal:
  whatsapp:
    phone_number_id: "{phone}"
    numero: "+57 300 0000000"
"""


async def test_provision_from_manifest_e2e_idempotente(tmp_path, monkeypatch, capsys):
    # Control DB efímero (mismo patrón que test_provision_tenant).
    control_name = f"test_control_man_{uuid.uuid4().hex[:12]}"
    control_url = tenant_url(get_settings().tenants_direct_url_base, control_name)
    monkeypatch.setenv("CONTROL_DATABASE_URL", control_url)
    get_settings.cache_clear()
    monkeypatch.setattr(session_mod, "_control_sessionmaker", None)
    monkeypatch.setattr(session_mod, "_control_engine", None)

    slug = f"mini{uuid.uuid4().hex[:10]}"
    phone = uuid.uuid4().hex[:15]
    nit = f"NIT-{uuid.uuid4().hex[:8]}"
    control_cache.invalidate(slug)

    manifiesto = tmp_path / "mini.yaml"
    manifiesto.write_text(_MANIFIESTO.format(slug=slug, nit=nit, phone=phone), encoding="utf-8")

    create_database(control_name)
    try:
        command.upgrade(Config("migrations/control/alembic.ini"), "head")

        empresa_id = provision_from_manifest(str(manifiesto))
        # El resumen cuenta las tablas fiscales base (0 en un tenant nuevo, pero verifican el esquema).
        resumen = capsys.readouterr().out
        assert "0 facturas_electronicas" in resumen
        assert "0 webhooks_matias" in resumen
        # Idempotente: re-correr el comando ENTERO no cambia el id.
        assert provision_from_manifest(str(manifiesto)) == empresa_id

        # --- Control DB: empresa + plan + wa_numeros ---
        with psycopg.connect(to_libpq(control_url), row_factory=dict_row) as cc:
            empresa = cc.execute(
                "SELECT id, plan_id FROM empresas WHERE slug = %s", (slug,)
            ).fetchone()
            assert empresa is not None and empresa["id"] == empresa_id
            assert empresa["plan_id"] is not None
            limites = cc.execute(
                "SELECT limites FROM planes WHERE id = %s", (empresa["plan_id"],)
            ).fetchone()["limites"]
            assert set(limites["features"]) == {"pack_agenda", "pack_faq", "canal_whatsapp"}
            # branding.tema viaja del manifiesto al control DB (white-label de UI con nombre).
            brand = cc.execute(
                "SELECT color_primario, tema FROM branding WHERE empresa_id = %s", (empresa_id,)
            ).fetchone()
            assert brand["color_primario"] == "#123456" and brand["tema"] == "aurora"
            wa = cc.execute(
                "SELECT empresa_id, numero FROM wa_numeros WHERE phone_number_id = %s", (phone,)
            ).fetchall()
            assert len(wa) == 1 and wa[0]["empresa_id"] == empresa_id

        # --- Tenant DB: filas de pack ---
        tenant_db_url = tenant_url(get_settings().tenants_direct_url_base, _db_name(slug))
        with psycopg.connect(to_libpq(tenant_db_url), row_factory=dict_row) as tc:
            def n(tabla: str) -> int:
                return tc.execute(f"SELECT count(*) AS n FROM {tabla}").fetchone()["n"]

            conteos = {t: n(t) for t in ("servicios", "recursos", "disponibilidad",
                                          "agenda_config", "conocimiento")}
            assert conteos == {"servicios": 1, "recursos": 1, "disponibilidad": 1,
                               "agenda_config": 1, "conocimiento": 1}
            cfg = tc.execute("SELECT modo_confirmacion, persona FROM agenda_config WHERE id=1").fetchone()
            assert cfg["modo_confirmacion"] == "manual" and "Mini" in cfg["persona"]
            # Relación recurso→servicio resuelta por nombre.
            assert n("recurso_servicio") == 1

        # --- Idempotencia E2E: tras la segunda corrida, los conteos NO cambiaron ---
        with psycopg.connect(to_libpq(tenant_db_url), row_factory=dict_row) as tc:
            despues = {
                t: tc.execute(f"SELECT count(*) AS n FROM {t}").fetchone()["n"]
                for t in ("servicios", "recursos", "disponibilidad", "agenda_config", "conocimiento")
            }
        assert despues == conteos
        with psycopg.connect(to_libpq(control_url), row_factory=dict_row) as cc:
            n_wa = cc.execute(
                "SELECT count(*) AS n FROM wa_numeros WHERE phone_number_id = %s", (phone,)
            ).fetchone()["n"]
        assert n_wa == 1  # no duplica el mapeo
    finally:
        drop_database(_db_name(slug))
        if session_mod._control_engine is not None:
            await session_mod._control_engine.dispose()
        control_cache.invalidate(slug)
        get_settings.cache_clear()
        drop_database(control_name)


async def test_manifiesto_invalido_no_provisiona(tmp_path, monkeypatch):
    # Falla cerrado: un manifiesto que no valida aborta ANTES de tocar la BD (no crea empresa ni DB).
    control_name = f"test_control_man_{uuid.uuid4().hex[:12]}"
    control_url = tenant_url(get_settings().tenants_direct_url_base, control_name)
    monkeypatch.setenv("CONTROL_DATABASE_URL", control_url)
    get_settings.cache_clear()
    monkeypatch.setattr(session_mod, "_control_sessionmaker", None)
    monkeypatch.setattr(session_mod, "_control_engine", None)

    slug = f"bad{uuid.uuid4().hex[:10]}"
    # Datos de agenda pero SIN pack_agenda activo → incoherencia (validación falla cerrado).
    manifiesto = tmp_path / "bad.yaml"
    manifiesto.write_text(
        f'version: 1\n'
        f'identidad: {{slug: {slug}, nombre: "Bad", nit: "NIT-{slug}"}}\n'
        f'plan: {{nombre: "X", features: ["pack_faq"]}}\n'
        f'packs:\n  agenda:\n    servicios: [{{nombre: "X", duracion_min: 10}}]\n',
        encoding="utf-8",
    )

    create_database(control_name)
    try:
        command.upgrade(Config("migrations/control/alembic.ini"), "head")
        from tools.manifest import ErrorManifiesto
        with pytest.raises(ErrorManifiesto):
            provision_from_manifest(str(manifiesto))
        # No se creó la empresa.
        with psycopg.connect(to_libpq(control_url), row_factory=dict_row) as cc:
            assert cc.execute("SELECT 1 FROM empresas WHERE slug=%s", (slug,)).fetchone() is None
    finally:
        get_settings.cache_clear()
        drop_database(control_name)
