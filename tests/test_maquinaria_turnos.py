"""Rotación de operadores dentro de un parte de horas de máquina (migración 0054).

Semántica de negocio (owner PIM): una máquina rota operadores el mismo día (Juan 8:00-13:00, Pedro
14:00-17:00). El PARTE por máquina·obra·día es el agregado; cada franja es un turno. Horas del día = Σ
turnos; el mínimo facturable se aplica UNA vez al total del día (la rotación NUNCA multiplica el cobro).

INVARIANTES DEL CARVE-OUT (test-primero):
  (a) replay de turno idéntico no duplica turno NI cargo;
  (b) turno de operador distinto suma al parte SIN duplicar el mínimo (delta de facturables correcto);
  (c) cargo delta idempotente por turno (retry no re-asienta);
  (e) aislamiento multi-tenant de las consultas nuevas (turnos);
Corre contra Postgres efímero (fixture `tenant`/`tenant_factory`, Docker 5433).
"""
from datetime import date, time
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from modules.cartera.service import construir_cartera_service
from modules.maquinaria.repository import SqlMaquinasRepository
from modules.maquinaria.schemas import MaquinaCrear, RegistroHorasCrear
from modules.maquinaria.service import MaquinariaService

_FECHA = date(2026, 7, 7)   # el 7 de julio: Juan de 8-13, Pedro de 14-17 (caso del cliente)


def _service(session: AsyncSession, cartera=None) -> MaquinariaService:
    return MaquinariaService(SqlMaquinasRepository(session), cartera)


async def _seed(
    session: AsyncSession, *, precio_hora: str = "160000", minimo_horas: int = 5, codigo: str = "M-001"
) -> tuple[int, int]:
    """Máquina (por service) + cliente/obra/asignación activa (por SQL). Devuelve (maquina_id, obra_id)."""
    maquina = await _service(session).crear(
        MaquinaCrear(
            codigo=codigo, nombre="Retroexcavadora", tipo="retro",
            precio_hora_default=Decimal("150000"), minimo_horas_factura=1,
        )
    )
    cliente_id = (
        await session.execute(text("INSERT INTO clientes (nombre, saldo_fiado) VALUES ('Cli', 0) RETURNING id"))
    ).scalar_one()
    obra_id = (
        await session.execute(
            text("INSERT INTO obras (cliente_id, nombre) VALUES (:c, 'Obra 1') RETURNING id"),
            {"c": cliente_id},
        )
    ).scalar_one()
    await session.execute(
        text(
            "INSERT INTO asignaciones_maquina_obra "
            "(maquina_id, obra_id, fecha_inicio, precio_hora, minimo_horas, activa) "
            "VALUES (:m, :o, '2026-01-01', :p, :min, true)"
        ),
        {"m": maquina.id, "o": obra_id, "p": precio_hora, "min": minimo_horas},
    )
    await session.flush()
    return maquina.id, obra_id


async def _cupo(session: AsyncSession, cliente_id: int, *, cupo: str = "100000000") -> None:
    await session.execute(
        text(
            "INSERT INTO cupos_alquiler (cliente_id, cupo, vigente_desde, activo) "
            "VALUES (:c, :cupo, CURRENT_DATE, true)"
        ),
        {"c": cliente_id, "cupo": cupo},
    )


async def _trabajador(session: AsyncSession, nombres: str, apellidos: str, documento: str) -> int:
    return (
        await session.execute(
            text(
                "INSERT INTO trabajadores (tipo_vinculacion, documento, nombres, apellidos, cargo, activo) "
                "VALUES ('DIRECTO', :doc, :n, :a, 'Operador', true) RETURNING id"
            ),
            {"doc": documento, "n": nombres, "a": apellidos},
        )
    ).scalar_one()


async def _cliente_de_obra(session: AsyncSession, obra_id: int) -> int:
    return (
        await session.execute(text("SELECT cliente_id FROM obras WHERE id=:o"), {"o": obra_id})
    ).scalar_one()


async def _contar(session: AsyncSession, tabla: str, where: str, params: dict) -> int:
    return (
        await session.execute(text(f"SELECT count(*) FROM {tabla} WHERE {where}"), params)
    ).scalar_one()


# --- (a) replay de turno idéntico: no duplica turno ni cargo ---------------------------------------
async def test_replay_turno_identico_no_duplica(tenant):
    """INVARIANTE: re-registrar el MISMO turno (operador, franja, horas) → replay: no crea 2º turno ni 2º
    cargo. Es lo que deja al bot reintentar sin duplicar."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        maquina_id, obra_id = await _seed(s, minimo_horas=5)
        cliente_id = await _cliente_de_obra(s, obra_id)
        await _cupo(s, cliente_id)
        await s.commit()

        svc = _service(s, construir_cartera_service(s))
        payload = RegistroHorasCrear(
            obra_id=obra_id, fecha=_FECHA, horas_trabajadas=Decimal("3"),
            operador_id=None, hora_inicio=time(8, 0), hora_fin=time(13, 0),
        )
        r1 = await svc.registrar_horas(maquina_id, payload)
        await s.commit()
        r2 = await svc.registrar_horas(maquina_id, payload)
        await s.commit()

    assert r1.replay is False and r2.replay is True
    assert r1.registro_id == r2.registro_id
    assert len(r2.turnos) == 1                                     # un solo turno
    async with AsyncSession(tenant.engine) as s2:
        assert await _contar(s2, "turnos_horas_maquina", "registro_horas_id=:r", {"r": r1.registro_id}) == 1
        # Un solo cargo (el del registro, turno_id NULL): la rotación idéntica no re-asienta.
        assert await _contar(s2, "cargos_alquiler", "obra_id=:o", {"o": obra_id}) == 1


# --- (b) turno de operador distinto suma sin duplicar el mínimo; delta de facturables correcto -----
async def test_operador_distinto_suma_sin_duplicar_minimo(tenant):
    """INVARIANTE: mín 5. Juan 3h → facturables 5 (delta cargo = 5h). +Pedro 1h → total 4, facturables
    SIGUEN 5 (delta 0, sin cargo nuevo). +2h más → total 6, facturables 6 (delta 1h → cargo de 1h)."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        maquina_id, obra_id = await _seed(s, minimo_horas=5, precio_hora="160000")
        cliente_id = await _cliente_de_obra(s, obra_id)
        await _cupo(s, cliente_id)
        # Dos operadores para la rotación.
        juan = await _trabajador(s, "Juan", "Pérez", "1001")
        pedro = await _trabajador(s, "Pedro", "Gómez", "1002")
        await s.commit()

        svc = _service(s, construir_cartera_service(s))
        # Juan 3h (bajo el mínimo 5).
        r1 = await svc.registrar_horas(maquina_id, RegistroHorasCrear(
            obra_id=obra_id, fecha=_FECHA, horas_trabajadas=Decimal("3"),
            operador_id=juan, hora_inicio=time(8, 0), hora_fin=time(11, 0),
        ))
        await s.commit()
        # Pedro 1h → total 4, aún bajo el mínimo: facturables se quedan en 5, delta 0.
        r2 = await svc.registrar_horas(maquina_id, RegistroHorasCrear(
            obra_id=obra_id, fecha=_FECHA, horas_trabajadas=Decimal("1"),
            operador_id=pedro, hora_inicio=time(14, 0), hora_fin=time(15, 0),
        ))
        await s.commit()
        # Pedro +2h más → total 6, supera el mínimo: facturables 6, delta 1h.
        r3 = await svc.registrar_horas(maquina_id, RegistroHorasCrear(
            obra_id=obra_id, fecha=_FECHA, horas_trabajadas=Decimal("2"),
            operador_id=pedro, hora_inicio=time(15, 0), hora_fin=time(17, 0),
        ))
        await s.commit()

    assert r1.horas_trabajadas == Decimal("3") and r1.horas_facturables == Decimal("5")
    assert r2.horas_trabajadas == Decimal("4") and r2.horas_facturables == Decimal("5")   # mínimo NO se duplica
    assert r3.horas_trabajadas == Decimal("6") and r3.horas_facturables == Decimal("6")
    assert r1.registro_id == r2.registro_id == r3.registro_id     # mismo parte del día
    assert len(r3.turnos) == 3                                     # tres franjas de rotación
    assert r3.ingreso == Decimal("6") * Decimal("160000")        # ingreso del DÍA (no por turno)

    async with AsyncSession(tenant.engine) as s2:
        # >1 operador distinto → la cabecera del parte queda NULL (el front cae a los turnos).
        op_cabecera = (await s2.execute(
            text("SELECT operador_id FROM registros_horas_maquina WHERE id=:r"), {"r": r3.registro_id}
        )).scalar_one()
        assert op_cabecera is None
        # Cargos: el del registro (5h) + el delta del 3er turno (1h). El 2º turno (delta 0) NO asentó cargo.
        assert await _contar(s2, "cargos_alquiler", "obra_id=:o", {"o": obra_id}) == 2
        saldo = (await s2.execute(text("SELECT saldo_fiado FROM clientes WHERE id=:c"), {"c": cliente_id})).scalar_one()
    # Total cartera = 6h × 160.000 = facturables del día × precio (la rotación no multiplicó el cobro).
    assert saldo == Decimal("960000.00")


# --- (c) cargo delta idempotente por turno (a nivel de cartera) ------------------------------------
async def test_cargo_delta_idempotente_por_turno(tenant):
    """INVARIANTE: `asentar_delta_turno` es idempotente por `turno_id` (retry → replay, no re-asienta el
    cargo ni re-sube el saldo). Doble guarda: pre-check + UNIQUE parcial WHERE turno_id."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        maquina_id, obra_id = await _seed(s, minimo_horas=4, precio_hora="160000")
        cliente_id = await _cliente_de_obra(s, obra_id)
        await _cupo(s, cliente_id)
        aid = (await s.execute(text("SELECT id FROM asignaciones_maquina_obra WHERE obra_id=:o"), {"o": obra_id})).scalar_one()
        rid = (await s.execute(
            text("INSERT INTO registros_horas_maquina (maquina_id, obra_id, fecha, horas_trabajadas, horas_facturables) "
                 "VALUES (:m,:o,:f,4,4) RETURNING id"),
            {"m": maquina_id, "o": obra_id, "f": _FECHA},
        )).scalar_one()
        tid = (await s.execute(
            text("INSERT INTO turnos_horas_maquina (registro_horas_id, horas) VALUES (:r, 2) RETURNING id"),
            {"r": rid},
        )).scalar_one()
        await s.commit()

        svc = construir_cartera_service(s)
        kwargs = dict(
            registro_horas_id=rid, turno_id=tid, obra_id=obra_id, maquina_id=maquina_id,
            asignacion_id=aid, cliente_id=cliente_id, delta_horas=Decimal("2"), precio_hora=Decimal("160000"),
        )
        d1 = await svc.asentar_delta_turno(**kwargs)
        await s.commit()
        d2 = await svc.asentar_delta_turno(**kwargs)
        await s.commit()

    assert d1.replay is False and d2.replay is True
    assert d1.fiado_id == d2.fiado_id
    assert d1.monto == Decimal("320000.00")                       # 2h × 160.000
    async with AsyncSession(tenant.engine) as s2:
        assert await _contar(s2, "cargos_alquiler", "turno_id=:t", {"t": tid}) == 1
        saldo = (await s2.execute(text("SELECT saldo_fiado FROM clientes WHERE id=:c"), {"c": cliente_id})).scalar_one()
    assert saldo == Decimal("320000.00")                          # subió UNA sola vez


async def test_delta_cero_no_asienta_cargo(tenant):
    """Si el día sigue bajo el mínimo (delta 0), el turno se registra pero NO asienta cargo."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        maquina_id, obra_id = await _seed(s, minimo_horas=5, precio_hora="160000")
        cliente_id = await _cliente_de_obra(s, obra_id)
        await _cupo(s, cliente_id)
        await s.commit()

        svc = _service(s, construir_cartera_service(s))
        await svc.registrar_horas(maquina_id, RegistroHorasCrear(
            obra_id=obra_id, fecha=_FECHA, horas_trabajadas=Decimal("2"),
            operador_id=None, hora_inicio=time(8, 0), hora_fin=time(10, 0),
        ))
        await s.commit()
        r2 = await svc.registrar_horas(maquina_id, RegistroHorasCrear(
            obra_id=obra_id, fecha=_FECHA, horas_trabajadas=Decimal("1"),
            operador_id=None, hora_inicio=time(14, 0), hora_fin=time(15, 0),
        ))
        await s.commit()

    assert r2.horas_trabajadas == Decimal("3") and r2.horas_facturables == Decimal("5")
    async with AsyncSession(tenant.engine) as s2:
        # Solo el cargo del registro (5h × precio); el 2º turno (delta 0) no asentó nada.
        assert await _contar(s2, "cargos_alquiler", "obra_id=:o", {"o": obra_id}) == 1


# --- parte legacy sin turnos: al entrar la rotación no pierde sus horas -----------------------------
async def test_parte_legacy_adopta_horas_al_rotar(tenant):
    """Un parte creado sin franja (legacy) que luego recibe un turno: sus horas se materializan como turno
    implícito para que Σ no las pierda (no hay backfill masivo; es perezoso al entrar la rotación)."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        maquina_id, obra_id = await _seed(s, minimo_horas=5, precio_hora="160000")
        cliente_id = await _cliente_de_obra(s, obra_id)
        await _cupo(s, cliente_id)
        await s.commit()

        svc = _service(s, construir_cartera_service(s))
        # Parte legacy: sin operador ni franja → sin turnos.
        r1 = await svc.registrar_horas(maquina_id, RegistroHorasCrear(
            obra_id=obra_id, fecha=_FECHA, horas_trabajadas=Decimal("6"),
        ))
        await s.commit()
        assert r1.turnos == []
        # Entra una franja distinta (2h más): adopta las 6h legacy + suma 2 → total 8, facturables 8.
        r2 = await svc.registrar_horas(maquina_id, RegistroHorasCrear(
            obra_id=obra_id, fecha=_FECHA, horas_trabajadas=Decimal("2"),
            hora_inicio=time(15, 0), hora_fin=time(17, 0),
        ))
        await s.commit()

    assert r2.horas_trabajadas == Decimal("8") and r2.horas_facturables == Decimal("8")
    assert len(r2.turnos) == 2                                     # el implícito (6h) + el nuevo (2h)


# --- (e) aislamiento multi-tenant de las consultas nuevas de turnos --------------------------------
async def test_aislamiento_turnos_no_cruza_empresas(tenant_factory):
    """La empresa B nunca ve los turnos de la A (la base ES la frontera; sin `empresa_id`)."""
    empresa_a = await tenant_factory()
    empresa_b = await tenant_factory()

    async with AsyncSession(empresa_a.engine, expire_on_commit=False) as sa:
        maquina_id, obra_id = await _seed(sa, minimo_horas=5)
        await sa.commit()
        await _service(sa).registrar_horas(maquina_id, RegistroHorasCrear(
            obra_id=obra_id, fecha=_FECHA, horas_trabajadas=Decimal("3"),
            hora_inicio=time(8, 0), hora_fin=time(11, 0),
        ))
        await sa.commit()

    async with AsyncSession(empresa_b.engine) as sb:
        total_b = (await sb.execute(text("SELECT count(*) FROM turnos_horas_maquina"))).scalar_one()
    assert total_b == 0
