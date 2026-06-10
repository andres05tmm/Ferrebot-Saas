"""Flag `--check` de provision_from_manifest (ADR 0011 F1b): valida SIN tocar la base.

PURO (sin BD ni red): el camino `--check` solo carga + valida el manifiesto. Verifica VALIDO/INVALIDO,
los exit codes y —clave— que NUNCA entra al camino de provisioning (no toca ninguna base).
"""
from __future__ import annotations

from pathlib import Path

import tools.provision_from_manifest as pm

_EJEMPLO = Path(__file__).parents[1] / "tools" / "onboarding" / "ferreteria-demo.manifest.example.yaml"

_INVALIDO = """\
version: 1
identidad: {slug: malo, nombre: "Malo", nit: "NIT-X"}
plan: {nombre: "Retail", features: ["pos"]}
packs:
  pos:
    productos:
      - { nombre: "Cosa", unidad_medida: "unidad", precio_venta: 0 }
"""


def test_check_valido_exit_0(capsys, monkeypatch):
    # --check NUNCA debe provisionar: si tocara ese camino, este stub lo delataría.
    def _boom(*a, **k):
        raise AssertionError("--check no debe llamar a provision_from_manifest")
    monkeypatch.setattr(pm, "provision_from_manifest", _boom)

    rc = pm.main(["--from", str(_EJEMPLO), "--check"])
    assert rc == 0
    assert "VALIDO" in capsys.readouterr().out


def test_check_invalido_exit_1(capsys, tmp_path, monkeypatch):
    monkeypatch.setattr(pm, "provision_from_manifest", lambda *a, **k: 1 / 0)  # no debe llamarse
    manifiesto = tmp_path / "malo.yaml"
    manifiesto.write_text(_INVALIDO, encoding="utf-8")

    rc = pm.main(["--from", str(manifiesto), "--check"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "INVALIDO" in err
    assert "precio_venta debe ser > 0" in err  # el ErrorManifiesto agrupado se ve en la salida


def test_check_manifest_no_devuelve_nada_si_valida():
    # check_manifest es el seam reusable: no lanza con un manifiesto válido.
    assert pm.check_manifest(str(_EJEMPLO)) is None
