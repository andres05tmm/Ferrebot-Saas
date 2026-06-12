"""Switch del número Kapso demo entre tenants (plan §6): re-apunta `wa_numeros` y limpia `MemoriaWa`.

Cubre con fakes (sin control DB ni Redis reales) la lógica de orquestación:
- el switch re-apunta el número y borra las conversaciones en Redis (origen + destino) por patrón
  seguro, sin tocar otros tenants ni otras familias de llaves, y NUNCA `flushdb`;
- el alias de vertical (`barberia`) resuelve a `barberia-demo`, y el slug completo también;
- `--list`/`consultar` no escribe (ni control DB ni Redis);
- un slug inexistente da error claro y `run` devuelve exit != 0;
- re-switch al mismo tenant es no-op idempotente: no reescribe ni limpia.
"""
import fnmatch

import pytest

from tools.switch_demo import (
    DEFAULT_PHONE_NUMBER_ID,
    EmpresaRef,
    SwitchError,
    consultar,
    ejecutar_switch,
    resolver_destino,
    run,
)

CLINICA = EmpresaRef(id=1, slug="clinica-demo", nombre="Clínica dental Aurora")
BARBERIA = EmpresaRef(id=2, slug="barberia-demo", nombre="El Patio")
PNID = DEFAULT_PHONE_NUMBER_ID
EMPRESAS = {"clinica-demo": CLINICA, "barberia-demo": BARBERIA}
CAPS = {2: frozenset({"pack_agenda", "canal_whatsapp", "pack_faq"})}
NUCLEO = {"clientes", "reportes"}


class FakeRedis:
    """Redis sincrónico mínimo: `scan_iter` por glob + `delete`. `flushdb` marca (debe NO usarse)."""

    def __init__(self, claves=()):
        self.data = {k: "x" for k in claves}
        self.flush_llamado = False

    def scan_iter(self, match=None):
        for k in list(self.data):
            if match is None or fnmatch.fnmatch(k, match):
                yield k

    def delete(self, *keys):
        n = 0
        for k in keys:
            if k in self.data:
                del self.data[k]
                n += 1
        return n

    def flushdb(self):  # pragma: no cover - solo existe para detectar uso indebido
        self.flush_llamado = True
        self.data.clear()


class FakeRepo:
    """Control DB falso: empresas por slug + mapeo phone_number_id→empresa_id + capacidades."""

    def __init__(self, empresas, actual=None, caps=None):
        self._empresas = empresas
        self._mapeo = {}
        if actual is not None:
            self._mapeo[actual[0]] = actual[1]
        self._caps = caps or {}
        self.reapuntadas = []

    def buscar_por_slug(self, slug):
        return self._empresas.get(slug)

    def empresa_actual(self, phone_number_id):
        eid = self._mapeo.get(phone_number_id)
        if eid is None:
            return None
        return next(e for e in self._empresas.values() if e.id == eid)

    def reapuntar(self, phone_number_id, empresa_id):
        self._mapeo[phone_number_id] = empresa_id
        self.reapuntadas.append((phone_number_id, empresa_id))

    def capacidades(self, empresa_id):
        return self._caps.get(empresa_id, frozenset())


def test_switch_remapea_y_limpia_memoria_de_origen_y_destino():
    repo = FakeRepo(EMPRESAS, actual=(PNID, 1), caps=CAPS)
    redis = FakeRedis(claves=[
        "wa:conv:1:+573001112233",   # conversación del tenant viejo (clinica): limpiar
        "wa:conv:2:+573009998877",   # conversación previa del destino (barberia): limpiar
        "wa:conv:99:+573000000000",  # de OTRO tenant: intacto
        "wa:dedup:abc",              # otra familia de llaves: intacto
    ])

    res = ejecutar_switch(repo, redis, phone_number_id=PNID, ident="barberia")

    assert res.cambiado is True
    assert res.destino == BARBERIA
    assert res.anterior == CLINICA
    assert repo.reapuntadas == [(PNID, 2)]
    # Limpió SOLO origen + destino.
    assert "wa:conv:1:+573001112233" not in redis.data
    assert "wa:conv:2:+573009998877" not in redis.data
    assert "wa:conv:99:+573000000000" in redis.data
    assert "wa:dedup:abc" in redis.data
    assert res.conversaciones_limpiadas == 2
    assert redis.flush_llamado is False
    # Packs efectivos incluyen el núcleo siempre-activo.
    assert ({"pack_agenda", "canal_whatsapp", "pack_faq"} | NUCLEO) <= res.capacidades


def test_switch_desde_numero_sin_mapear_solo_limpia_destino():
    repo = FakeRepo(EMPRESAS, actual=None, caps=CAPS)
    redis = FakeRedis(claves=["wa:conv:2:+573009998877"])

    res = ejecutar_switch(repo, redis, phone_number_id=PNID, ident="barberia-demo")

    assert res.cambiado is True
    assert res.anterior is None
    assert repo.reapuntadas == [(PNID, 2)]
    assert "wa:conv:2:+573009998877" not in redis.data
    assert res.conversaciones_limpiadas == 1


def test_resolver_destino_acepta_alias_vertical_y_slug_completo():
    repo = FakeRepo(EMPRESAS)
    assert resolver_destino(repo, "barberia") == BARBERIA       # alias → barberia-demo
    assert resolver_destino(repo, "barberia-demo") == BARBERIA  # slug completo
    assert resolver_destino(repo, "clinica-demo") == CLINICA


def test_slug_inexistente_da_error_claro_sin_tocar_nada():
    repo = FakeRepo(EMPRESAS)
    redis = FakeRedis(claves=["wa:conv:1:+57300"])
    with pytest.raises(SwitchError) as exc:
        ejecutar_switch(repo, redis, phone_number_id=PNID, ident="zapateria")
    assert "zapateria" in str(exc.value)
    assert repo.reapuntadas == []
    assert "wa:conv:1:+57300" in redis.data  # no limpió nada


def test_switch_al_mismo_tenant_es_no_op_idempotente():
    repo = FakeRepo(EMPRESAS, actual=(PNID, 2), caps=CAPS)
    redis = FakeRedis(claves=["wa:conv:2:+57300"])

    res = ejecutar_switch(repo, redis, phone_number_id=PNID, ident="barberia-demo")

    assert res.cambiado is False
    assert res.destino == BARBERIA
    assert repo.reapuntadas == []               # no reescribió
    assert "wa:conv:2:+57300" in redis.data     # no limpió
    assert res.conversaciones_limpiadas == 0
    assert (CAPS[2] | NUCLEO) <= res.capacidades


def test_consultar_no_escribe_y_aplica_nucleo():
    repo = FakeRepo(EMPRESAS, actual=(PNID, 1), caps={1: frozenset({"pack_agenda"})})
    actual, caps = consultar(repo, phone_number_id=PNID)
    assert actual == CLINICA
    assert {"pack_agenda"} | NUCLEO <= caps
    assert repo.reapuntadas == []


def test_run_list_exit0_y_no_toca_redis(capsys):
    repo = FakeRepo(EMPRESAS, actual=(PNID, 1), caps={1: frozenset({"pack_agenda"})})
    redis = FakeRedis(claves=["wa:conv:1:+57300"])

    code = run(repo, redis, ["--list"])

    assert code == 0
    assert repo.reapuntadas == []
    assert "wa:conv:1:+57300" in redis.data
    out = capsys.readouterr().out
    assert "clinica-demo" in out


def test_run_slug_inexistente_exit_no_cero(capsys):
    repo = FakeRepo(EMPRESAS)
    redis = FakeRedis()
    code = run(repo, redis, ["zapateria"])
    assert code != 0
    assert repo.reapuntadas == []
    assert "zapateria" in capsys.readouterr().err


def test_run_switch_imprime_tenant_negocio_y_packs(capsys):
    repo = FakeRepo(EMPRESAS, actual=(PNID, 1), caps=CAPS)
    redis = FakeRedis(claves=["wa:conv:1:+57300"])

    code = run(repo, redis, ["barberia"])

    assert code == 0
    out = capsys.readouterr().out
    assert "barberia-demo" in out      # tenant activo
    assert "El Patio" in out           # nombre del negocio
    assert "pack_agenda" in out        # packs encendidos
