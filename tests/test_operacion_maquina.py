"""Operación de máquina EN VIVO (cronómetro + rotación de operadores) — feature PIM (migración 0055).

Corre contra Postgres efímero (fixture `tenant`, Docker 5433). Los INVARIANTES del carve-out van
TEST-PRIMERO:
  - IDEMPOTENCIA de finalizar: finalizar dos veces NO crea un segundo parte ni duplica el cargo a cartera
    (ancla `sesion.registro_horas_id`).
  - «Nada mueve cartera sin registro»: finalizar pasa por `registrar_horas`; con cupo asienta el cargo una
    sola vez.
  - Una sesión ABIERTA por máquina (índice único parcial) y un tramo abierto por sesión.
  - AISLAMIENTO multi-tenant: una sesión en la empresa A jamás aparece en la B.

La sesión en vivo es captura efímera; al finalizar se MATERIALIZA en el parte diario existente. El reloj
propone las horas por tramo; los tests las fijan por `ajustes` para ser determinísticos (el elapsed real
entre iniciar y finalizar en un test es ~0).
"""
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from modules.cartera.service import construir_cartera_service
from modules.maquinaria.errors import (
    OperadorInexistente,
    SesionInexistente,
    SesionNoAbierta,
    SesionYaAbierta,
    SinAsignacionActiva,
)
from modules.maquinaria.operacion_repository import SqlOperacionRepository
from modules.maquinaria.operacion_service import construir_operacion_service
from services.calculations.maquinas import horas_transcurridas


# --------------------------- helper puro: cronómetro ---------------------------

def test_horas_transcurridas():
    base = datetime(2026, 7, 11, 8, 0, 0, tzinfo=timezone.utc)
    assert horas_transcurridas(base, base.replace(hour=12)) == Decimal("4.0000")       # 4 h exactas
    assert horas_transcurridas(base, base.replace(hour=9, minute=30)) == Decimal("1.5000")  # 90 min
    assert horas_transcurridas(base, base) == Decimal("0")                             # mismo instante
    assert horas_transcurridas(base, base.replace(hour=7)) == Decimal("0")             # reloj hacia atrás → 0


# --------------------------- seeds (SQL directo, patrón del repo) ---------------------------

async def _seed(
    s: AsyncSession, *, precio: str = "160000", minimo: int = 5, con_cupo: bool = False
) -> tuple[int, int, int]:
    """Máquina + cliente + obra + asignación activa (desde 2026-01-01, sin fin → cubre hoy). Devuelve
    (maquina_id, obra_id, cliente_id). Con `con_cupo` agrega un cupo de alquiler activo para el seam."""
    cliente_id = (
        await s.execute(text("INSERT INTO clientes (nombre, saldo_fiado) VALUES ('Alcaldía', 0) RETURNING id"))
    ).scalar_one()
    obra_id = (
        await s.execute(
            text("INSERT INTO obras (cliente_id, nombre) VALUES (:c, 'Vía La Paz') RETURNING id"),
            {"c": cliente_id},
        )
    ).scalar_one()
    maquina_id = (
        await s.execute(
            text(
                "INSERT INTO maquinas (codigo, nombre, tipo, precio_hora_default) "
                "VALUES ('M-1', 'Vibrocompactador', 'compactador', 150000) RETURNING id"
            )
        )
    ).scalar_one()
    await s.execute(
        text(
            "INSERT INTO asignaciones_maquina_obra "
            "(maquina_id, obra_id, fecha_inicio, precio_hora, minimo_horas, activa) "
            "VALUES (:m, :o, '2026-01-01', :p, :min, true)"
        ),
        {"m": maquina_id, "o": obra_id, "p": precio, "min": minimo},
    )
    if con_cupo:
        await s.execute(
            text(
                "INSERT INTO cupos_alquiler (cliente_id, cupo, vigente_desde, activo) "
                "VALUES (:c, '10000000', CURRENT_DATE, true)"
            ),
            {"c": cliente_id},
        )
    await s.flush()
    return maquina_id, obra_id, cliente_id


async def _operador(s: AsyncSession, *, doc: str, nombre: str) -> int:
    return (
        await s.execute(
            text(
                "INSERT INTO trabajadores (tipo_vinculacion, documento, nombres, apellidos, cargo, activo) "
                "VALUES ('DIRECTO', :d, :n, 'Op', 'Operador', true) RETURNING id"
            ),
            {"d": doc, "n": nombre},
        )
    ).scalar_one()


async def _cuenta(engine, tabla: str, where: str = "", params: dict | None = None) -> int:
    async with AsyncSession(engine) as s:
        sql = f"SELECT count(*) FROM {tabla}" + (f" WHERE {where}" if where else "")
        return (await s.execute(text(sql), params or {})).scalar_one()


async def _saldo_fiado(engine, cid: int) -> Decimal:
    async with AsyncSession(engine) as s:
        return (
            await s.execute(text("SELECT saldo_fiado FROM clientes WHERE id=:c"), {"c": cid})
        ).scalar_one()


# --------------------------- iniciar ---------------------------

async def test_iniciar_abre_sesion_y_primer_tramo(tenant):
    """Activar abre una sesión ABIERTA con su primer tramo del operador; la obra se infiere de la única
    asignación vigente si no se pasa."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        maquina_id, obra_id, _ = await _seed(s)
        op_a = await _operador(s, doc="111", nombre="Juan")
        await s.commit()

        sesion = await construir_operacion_service(s).iniciar(maquina_id, operador_id=op_a)
        await s.commit()

        assert sesion.estado == "ABIERTA"
        assert sesion.obra_id == obra_id            # inferida de la asignación vigente
        op = SqlOperacionRepository(s)
        assert (await op.sesion_abierta_de_maquina(maquina_id)).id == sesion.id
        tramo = await op.tramo_abierto(sesion.id)
        assert tramo is not None and tramo.operador_id == op_a and tramo.finalizado_en is None


async def test_no_dos_sesiones_abiertas_por_maquina(tenant):
    """INVARIANTE: una máquina no puede correr dos sesiones a la vez → 409 SesionYaAbierta."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        maquina_id, obra_id, _ = await _seed(s)
        await s.commit()
        svc = construir_operacion_service(s)
        await svc.iniciar(maquina_id, obra_id=obra_id)
        await s.commit()
        with pytest.raises(SesionYaAbierta):
            await svc.iniciar(maquina_id, obra_id=obra_id)


async def test_iniciar_sin_asignacion_activa_409(tenant):
    """Sin asignación que ponga la máquina en una obra hoy, no hay precio/mínimo → SinAsignacionActiva."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        maquina_id = (
            await s.execute(
                text(
                    "INSERT INTO maquinas (codigo, nombre, tipo, precio_hora_default) "
                    "VALUES ('M-9','Sin asignar','t',1) RETURNING id"
                )
            )
        ).scalar_one()
        await s.commit()
        with pytest.raises(SinAsignacionActiva):
            await construir_operacion_service(s).iniciar(maquina_id)


async def test_iniciar_operador_invalido_404(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        maquina_id, obra_id, _ = await _seed(s)
        await s.commit()
        with pytest.raises(OperadorInexistente):
            await construir_operacion_service(s).iniciar(maquina_id, obra_id=obra_id, operador_id=999999)


# --------------------------- rotar ---------------------------

async def test_rotar_cierra_el_anterior_y_abre_uno(tenant):
    """Rotar cierra el tramo corriente y abre otro: siempre un solo tramo abierto por sesión."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        maquina_id, obra_id, _ = await _seed(s)
        op_a = await _operador(s, doc="111", nombre="Juan")
        op_b = await _operador(s, doc="222", nombre="Pedro")
        await s.commit()
        svc = construir_operacion_service(s)
        sesion = await svc.iniciar(maquina_id, obra_id=obra_id, operador_id=op_a)
        await s.commit()
        await svc.rotar(sesion.id, op_b)
        await s.commit()

        op = SqlOperacionRepository(s)
        tramos = await op.tramos_de_sesion(sesion.id)
        assert len(tramos) == 2
        assert tramos[0].operador_id == op_a and tramos[0].finalizado_en is not None   # cerrado
        assert tramos[1].operador_id == op_b and tramos[1].finalizado_en is None        # corriendo
        abierto = await op.tramo_abierto(sesion.id)
        assert abierto is not None and abierto.id == tramos[1].id


# --------------------------- finalizar (materialización) ---------------------------

async def _ids_tramos(s: AsyncSession, sesion_id: int) -> list[int]:
    return [t.id for t in await SqlOperacionRepository(s).tramos_de_sesion(sesion_id)]


async def test_finalizar_materializa_parte_con_dos_turnos(tenant):
    """Finalizar escribe el parte del día: horas = Σ tramos (mínimo aplicado UNA vez), dos turnos de
    rotación, ingreso = facturables × precio pactado."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        maquina_id, obra_id, _ = await _seed(s, precio="160000", minimo=5)
        op_a = await _operador(s, doc="111", nombre="Juan")
        op_b = await _operador(s, doc="222", nombre="Pedro")
        await s.commit()
        svc = construir_operacion_service(s)
        sesion = await svc.iniciar(maquina_id, obra_id=obra_id, operador_id=op_a)
        await s.commit()
        await svc.rotar(sesion.id, op_b)
        await s.commit()

        t_a, t_b = await _ids_tramos(s, sesion.id)
        r = await svc.finalizar(sesion.id, ajustes={t_a: Decimal("4"), t_b: Decimal("5")})
        await s.commit()

    assert r.replay is False
    assert r.horas_trabajadas == Decimal("9") and r.horas_facturables == Decimal("9")
    assert r.ingreso == Decimal("9") * Decimal("160000")
    assert len(r.turnos) == 2
    assert await _cuenta(tenant.engine, "registros_horas_maquina", "maquina_id=:m", {"m": maquina_id}) == 1
    assert await _cuenta(tenant.engine, "turnos_horas_maquina") == 2
    async with AsyncSession(tenant.engine) as s:
        estado, rid = (
            await s.execute(
                text("SELECT estado, registro_horas_id FROM sesiones_maquina WHERE id=:i"),
                {"i": sesion.id},
            )
        ).one()
    assert estado == "FINALIZADA" and rid == r.registro_id


async def test_finalizar_sin_ajustes_aplica_minimo(tenant):
    """Sin ajustes el reloj propone las horas medidas (~0 en un test); el día cae bajo el mínimo, así que
    se factura el mínimo pactado (piso de movilización)."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        maquina_id, obra_id, _ = await _seed(s, precio="160000", minimo=5)
        await s.commit()
        svc = construir_operacion_service(s)
        sesion = await svc.iniciar(maquina_id, obra_id=obra_id)
        await s.commit()
        r = await svc.finalizar(sesion.id)
        await s.commit()

    assert r.horas_facturables == Decimal("5") and r.minimo_cubierto is False


async def test_finalizar_idempotente_no_duplica(tenant):
    """INVARIANTE (carve-out): finalizar dos veces NO crea un segundo parte — replay del mismo registro."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        maquina_id, obra_id, _ = await _seed(s)
        await s.commit()
        svc = construir_operacion_service(s)
        sesion = await svc.iniciar(maquina_id, obra_id=obra_id)
        await s.commit()
        t = (await _ids_tramos(s, sesion.id))[0]
        primero = await svc.finalizar(sesion.id, ajustes={t: Decimal("6")})
        await s.commit()
        segundo = await svc.finalizar(sesion.id, ajustes={t: Decimal("6")})
        await s.commit()

    assert primero.replay is False and segundo.replay is True
    assert segundo.registro_id == primero.registro_id
    assert segundo.horas_facturables == primero.horas_facturables
    assert await _cuenta(tenant.engine, "registros_horas_maquina", "maquina_id=:m", {"m": maquina_id}) == 1


async def test_finalizar_seam_cartera_asienta_una_vez(tenant):
    """INVARIANTE «nada mueve cartera sin registro»: con cartera inyectada y cupo, finalizar asienta el
    cargo EN LA MISMA transacción una sola vez; re-finalizar (replay) no lo duplica."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        maquina_id, obra_id, cid = await _seed(s, precio="160000", minimo=4, con_cupo=True)
        await s.commit()
        svc = construir_operacion_service(s, construir_cartera_service(s))
        sesion = await svc.iniciar(maquina_id, obra_id=obra_id)
        await s.commit()
        t = (await _ids_tramos(s, sesion.id))[0]
        await svc.finalizar(sesion.id, ajustes={t: Decimal("8")})
        await s.commit()
        await svc.finalizar(sesion.id, ajustes={t: Decimal("8")})   # replay
        await s.commit()

    assert await _cuenta(tenant.engine, "cargos_alquiler", "obra_id=:o", {"o": obra_id}) == 1
    assert await _saldo_fiado(tenant.engine, cid) == Decimal("1280000.00")   # 8 × 160.000, una sola vez


async def test_anular_no_materializa_ni_factura(tenant):
    """Anular descarta la captura: sin parte, sin cargo."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        maquina_id, obra_id, _ = await _seed(s, con_cupo=True)
        await s.commit()
        svc = construir_operacion_service(s, construir_cartera_service(s))
        sesion = await svc.iniciar(maquina_id, obra_id=obra_id)
        await s.commit()
        anulada = await svc.anular(sesion.id)
        await s.commit()

    assert anulada.estado == "ANULADA"
    assert await _cuenta(tenant.engine, "registros_horas_maquina") == 0
    assert await _cuenta(tenant.engine, "cargos_alquiler") == 0


async def test_detalle_trae_tramos_con_horas_propuestas(tenant):
    """El detalle (modal de revisión) trae los tramos con operador resuelto y horas propuestas por el reloj."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        maquina_id, obra_id, _ = await _seed(s)
        op_a = await _operador(s, doc="111", nombre="Juan")
        await s.commit()
        svc = construir_operacion_service(s)
        sesion = await svc.iniciar(maquina_id, obra_id=obra_id, operador_id=op_a)
        await s.commit()
        await svc.rotar(sesion.id, None)   # segundo tramo sin operador
        await s.commit()
        detalle = await svc.detalle(sesion.id)

    assert detalle["sesion"].id == sesion.id
    tramos = detalle["tramos"]
    assert len(tramos) == 2
    assert tramos[0]["operador"] == "Juan Op"          # nombres 'Juan' + apellidos 'Op'
    assert tramos[1]["operador"] is None               # tramo sin operador
    assert all("horas_propuestas" in t for t in tramos)


# --------------------------- errores de sesión ---------------------------

async def test_finalizar_sesion_inexistente_404(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        with pytest.raises(SesionInexistente):
            await construir_operacion_service(s).finalizar(999999)


async def test_rotar_sobre_sesion_finalizada_409(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        maquina_id, obra_id, _ = await _seed(s)
        await s.commit()
        svc = construir_operacion_service(s)
        sesion = await svc.iniciar(maquina_id, obra_id=obra_id)
        await s.commit()
        t = (await _ids_tramos(s, sesion.id))[0]
        await svc.finalizar(sesion.id, ajustes={t: Decimal("3")})
        await s.commit()
        with pytest.raises(SesionNoAbierta):
            await svc.rotar(sesion.id, None)


# --------------------------- aislamiento multi-tenant ---------------------------

async def test_aislamiento_sesion_no_cruza_empresas(tenant_factory):
    """INVARIANTE multi-tenant: una sesión (y sus tramos) de la empresa A jamás aparece en la B."""
    empresa_a = await tenant_factory()
    empresa_b = await tenant_factory()

    async with AsyncSession(empresa_a.engine, expire_on_commit=False) as sa:
        maquina_id, obra_id, _ = await _seed(sa)
        await sa.commit()
        await construir_operacion_service(sa).iniciar(maquina_id, obra_id=obra_id)
        await sa.commit()

    assert await _cuenta(empresa_a.engine, "sesiones_maquina") == 1
    assert await _cuenta(empresa_b.engine, "sesiones_maquina") == 0
    assert await _cuenta(empresa_b.engine, "tramos_operador") == 0
