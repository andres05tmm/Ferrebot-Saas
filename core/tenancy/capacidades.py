"""Capacidades efectivas de una empresa (feature flags) — leídas del control DB.

`feature-flags.md`: features efectivas = features del plan ∪ overrides habilitados − deshabilitados.
Único lugar con el SQL de capacidades; lo consumen el gate del API (`core.auth.features`) y el bot.
Vivía en `apps/bot/repos.py`; se movió aquí para compartirlo sin que `core` importe de `apps`.
"""
from __future__ import annotations

import json

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.tenancy.catalogo import expandir_metapacks


class ControlCapacidades:
    """Features efectivas = features del plan ± overrides de `empresa_features` (feature-flags §).

    El set devuelto viene con los meta-packs EXPANDIDOS (`pos` → ventas/caja/inventario, conservando
    `pos`): todos los consumidores (gate del API, bot, worker, superadmin) ven las features finas.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def efectivas(self, empresa_id: int) -> frozenset[str]:
        plan = (
            await self._s.execute(
                text(
                    "SELECT p.limites FROM empresas e "
                    "JOIN planes p ON p.id = e.plan_id WHERE e.id = :e"
                ),
                {"e": empresa_id},
            )
        ).first()
        efectivas: set[str] = set()
        if plan is not None and plan[0] is not None:
            limites = plan[0] if isinstance(plan[0], dict) else json.loads(plan[0])
            efectivas = set(limites.get("features", []))

        overrides = (
            await self._s.execute(
                text("SELECT feature, habilitada FROM empresa_features WHERE empresa_id = :e"),
                {"e": empresa_id},
            )
        ).all()
        for feature, habilitada in overrides:
            if habilitada:
                efectivas.add(feature)
            else:
                efectivas.discard(feature)
        return expandir_metapacks(frozenset(efectivas))
