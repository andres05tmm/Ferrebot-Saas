"""Herramienta transversal `responder_faq` (`ai/faq_tools.py`) contra base efímera real.

Verifica el gating por flag `pack_faq`, que la herramienta recupera y entrega las entradas relevantes al
agente, y que sin información devuelve una SEÑAL CLARA de no inventar (ofrecer humano / decir que no se
tiene el dato).
"""
from sqlalchemy.ext.asyncio import AsyncSession

from ai.envelope import Contexto, Resultado
from ai.faq_tools import FaqDeps, ejecutar, exponer_catalogo
from core.llm.base import ToolCall
from modules.faq.repository import SqlConocimientoRepository
from modules.faq.schemas import ConocimientoCrear
from modules.faq.service import FaqService


def _ctx(capacidades=frozenset({"pack_faq"})) -> Contexto:
    return Contexto(
        tenant_id=1, usuario_id=0, rol="cliente", origen="whatsapp",
        capacidades=capacidades, cliente_telefono="3001112233",
    )


def _deps(s: AsyncSession) -> FaqDeps:
    return FaqDeps(faq=FaqService(SqlConocimientoRepository(s)))


def _call(pregunta: str) -> ToolCall:
    return ToolCall(id="t", name="responder_faq", arguments={"pregunta": pregunta})


async def _crear(s: AsyncSession, titulo: str, contenido: str) -> None:
    await SqlConocimientoRepository(s).crear(ConocimientoCrear(titulo=titulo, contenido=contenido))
    await s.commit()


def test_gating_por_flag_pack_faq():
    assert exponer_catalogo(_ctx(frozenset())) == []                 # sin el flag, no se expone
    specs = exponer_catalogo(_ctx())
    assert [s.name for s in specs] == ["responder_faq"]              # con el flag, sí


async def test_responder_faq_entrega_entradas_relevantes(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await _crear(s, "Ubicación", "Estamos en la Carrera 1 # 2-3.")
        r = await ejecutar(_call("¿dónde quedan? su ubicación"), _ctx(), _deps(s))

    assert isinstance(r, Resultado)
    assert r.data["entradas"] and r.data["entradas"][0]["titulo"] == "Ubicación"


async def test_responder_faq_sin_info_senala_no_inventar(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        r = await ejecutar(_call("¿tienen estacionamiento para motos?"), _ctx(), _deps(s))

    assert isinstance(r, Resultado)
    assert r.data["entradas"] == []
    resumen = r.resumen.lower()
    assert "no inventes" in resumen and ("asesor" in resumen or "no tienes" in resumen)
