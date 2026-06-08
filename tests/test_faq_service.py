"""Pack FAQ / conocimiento — servicio + recuperación (keyword v1) contra base efímera real.

Cubre: recuperación de entradas relevantes por palabras clave; "pocas → devuélvelas todas"; sin
entradas → sin información (señal de no inventar); las inactivas no se recuperan; y el CRUD básico.
"""
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from modules.faq.errors import ConocimientoInexistente
from modules.faq.repository import SqlConocimientoRepository
from modules.faq.schemas import ConocimientoCrear
from modules.faq.service import FaqService


def _svc(s: AsyncSession) -> FaqService:
    return FaqService(SqlConocimientoRepository(s))


async def _crear(s: AsyncSession, titulo: str, contenido: str, *, activo: bool = True, orden: int = 0):
    repo = SqlConocimientoRepository(s)
    e = await repo.crear(ConocimientoCrear(titulo=titulo, contenido=contenido, activo=activo, orden=orden))
    await s.commit()
    return e


# Seis temas (> límite de 5) para forzar el ranking por palabras clave, no el "devolver todas".
_SEIS_TEMAS = [
    ("Ubicación", "Estamos ubicados en la Carrera 1 # 2-3, local 4."),
    ("Horarios", "Atendemos de lunes a viernes de 8am a 6pm."),
    ("Formas de pago", "Aceptamos efectivo, Nequi y tarjeta."),
    ("Parqueo", "Hay parqueadero gratuito para clientes."),
    ("Políticas de cancelación", "Cancela con 24 horas de anticipación."),
    ("Servicios", "Ofrecemos limpieza dental y blanqueamiento."),
]


async def test_recupera_entrada_relevante_por_keyword(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        for titulo, contenido in _SEIS_TEMAS:
            await _crear(s, titulo, contenido)
        r = await _svc(s).responder("¿cuál es la ubicación del local?")

    assert r.hay_info
    titulos = [e.titulo for e in r.entradas]
    assert "Ubicación" in titulos          # la relevante se recupera
    assert "Parqueo" not in titulos        # las no relacionadas no


async def test_pocas_entradas_se_devuelven_todas(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await _crear(s, "Horarios", "Lunes a viernes 8-6")
        await _crear(s, "Parqueo", "Gratis para clientes")
        r = await _svc(s).responder("cualquier cosa sin relación")

    assert r.hay_info
    assert {e.titulo for e in r.entradas} == {"Horarios", "Parqueo"}  # pocas → todas


async def test_sin_entradas_no_hay_info(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        r = await _svc(s).responder("¿a qué hora abren?")
    assert not r.hay_info and r.entradas == []   # señal de "no inventar / escalar"


async def test_inactivas_no_se_recuperan(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await _crear(s, "Activa", "visible al agente", activo=True)
        await _crear(s, "Inactiva", "oculta al agente", activo=False)
        r = await _svc(s).responder("hola")
    assert [e.titulo for e in r.entradas] == ["Activa"]


# --- CRUD -------------------------------------------------------------------
async def test_crud_basico(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        svc = _svc(s)
        creada = await svc.crear(ConocimientoCrear(titulo="Horarios", contenido="8 a 6", orden=1))
        await s.commit()
        assert creada.id is not None and creada.actualizado_en is None

        actualizada = await svc.actualizar(
            creada.id, ConocimientoCrear(titulo="Horarios", contenido="8 a 7", orden=1)
        )
        await s.commit()
        assert actualizada.contenido == "8 a 7"
        assert actualizada.actualizado_en is not None   # se sella concreto (sin lazy-load al serializar)

        # incluir inactivas
        await svc.crear(ConocimientoCrear(titulo="Oculta", contenido="x", activo=False))
        await s.commit()
        assert len(await svc.listar(solo_activas=True)) == 1
        assert len(await svc.listar(solo_activas=False)) == 2

        await svc.eliminar(creada.id)
        await s.commit()
        with pytest.raises(ConocimientoInexistente):
            await svc.obtener(creada.id)
