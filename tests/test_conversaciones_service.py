"""Motor + repositorio del pack de conversación / handoff contra base efímera real.

Cubre el ciclo del handoff: escalar (upsert por teléfono), `esta_en_humano` (el predicado de pausa del
runtime), resolver (devuelve al bot y sella `resuelta_en`) y el listado de escaladas. Además el INBOX
(Fase 2): persistir el hilo, listarlo, takeover (`tomar`), responder como asesor y el aislamiento
multi-tenant del hilo (A no ve el de B).
"""
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from modules.conversaciones.errors import (
    ConversacionInexistente,
    ConversacionNoEnHumano,
)
from modules.conversaciones.repository import SqlConversacionRepository
from modules.conversaciones.service import ConversacionService

TEL_A = "573001112233"
TEL_B = "573009998877"


def _svc(s: AsyncSession) -> ConversacionService:
    return ConversacionService(SqlConversacionRepository(s))


class _EnviadorFake:
    """Enviador saliente de prueba: registra (tenant_id, to, texto) sin tocar red."""

    def __init__(self) -> None:
        self.envios: list[tuple[int, str, str]] = []

    async def enviar(self, tenant_id: int, to: str, texto: str) -> None:
        self.envios.append((tenant_id, to, texto))


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


# ── Inbox: hilo de mensajes (0024) ───────────────────────────────────────────
async def test_agregar_y_listar_mensajes_en_orden(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        repo = SqlConversacionRepository(s)
        await repo.asegurar(TEL_A)
        await repo.agregar_mensaje(TEL_A, "entrante", "cliente", "Hola, ¿tienen cupo?")
        await repo.agregar_mensaje(TEL_A, "saliente", "bot", "¡Claro! ¿Para cuándo?")
        await repo.agregar_mensaje(TEL_A, "saliente", "asesor", "Te llamo en 5 min.")
        await s.commit()

        conv = await repo.por_telefono(TEL_A)
        hilo = await _svc(s).listar_mensajes(conv.id)
        assert [(m.direccion, m.autor, m.texto) for m in hilo] == [
            ("entrante", "cliente", "Hola, ¿tienen cupo?"),
            ("saliente", "bot", "¡Claro! ¿Para cuándo?"),
            ("saliente", "asesor", "Te llamo en 5 min."),
        ]


async def test_listar_mensajes_de_conversacion_inexistente(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        with pytest.raises(ConversacionInexistente):
            await _svc(s).listar_mensajes(99999)


async def test_inbox_lista_todas_con_ultimo_mensaje_y_estado(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        repo = SqlConversacionRepository(s)
        # A: la atiende el bot (sin escalar); B: escalada a humano.
        await repo.asegurar(TEL_A)
        await repo.agregar_mensaje(TEL_A, "entrante", "cliente", "primer hola")
        await repo.agregar_mensaje(TEL_A, "saliente", "bot", "respuesta del bot")
        await repo.escalar(TEL_B, "queja")
        await repo.agregar_mensaje(TEL_B, "entrante", "cliente", "quiero un humano")
        await s.commit()

        filas = await _svc(s).listar_inbox()
        por_tel = {f.conversacion.cliente_telefono: f for f in filas}
        assert set(por_tel) == {TEL_A, TEL_B}
        assert por_tel[TEL_A].conversacion.estado == "bot"
        assert por_tel[TEL_A].ultimo.texto == "respuesta del bot"   # último mensaje del cliente A
        assert por_tel[TEL_B].conversacion.estado == "humano"
        assert por_tel[TEL_B].ultimo.texto == "quiero un humano"


# ── Takeover manual (tomar) ──────────────────────────────────────────────────
async def test_tomar_pasa_a_humano_sin_escalada_previa(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        svc = _svc(s)
        conv = await SqlConversacionRepository(s).asegurar(TEL_A)   # nace en bot
        await s.commit()
        assert await svc.esta_en_humano(TEL_A) is False

        tomada = await svc.tomar(conv.id)
        await s.commit()
        assert tomada.estado == "humano" and tomada.escalada_en is not None
        assert await svc.esta_en_humano(TEL_A) is True              # el bot queda pausado


async def test_tomar_inexistente(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        with pytest.raises(ConversacionInexistente):
            await _svc(s).tomar(99999)


# ── Responder como asesor ────────────────────────────────────────────────────
async def test_responder_envia_persiste_y_exige_humano(tenant):
    enviador = _EnviadorFake()
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        repo = SqlConversacionRepository(s)
        svc = ConversacionService(repo, enviador=enviador)
        conv = await repo.asegurar(TEL_A)
        await s.commit()

        # En estado bot NO se puede responder (toma primero).
        with pytest.raises(ConversacionNoEnHumano):
            await svc.responder(conv.id, "hola", tenant_id=7)

        await svc.tomar(conv.id)
        await s.commit()
        msg = await svc.responder(conv.id, "Te atiendo yo, dame un momento.", tenant_id=7)
        await s.commit()

        assert enviador.envios == [(7, TEL_A, "Te atiendo yo, dame un momento.")]
        assert msg.direccion == "saliente" and msg.autor == "asesor"
        hilo = await svc.listar_mensajes(conv.id)
        assert [(m.autor, m.texto) for m in hilo] == [("asesor", "Te atiendo yo, dame un momento.")]


# ── Aislamiento multi-tenant del hilo (A no ve el de B) ──────────────────────
async def test_hilo_aislado_entre_tenants(tenant_factory):
    a = await tenant_factory()
    b = await tenant_factory()
    async with AsyncSession(a.engine, expire_on_commit=False) as sa:
        ra = SqlConversacionRepository(sa)
        await ra.asegurar(TEL_A)
        await ra.agregar_mensaje(TEL_A, "entrante", "cliente", "secreto de A")
        await sa.commit()

    # La empresa B NO ve el hilo ni la conversación de A (bases separadas = frontera del tenant).
    async with AsyncSession(b.engine, expire_on_commit=False) as sb:
        rb = SqlConversacionRepository(sb)
        assert await rb.por_telefono(TEL_A) is None
        assert await rb.listar_mensajes(TEL_A) == []
        assert await rb.listar_inbox() == []
