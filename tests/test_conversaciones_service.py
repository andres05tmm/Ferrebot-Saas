"""Motor + repositorio del pack de conversación / handoff contra base efímera real.

Cubre el ciclo del handoff: escalar (upsert por teléfono), `esta_en_humano` (el predicado de pausa del
runtime), resolver (devuelve al bot y sella `resuelta_en`) y el listado de escaladas.
"""
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from modules.conversaciones.errors import ConversacionInexistente
from modules.conversaciones.repository import SqlConversacionRepository
from modules.conversaciones.service import ConversacionService

TEL_A = "573001112233"
TEL_B = "573009998877"


def _svc(s: AsyncSession) -> ConversacionService:
    return ConversacionService(SqlConversacionRepository(s))


async def test_escalar_pone_en_humano_y_esta_en_humano(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        svc = _svc(s)
        assert await svc.esta_en_humano(TEL_A) is False     # sin conversación → bot
        conv = await svc.escalar(TEL_A, motivo="no resuelvo")
        await s.commit()
        assert conv.estado == "humano" and conv.escalada_en is not None
        assert await svc.esta_en_humano(TEL_A) is True


async def test_resolver_reanuda_y_sella_resuelta_en(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        svc = _svc(s)
        conv = await svc.escalar(TEL_A, motivo="x")
        await s.commit()

        vuelto = await svc.resolver(conv.id)
        await s.commit()
        assert vuelto.estado == "bot" and vuelto.resuelta_en is not None
        assert await svc.esta_en_humano(TEL_A) is False     # reanudado: el agente vuelve a atender


async def test_resolver_inexistente(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        with pytest.raises(ConversacionInexistente):
            await _svc(s).resolver(99999)


async def test_listar_escaladas_solo_humano(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        svc = _svc(s)
        a = await svc.escalar(TEL_A, motivo="a")
        await svc.escalar(TEL_B, motivo="b")
        await s.commit()
        # Resolver A: ya no aparece en la bandeja.
        await svc.resolver(a.id)
        await s.commit()

        escaladas = await svc.listar_escaladas()
        assert [c.cliente_telefono for c in escaladas] == [TEL_B]


async def test_reescalar_reusa_la_misma_fila(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        svc = _svc(s)
        c1 = await svc.escalar(TEL_A, motivo="primera")
        await s.commit()
        await svc.resolver(c1.id)
        await s.commit()
        c2 = await svc.escalar(TEL_A, motivo="segunda")
        await s.commit()
        assert c2.id == c1.id                  # misma conversación (única por teléfono)
        assert c2.estado == "humano" and c2.resuelta_en is None and c2.motivo == "segunda"
