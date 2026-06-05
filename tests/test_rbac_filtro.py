"""E2 Parte 0 — `get_filtro_efectivo`: scoping por vendedor (auth-rbac.md).

Dependency PURA (no toca BD ni request): se prueba llamándola directo con un Principal y el
query param. Regla: un vendedor SIEMPRE ve solo lo suyo (ignora ?vendedor_id); admin/super_admin
ven todo (None) o impersonan a un vendedor (?vendedor_id=N).
"""
from __future__ import annotations

from core.auth import Principal
from core.auth.deps import get_filtro_efectivo


def test_vendedor_ignora_query_param():
    user = Principal(user_id=5, tenant="pr", rol="vendedor")
    assert get_filtro_efectivo(user=user, vendedor_id=99) == 5   # ignora la impersonación
    assert get_filtro_efectivo(user=user, vendedor_id=None) == 5


def test_admin_sin_query_ve_todo():
    user = Principal(user_id=1, tenant="pr", rol="admin")
    assert get_filtro_efectivo(user=user, vendedor_id=None) is None


def test_admin_con_query_impersona():
    user = Principal(user_id=1, tenant="pr", rol="admin")
    assert get_filtro_efectivo(user=user, vendedor_id=7) == 7


def test_super_admin_se_comporta_como_admin():
    user = Principal(user_id=1, tenant="pr", rol="super_admin")
    assert get_filtro_efectivo(user=user, vendedor_id=7) == 7
    assert get_filtro_efectivo(user=user, vendedor_id=None) is None
