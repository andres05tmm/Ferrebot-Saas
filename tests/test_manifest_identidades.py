"""Identidades extra del manifiesto (ADR 0009 / login real). PURO: sin red ni BD.

Cubre: schema (rol default vendedor, email requerido y validado) y validación semántica (email único
entre identidades y distinto del admin).
"""
from __future__ import annotations

import pytest

from tools.manifest import ErrorManifiesto, Manifiesto, validar


def _base(identidades, *, admin_email="admin@x.co") -> dict:
    return {
        "version": 1,
        "identidad": {"slug": "demo-x", "nombre": "X", "nit": "900-1"},
        "admin": {"nombre": "Admin", "email": admin_email},
        "identidades": identidades,
        "plan": {"nombre": "Núcleo", "features": []},
    }


def test_identidad_demo_rol_default_vendedor():
    m = Manifiesto.model_validate(_base([{"email": "demo+x@melquiadez.com"}]))
    assert len(m.identidades) == 1
    assert m.identidades[0].rol == "vendedor"          # default no-admin
    assert m.identidades[0].nombre == "Demo"
    validar(m)  # no lanza


def test_identidad_email_invalido_falla_en_esquema():
    with pytest.raises(Exception, match="identidad.email no parece un email válido"):
        Manifiesto.model_validate(_base([{"email": "no-es-email"}]))


def test_identidad_rol_invalido_falla_en_esquema():
    with pytest.raises(Exception):  # Literal["admin","vendedor"] → ValidationError
        Manifiesto.model_validate(_base([{"email": "a@b.co", "rol": "super_admin"}]))


def test_identidad_duplicada_falla():
    datos = _base([{"email": "demo@x.co"}, {"email": "DEMO@x.co"}])  # mismo email (case-insensitive)
    with pytest.raises(ErrorManifiesto, match="identidad duplicada"):
        validar(Manifiesto.model_validate(datos))


def test_identidad_que_repite_admin_falla():
    datos = _base([{"email": "admin@x.co"}], admin_email="Admin@X.co")
    with pytest.raises(ErrorManifiesto, match="repite el email del admin"):
        validar(Manifiesto.model_validate(datos))
