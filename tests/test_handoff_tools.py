"""Herramienta transversal `escalar_humano` (`ai/handoff_tools.py`) contra base efímera real.

Verifica que escalar marca la conversación como `humano` y guarda el motivo, que devuelve un mensaje
para el cliente, y —lo crítico— el GUARDARRAÍL de seguridad: el teléfono sale del Contexto del canal;
el modelo no puede escalar la conversación de otro número ni operar sin teléfono.
"""
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ai.envelope import Contexto, ErrorTool, Resultado
from ai.handoff_tools import HandoffDeps, ejecutar
from core.llm.base import ToolCall
from modules.conversaciones.repository import SqlConversacionRepository
from modules.conversaciones.service import ConversacionService

TEL_A = "573001112233"
TEL_B = "573009998877"


def _deps(s: AsyncSession) -> HandoffDeps:
    return HandoffDeps(conversaciones=ConversacionService(SqlConversacionRepository(s)))


def _ctx(telefono: str | None = TEL_A) -> Contexto:
    return Contexto(
        tenant_id=1, usuario_id=0, rol="cliente", origen="whatsapp",
        capacidades=frozenset(), cliente_telefono=telefono,
    )


def _call(herramienta: str, **arguments) -> ToolCall:
    return ToolCall(id="t", name=herramienta, arguments=arguments)


async def test_escalar_marca_humano_y_guarda_motivo(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        r = await ejecutar(_call("escalar_humano", motivo="Pide hablar con un asesor"), _ctx(TEL_A), _deps(s))
        await s.commit()
        assert isinstance(r, Resultado)
        assert r.data["estado"] == "humano"
        assert r.evento == "conversacion_escalada"
        assert "asesor" in r.resumen.lower()  # mensaje para el cliente

        fila = (await s.execute(
            text("SELECT cliente_telefono, estado, motivo, resuelta_en FROM conversaciones")
        )).one()
        assert fila.cliente_telefono == TEL_A
        assert fila.estado == "humano"
        assert fila.motivo == "Pide hablar con un asesor"
        assert fila.resuelta_en is None


async def test_escalar_usa_telefono_del_contexto_no_de_los_args(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        # El modelo intenta colar otro teléfono en los args: debe ignorarse (no está en el schema).
        r = await ejecutar(
            _call("escalar_humano", motivo="queja", cliente_telefono=TEL_B), _ctx(TEL_A), _deps(s)
        )
        await s.commit()
        assert isinstance(r, Resultado)
        # En la base la conversación escalada es la del CONTEXTO (TEL_A), no la de los args (TEL_B).
        tel = (await s.execute(text("SELECT cliente_telefono FROM conversaciones"))).scalar_one()
        assert tel == TEL_A


async def test_escalar_es_idempotente_por_telefono(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await ejecutar(_call("escalar_humano", motivo="primera"), _ctx(TEL_A), _deps(s))
        await ejecutar(_call("escalar_humano", motivo="segunda"), _ctx(TEL_A), _deps(s))
        await s.commit()
        filas = (await s.execute(text("SELECT motivo FROM conversaciones WHERE cliente_telefono=:t"),
                                 {"t": TEL_A})).all()
        assert len(filas) == 1                 # una sola conversación por cliente (upsert)
        assert filas[0].motivo == "segunda"    # re-escala con el motivo más reciente


async def test_falla_cerrada_sin_telefono_en_contexto(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        err = await ejecutar(_call("escalar_humano", motivo="x"), _ctx(telefono=None), _deps(s))
        assert isinstance(err, ErrorTool) and err.error == "contexto_invalido"


async def test_escalar_validacion_de_args(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        err = await ejecutar(_call("escalar_humano"), _ctx(), _deps(s))  # falta motivo
        assert isinstance(err, ErrorTool) and err.error == "validacion" and err.recuperable


async def test_herramienta_desconocida(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        err = await ejecutar(_call("no_existe"), _ctx(), _deps(s))
        assert isinstance(err, ErrorTool) and err.error == "error_interno"
