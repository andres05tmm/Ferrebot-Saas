"""Cartera de alquiler (Fase 5): consumo de horas → cargo en el ledger de fiados, cupos y colitas.

Corre contra Postgres efímero (fixture `tenant`, Docker 5433). El INVARIANTE del carve-out va
TEST-PRIMERO: un mismo `RegistroHorasMaquina` NUNCA genera dos cargos en fiados (el bot/Fase 6 puede
reintentar). El resto cubre: cupo excedido avisa sin bloquear (SSE al dueño), un solo cupo activo por
cliente, el seam de maquinaria (genera el cargo con cupo; NO lo genera sin capacidad ni sin cupo),
detección de colita y aislamiento multi-tenant.
"""
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

import modules.cartera.repository as cartera_repo
from core.config.timezone import now_co
from modules.cartera.schemas import CupoCrear
from modules.cartera.service import construir_cartera_service
from modules.fiados.repository import SqlFiadosRepository
from modules.fiados.service import FiadosService
from modules.maquinaria.repository import SqlMaquinasRepository
from modules.maquinaria.schemas import MaquinaCrear, RegistroHorasCrear
from modules.maquinaria.service import MaquinariaService

_KEY = "alquiler:horas:{}"


# --- seeds (SQL directo: prerequisitos de otros módulos; el patrón de los tests del repo) ----------
async def _cliente(s: AsyncSession) -> int:
    return (
        await s.execute(text("INSERT INTO clientes (nombre, saldo_fiado) VALUES ('Alcaldía', 0) RETURNING id"))
    ).scalar_one()


async def _obra(s: AsyncSession, cid: int, *, estado: str = "EN_EJECUCION") -> int:
    return (
        await s.execute(
            text("INSERT INTO obras (cliente_id, nombre, estado) VALUES (:c, 'Vía La Paz', :e) RETURNING id"),
            {"c": cid, "e": estado},
        )
    ).scalar_one()


async def _maquina(s: AsyncSession, *, codigo: str = "M-1") -> int:
    return (
        await s.execute(
            text(
                "INSERT INTO maquinas (codigo, nombre, tipo, precio_hora_default) "
                "VALUES (:c, 'Vibrocompactador', 'compactador', 120000) RETURNING id"
            ),
            {"c": codigo},
        )
    ).scalar_one()


async def _asignacion(s: AsyncSession, mid: int, oid: int, *, precio: str = "160000", minimo: int = 4) -> int:
    return (
        await s.execute(
            text(
                "INSERT INTO asignaciones_maquina_obra "
                "(maquina_id, obra_id, fecha_inicio, precio_hora, minimo_horas, activa) "
                "VALUES (:m, :o, '2026-01-01', :p, :min, true) RETURNING id"
            ),
            {"m": mid, "o": oid, "p": precio, "min": minimo},
        )
    ).scalar_one()


async def _registro(
    s: AsyncSession, mid: int, oid: int, *, fact: str = "8", fecha: date = date(2026, 2, 2)
) -> int:
    return (
        await s.execute(
            text(
                "INSERT INTO registros_horas_maquina "
                "(maquina_id, obra_id, fecha, horas_trabajadas, horas_facturables) "
                "VALUES (:m, :o, :f, :fact, :fact) RETURNING id"
            ),
            {"m": mid, "o": oid, "f": fecha, "fact": fact},
        )
    ).scalar_one()


async def _cupo_sql(s: AsyncSession, cid: int, *, cupo: str = "10000000") -> None:
    await s.execute(
        text(
            "INSERT INTO cupos_alquiler (cliente_id, cupo, vigente_desde, activo) "
            "VALUES (:c, :cupo, CURRENT_DATE, true)"
        ),
        {"c": cid, "cupo": cupo},
    )


async def _cuenta(engine, tabla: str, where: str = "", params: dict | None = None) -> int:
    async with AsyncSession(engine) as s:
        sql = f"SELECT count(*) FROM {tabla}" + (f" WHERE {where}" if where else "")
        return (await s.execute(text(sql), params or {})).scalar_one()


async def _saldo_fiado(engine, cid: int) -> Decimal:
    async with AsyncSession(engine) as s:
        return (
            await s.execute(text("SELECT saldo_fiado FROM clientes WHERE id=:c"), {"c": cid})
        ).scalar_one()


def _espia_publish(monkeypatch) -> list[tuple]:
    """Espía `modules.cartera.repository.publish` (los eventos de fiados usan otro import, no se capturan)."""
    eventos: list[tuple] = []

    async def fake(session, event, data):
        eventos.append((event, data))

    monkeypatch.setattr(cartera_repo, "publish", fake)
    return eventos


# --- INVARIANTE (test-primero): idempotencia del cargo por registro de horas -----------------------
async def test_asentar_consumo_horas_idempotente(tenant):
    """Un mismo `RegistroHorasMaquina` NUNCA genera dos cargos: reintentar es REPLAY (doble guarda:
    UNIQUE(registro_horas_id) + el idempotency_key de fiados). El saldo sube UNA sola vez."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        cid = await _cliente(s)
        oid = await _obra(s, cid)
        mid = await _maquina(s)
        aid = await _asignacion(s, mid, oid)
        rid = await _registro(s, mid, oid)
        await _cupo_sql(s, cid)
        await s.commit()

        svc = construir_cartera_service(s)
        kwargs = dict(
            registro_horas_id=rid, obra_id=oid, maquina_id=mid, asignacion_id=aid, cliente_id=cid,
            horas_facturables=Decimal("8"), precio_hora=Decimal("160000"),
        )
        r1 = await svc.asentar_consumo_horas(**kwargs)
        await s.commit()
        r2 = await svc.asentar_consumo_horas(**kwargs)
        await s.commit()

    assert r1.replay is False and r2.replay is True
    assert r2.fiado_id == r1.fiado_id
    assert r1.monto == Decimal("1280000.00")            # 8 × 160.000, cuantizado a MONEY(12,2)
    # Un solo cargo, un solo fiado con la key, un solo movimiento cargo; saldo cargado UNA vez.
    assert await _cuenta(tenant.engine, "cargos_alquiler", "registro_horas_id=:r", {"r": rid}) == 1
    assert await _cuenta(tenant.engine, "fiados", "idempotency_key=:k", {"k": _KEY.format(rid)}) == 1
    assert await _cuenta(tenant.engine, "fiados_movimientos", "tipo='cargo'") == 1
    assert await _saldo_fiado(tenant.engine, cid) == Decimal("1280000.00")


async def test_asentar_consumo_horas_race_choca_unique(tenant):
    """Guarda DURA a nivel de base: si dos flujos esquivaran el pre-check, el 2º cargo viola el UNIQUE."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        cid = await _cliente(s)
        oid = await _obra(s, cid)
        mid = await _maquina(s)
        await _asignacion(s, mid, oid)
        rid = await _registro(s, mid, oid)
        fid = (
            await s.execute(
                text("INSERT INTO fiados (cliente_id, monto, saldo) VALUES (:c, 1, 1) RETURNING id"),
                {"c": cid},
            )
        ).scalar_one()
        await s.execute(
            text(
                "INSERT INTO cargos_alquiler (registro_horas_id, fiado_id, obra_id, maquina_id, asignacion_id, monto) "
                "VALUES (:r, :f, :o, :m, :a, 1)"
            ),
            {"r": rid, "f": fid, "o": oid, "m": mid, "a": 1},
        )
        await s.commit()
        with pytest.raises(IntegrityError):
            await s.execute(
                text(
                    "INSERT INTO cargos_alquiler (registro_horas_id, fiado_id, obra_id, maquina_id, asignacion_id, monto) "
                    "VALUES (:r, :f, :o, :m, :a, 1)"
                ),
                {"r": rid, "f": fid, "o": oid, "m": mid, "a": 1},
            )
        await s.rollback()


# --- cupo excedido: avisa sin bloquear -------------------------------------------------------------
async def test_cupo_excedido_avisa_sin_bloquear(tenant, monkeypatch):
    """Si `saldo + cargo > cupo` se AVISA al dueño (SSE `cartera_cupo_excedido`) pero NO se bloquea: el
    cargo se asienta igual."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        cid = await _cliente(s)
        oid = await _obra(s, cid)
        mid = await _maquina(s)
        aid = await _asignacion(s, mid, oid)
        rid = await _registro(s, mid, oid)
        await _cupo_sql(s, cid, cupo="100000")   # cupo (100k) < cargo (1,28M) → excedido
        await s.commit()

        eventos = _espia_publish(monkeypatch)
        r = await construir_cartera_service(s).asentar_consumo_horas(
            registro_horas_id=rid, obra_id=oid, maquina_id=mid, asignacion_id=aid, cliente_id=cid,
            horas_facturables=Decimal("8"), precio_hora=Decimal("160000"),
        )
        await s.commit()

    assert r.cupo_excedido is True and r.replay is False
    assert [e[0] for e in eventos] == ["cartera_cupo_excedido"]
    assert Decimal(eventos[0][1]["excedente"]) == Decimal("1180000")   # 1.280.000 − 100.000 (cupo MONEY4)
    # NO bloqueó: el cargo quedó asentado y el saldo subió completo.
    assert await _cuenta(tenant.engine, "cargos_alquiler", "registro_horas_id=:r", {"r": rid}) == 1
    assert await _saldo_fiado(tenant.engine, cid) == Decimal("1280000.00")


# --- un solo cupo activo por cliente ---------------------------------------------------------------
async def test_cupo_activo_unico_por_cliente(tenant):
    """Crear un cupo DESACTIVA el activo previo del cliente: siempre a lo sumo un cupo activo (histórico
    en las filas inactivas). El repo respeta el único parcial `uq_cupos_alquiler_cliente_activo`."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        cid = await _cliente(s)
        await s.commit()
        svc = construir_cartera_service(s)
        await svc.crear_cupo(CupoCrear(cliente_id=cid, cupo=Decimal("10000000"), vigente_desde=date(2026, 1, 1)))
        await s.commit()
        await svc.crear_cupo(CupoCrear(cliente_id=cid, cupo=Decimal("20000000"), vigente_desde=date(2026, 6, 1)))
        await s.commit()

    assert await _cuenta(tenant.engine, "cupos_alquiler", "cliente_id=:c", {"c": cid}) == 2
    assert await _cuenta(tenant.engine, "cupos_alquiler", "cliente_id=:c AND activo", {"c": cid}) == 1
    async with AsyncSession(tenant.engine) as s:
        activo = (
            await s.execute(
                text("SELECT cupo FROM cupos_alquiler WHERE cliente_id=:c AND activo"), {"c": cid}
            )
        ).scalar_one()
    assert activo == Decimal("20000000.0000")   # el vigente es el último


# --- seam de maquinaria: genera el cargo al registrar horas (integración) --------------------------
async def test_seam_genera_cargo_con_cupo(tenant):
    """Con `CarteraAlquilerService` inyectado y cupo activo, registrar horas asienta el cargo EN LA MISMA
    transacción (invariante «nada mueve cartera sin registro»)."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        cid = await _cliente(s)
        oid = await _obra(s, cid)
        mid = await _maquina(s)
        await _asignacion(s, mid, oid, precio="160000", minimo=4)
        await _cupo_sql(s, cid)
        await s.commit()

        maq = MaquinariaService(SqlMaquinasRepository(s), construir_cartera_service(s))
        r = await maq.registrar_horas(
            mid, RegistroHorasCrear(obra_id=oid, fecha=date(2026, 2, 3), horas_trabajadas=Decimal("8"))
        )
        await s.commit()

    assert r.horas_facturables == Decimal("8") and r.replay is False
    assert await _cuenta(tenant.engine, "cargos_alquiler", "obra_id=:o", {"o": oid}) == 1
    assert await _saldo_fiado(tenant.engine, cid) == Decimal("1280000.00")


async def test_seam_sin_capacidad_no_genera_cargo(tenant):
    """Sin cartera inyectada (tenant sin la capacidad `cartera_alquiler`) el registro de horas NO toca la
    cartera: comportamiento actual intacto (ni cargos ni fiados)."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        cid = await _cliente(s)
        oid = await _obra(s, cid)
        mid = await _maquina(s)
        await _asignacion(s, mid, oid)
        await _cupo_sql(s, cid)   # aunque hubiera cupo, sin service inyectado no se asienta nada
        await s.commit()

        maq = MaquinariaService(SqlMaquinasRepository(s))   # cartera=None
        await maq.registrar_horas(
            mid, RegistroHorasCrear(obra_id=oid, fecha=date(2026, 2, 3), horas_trabajadas=Decimal("8"))
        )
        await s.commit()

    assert await _cuenta(tenant.engine, "cargos_alquiler") == 0
    assert await _cuenta(tenant.engine, "fiados") == 0
    assert await _saldo_fiado(tenant.engine, cid) == Decimal("0.00")


async def test_seam_sin_cupo_activo_no_genera_cargo(tenant):
    """Con cartera inyectada pero SIN cupo activo del cliente, la compuerta del seam no asienta el cargo:
    el alquiler a crédito solo aplica a clientes con cupo otorgado."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        cid = await _cliente(s)
        oid = await _obra(s, cid)
        mid = await _maquina(s)
        await _asignacion(s, mid, oid)
        await s.commit()   # sin cupo

        maq = MaquinariaService(SqlMaquinasRepository(s), construir_cartera_service(s))
        await maq.registrar_horas(
            mid, RegistroHorasCrear(obra_id=oid, fecha=date(2026, 2, 3), horas_trabajadas=Decimal("8"))
        )
        await s.commit()

    assert await _cuenta(tenant.engine, "cargos_alquiler") == 0
    assert await _cuenta(tenant.engine, "fiados") == 0
    assert await _saldo_fiado(tenant.engine, cid) == Decimal("0.00")


# --- colita: obra cerrada con saldo estancado ------------------------------------------------------
async def test_detectar_colita_solo_obra_cerrada(tenant):
    """Detecta colita en una obra FINALIZADA con saldo sin abono; una obra EN_EJECUCION con saldo NO es
    colita (finalizar/liquidar es lo que la habilita, diseño §4.b)."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        cid = await _cliente(s)
        mid = await _maquina(s)
        # Obra FINALIZADA con consumo (colita candidata).
        oid_fin = await _obra(s, cid, estado="FINALIZADA")
        aid_f = await _asignacion(s, mid, oid_fin)
        rid_f = await _registro(s, mid, oid_fin, fecha=date(2026, 2, 2))
        # Obra EN_EJECUCION con consumo (NO colita).
        oid_eje = await _obra(s, cid, estado="EN_EJECUCION")
        aid_e = await _asignacion(s, mid, oid_eje)
        rid_e = await _registro(s, mid, oid_eje, fecha=date(2026, 2, 3))
        await _cupo_sql(s, cid)
        await s.commit()

        svc = construir_cartera_service(s)
        for rid, oid, aid in ((rid_f, oid_fin, aid_f), (rid_e, oid_eje, aid_e)):
            await svc.asentar_consumo_horas(
                registro_horas_id=rid, obra_id=oid, maquina_id=mid, asignacion_id=aid, cliente_id=cid,
                horas_facturables=Decimal("8"), precio_hora=Decimal("160000"),
            )
        await s.commit()

        colitas = await svc.detectar_colitas(ahora=now_co(), dias_umbral=15)

    assert len(colitas) == 1
    c = colitas[0]
    assert c.obra_id == oid_fin and c.cliente_id == cid
    assert c.saldo == Decimal("1280000.00") and c.ultimo_abono_en is None


# --- enriquecimiento de lecturas para el dashboard (nombres, horas, abonos reales) -----------------
async def test_listar_colitas_trae_nombres_reales(tenant):
    """`/colitas` (listar_colitas) enriquece con `cliente_nombre` y `obra_nombre` reales (JOIN
    clientes+obras): el dashboard cae a "Cliente #id"/"Obra #id" sin ellos."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        cid = await _cliente(s)                       # nombre 'Alcaldía'
        oid = await _obra(s, cid, estado="FINALIZADA")  # nombre 'Vía La Paz'
        mid = await _maquina(s)
        aid = await _asignacion(s, mid, oid)
        rid = await _registro(s, mid, oid)
        await _cupo_sql(s, cid)
        await s.commit()

        svc = construir_cartera_service(s)
        await svc.asentar_consumo_horas(
            registro_horas_id=rid, obra_id=oid, maquina_id=mid, asignacion_id=aid, cliente_id=cid,
            horas_facturables=Decimal("8"), precio_hora=Decimal("160000"),
        )
        await s.commit()

        colitas = await svc.listar_colitas()

    assert len(colitas) == 1
    c = colitas[0]
    assert c.cliente_id == cid and c.obra_id == oid
    assert c.cliente_nombre == "Alcaldía"          # nombre real, no "Cliente #id"
    assert c.obra_nombre == "Vía La Paz"           # nombre real, no "Obra #id"
    assert c.saldo == Decimal("1280000.00")


async def test_cartera_de_obra_trae_maquina_horas_y_abonos(tenant):
    """El detalle de obra (cartera_de_obra) trae `obra_nombre`/`cliente_nombre`, y por cada cargo el
    `maquina_nombre` y las `horas_facturables` reales (JOIN maquinas + registros_horas_maquina), más los
    `abonos` del ledger imputados a la obra. Sin esto el dashboard muestra "Máquina #id", "0 h" y
    "Sin abonos"."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        cid = await _cliente(s)                       # 'Alcaldía'
        oid = await _obra(s, cid)                      # 'Vía La Paz'
        mid = await _maquina(s)                        # nombre 'Vibrocompactador'
        aid = await _asignacion(s, mid, oid)
        rid = await _registro(s, mid, oid, fact="8")
        await _cupo_sql(s, cid)
        await s.commit()

        svc = construir_cartera_service(s)
        r = await svc.asentar_consumo_horas(
            registro_horas_id=rid, obra_id=oid, maquina_id=mid, asignacion_id=aid, cliente_id=cid,
            horas_facturables=Decimal("8"), precio_hora=Decimal("160000"),
        )
        await s.commit()

        # Un abono real sobre el fiado del cargo (queda imputado a la obra por su fiado_id).
        await FiadosService(SqlFiadosRepository(s)).abonar(fiado_id=r.fiado_id, monto=Decimal("200000"))
        await s.commit()

        vista = await svc.cartera_de_obra(oid)

    # Encabezado: nombres reales, no "#id".
    assert vista.obra_nombre == "Vía La Paz"
    assert vista.cliente_nombre == "Alcaldía"
    assert vista.cliente_id == cid
    # Saldo = cargo (1.280.000) − abono (200.000).
    assert vista.saldo == Decimal("1080000.00")
    # Cargo enriquecido: nombre de máquina y horas facturables reales.
    assert len(vista.cargos) == 1
    cargo = vista.cargos[0]
    assert cargo.maquina_nombre == "Vibrocompactador"     # no "Máquina #id"
    assert cargo.horas_facturables == Decimal("8")        # no "0 h"
    assert cargo.monto == Decimal("1280000.00")
    # Abonos reales imputados a la obra: no "Sin abonos".
    assert len(vista.abonos) == 1
    assert vista.abonos[0].monto == Decimal("200000.00")


# --- aislamiento multi-tenant ----------------------------------------------------------------------
async def test_aislamiento_cartera_no_cruza_empresas(tenant_factory):
    """La cartera (cupos + cargos + fiados) de la empresa A jamás aparece en la B (bases distintas)."""
    empresa_a = await tenant_factory()
    empresa_b = await tenant_factory()

    async with AsyncSession(empresa_a.engine, expire_on_commit=False) as s:
        cid = await _cliente(s)
        oid = await _obra(s, cid)
        mid = await _maquina(s)
        aid = await _asignacion(s, mid, oid)
        rid = await _registro(s, mid, oid)
        await _cupo_sql(s, cid)
        await s.commit()
        await construir_cartera_service(s).asentar_consumo_horas(
            registro_horas_id=rid, obra_id=oid, maquina_id=mid, asignacion_id=aid, cliente_id=cid,
            horas_facturables=Decimal("8"), precio_hora=Decimal("160000"),
        )
        await s.commit()

    assert await _cuenta(empresa_a.engine, "cargos_alquiler") == 1
    assert await _cuenta(empresa_b.engine, "cupos_alquiler") == 0
    assert await _cuenta(empresa_b.engine, "cargos_alquiler") == 0
    assert await _cuenta(empresa_b.engine, "fiados") == 0


# --- el cron quedó cableado en el runtime ARQ ------------------------------------------------------
def test_detectar_colitas_registrado_en_cron_jobs():
    import apps.worker.main as worker

    funcs = {getattr(c, "coroutine", None) for c in worker.WorkerSettings.cron_jobs}
    assert worker.detectar_colitas_alquiler in funcs
