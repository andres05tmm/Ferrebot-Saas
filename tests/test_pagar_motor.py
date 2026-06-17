"""Motor de pagar (ADR 0019) — el job determinista sobre la base efímera real.

Cubre la clasificación pura (vencimiento efectivo, por vencer / vencida, derivación cuando
`fecha_vencimiento` es NULL) y los guardarraíles del MOTOR (no dependen del LLM): ventana horaria,
config inactiva, cadencia por factura, y que un envío fallido NO sella el dedup. Además los
invariantes del repo: AISLAMIENTO multi-tenant (la empresa A nunca ve cuentas de B) e IDEMPOTENCIA
(una corrida repetida no reenvía ni cuenta doble). El envío real se inyecta como callback y se falsea.
"""
from datetime import datetime, time, timedelta
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.config.timezone import COLOMBIA_TZ, today_co
from modules.pagar.repository import FacturaPendiente, SqlPagarRepository
from modules.pagar.service import AvisoPagar, PagarService, clasificar_cuenta


def _ahora(hora: int = 10) -> datetime:
    """Un instante de hoy a la hora dada (Colombia): controla la ventana horaria del motor."""
    return datetime.combine(today_co(), time(hora, 0), tzinfo=COLOMBIA_TZ)


def _fake_enviar(registro: list[str], *, ok: bool = True, capturar: list[AvisoPagar] | None = None):
    """Callback de envío falso: registra los factura_id avisados y reporta éxito (`ok`)."""
    async def enviar(aviso: AvisoPagar) -> bool:
        registro.extend(c.factura_id for c in aviso.cuentas)
        if capturar is not None:
            capturar.append(aviso)
        return ok
    return enviar


async def _seed_factura(
    s: AsyncSession, *, factura_id: str = "F-001", proveedor: str = "Tornillos SA",
    total: str = "100000", pendiente: str = "100000", estado: str = "pendiente",
    fecha=None, fecha_vencimiento=None,
) -> str:
    fecha = fecha or today_co()
    await s.execute(
        text(
            "INSERT INTO facturas_proveedores "
            "(id, proveedor, total, pagado, pendiente, estado, fecha, fecha_vencimiento) "
            "VALUES (:id, :p, :t, 0, :pend, :e, :f, :fv)"
        ),
        {"id": factura_id, "p": proveedor, "t": total, "pend": pendiente,
         "e": estado, "f": fecha, "fv": fecha_vencimiento},
    )
    await s.commit()
    return factura_id


async def _config(s: AsyncSession, **valores):
    """Config get-or-create con overrides directos (el motor la lee tal cual)."""
    repo = SqlPagarRepository(s)
    config = await repo.obtener_config()
    for campo, valor in valores.items():
        setattr(config, campo, valor)
    await s.commit()
    return config


# --- clasificación pura (sin BD) ----------------------------------------------
def _factura(fecha, fecha_vencimiento=None, *, pendiente="100000") -> FacturaPendiente:
    return FacturaPendiente(
        factura_id="F", proveedor="Prov", pendiente=Decimal(pendiente),
        fecha=fecha, fecha_vencimiento=fecha_vencimiento,
        avisos_enviados=0, ultimo_aviso_en=None,
    )


def test_clasifica_por_vencer_vencida_y_fuera_de_ventana():
    hoy = today_co()
    kw = {"hoy": hoy, "dias_aviso_previo": 3, "plazo_default_dias": 30}

    por_vencer = clasificar_cuenta(_factura(hoy, hoy + timedelta(days=2)), **kw)
    assert por_vencer.por_vencer and not por_vencer.vencida and por_vencer.dias_para_vencer == 2

    vencida = clasificar_cuenta(_factura(hoy, hoy - timedelta(days=1)), **kw)
    assert vencida.vencida and not vencida.por_vencer and vencida.dias_para_vencer == -1

    lejana = clasificar_cuenta(_factura(hoy, hoy + timedelta(days=10)), **kw)
    assert not lejana.por_vencer and not lejana.vencida


def test_clasifica_deriva_vencimiento_cuando_es_null():
    hoy = today_co()
    # fecha_vencimiento NULL → vencimiento = fecha + plazo_default_dias.
    cuenta = clasificar_cuenta(
        _factura(hoy - timedelta(days=30), None),
        hoy=hoy, dias_aviso_previo=3, plazo_default_dias=30,
    )
    assert cuenta.vencimiento_efectivo == hoy
    assert cuenta.dias_para_vencer == 0 and cuenta.por_vencer and not cuenta.vencida


# --- corrida del motor: envío, dedup e idempotencia ---------------------------
async def test_envia_resumen_y_es_idempotente_en_la_cadencia(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        factura = await _seed_factura(s, fecha_vencimiento=today_co() + timedelta(days=2))
        await _config(s, dias_aviso_previo=3, cadencia_dias=3)
        svc = PagarService(SqlPagarRepository(s))

        registro1: list[str] = []
        capturado: list[AvisoPagar] = []
        r1 = await svc.procesar_avisos(ahora=_ahora(), enviar=_fake_enviar(registro1, capturar=capturado))
        await s.commit()

        registro2: list[str] = []
        r2 = await svc.procesar_avisos(ahora=_ahora(11), enviar=_fake_enviar(registro2))
        await s.commit()

        estado = await SqlPagarRepository(s).estado_factura(factura)

    assert registro1 == [factura] and r1.avisos_enviados == 1 and r1.facturas_notificadas == 1
    assert capturado[0].total_por_vencer == Decimal("100000") and capturado[0].total_vencido == 0
    # Idempotencia: misma corrida dentro de la cadencia NO reenvía ni cuenta doble.
    assert registro2 == [] and r2.avisos_enviados == 0
    assert estado.avisos_enviados == 1


async def test_envio_fallido_no_sella_dedup(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        factura = await _seed_factura(s, fecha_vencimiento=today_co())
        await _config(s)
        svc = PagarService(SqlPagarRepository(s))
        r = await svc.procesar_avisos(ahora=_ahora(), enviar=_fake_enviar([], ok=False))
        await s.commit()
        estado = await SqlPagarRepository(s).estado_factura(factura)

    assert r.avisos_enviados == 0
    assert estado.avisos_enviados == 0 and estado.ultimo_aviso_en is None


async def test_cadencia_reenvia_despues_del_intervalo(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        factura = await _seed_factura(s, fecha_vencimiento=today_co() + timedelta(days=2))
        await _config(s, dias_aviso_previo=3, cadencia_dias=3)
        svc = PagarService(SqlPagarRepository(s))

        primera: list[str] = []
        await svc.procesar_avisos(ahora=_ahora(), enviar=_fake_enviar(primera))
        await s.commit()
        segunda: list[str] = []
        r = await svc.procesar_avisos(ahora=_ahora() + timedelta(days=4), enviar=_fake_enviar(segunda))
        await s.commit()
        estado = await SqlPagarRepository(s).estado_factura(factura)

    assert primera == [factura] and segunda == [factura]   # 4 días > cadencia: reanuda
    assert r.avisos_enviados == 1 and estado.avisos_enviados == 2


# --- guardarraíles ------------------------------------------------------------
async def test_ventana_horaria_no_envia_fuera_de_horario(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await _seed_factura(s, fecha_vencimiento=today_co())
        await _config(s, hora_inicio=time(8), hora_fin=time(18))
        svc = PagarService(SqlPagarRepository(s))
        registro: list[str] = []
        r = await svc.procesar_avisos(ahora=_ahora(22), enviar=_fake_enviar(registro))
        await s.commit()

    assert registro == [] and r.avisos_enviados == 0


async def test_config_inactiva_no_envia(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await _seed_factura(s, fecha_vencimiento=today_co())
        await _config(s, activo=False)
        registro: list[str] = []
        r = await PagarService(SqlPagarRepository(s)).procesar_avisos(
            ahora=_ahora(), enviar=_fake_enviar(registro)
        )
        await s.commit()

    assert registro == [] and r.avisos_enviados == 0


async def test_factura_lejana_no_amerita_aviso_pero_vencida_si(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await _seed_factura(s, factura_id="LEJANA", fecha_vencimiento=today_co() + timedelta(days=20))
        vencida = await _seed_factura(
            s, factura_id="VENCIDA", fecha_vencimiento=today_co() - timedelta(days=2)
        )
        await _config(s, dias_aviso_previo=3)
        registro: list[str] = []
        capturado: list[AvisoPagar] = []
        await PagarService(SqlPagarRepository(s)).procesar_avisos(
            ahora=_ahora(), enviar=_fake_enviar(registro, capturar=capturado)
        )
        await s.commit()

    assert registro == [vencida]                       # la lejana queda fuera de la ventana
    assert capturado[0].total_vencido == Decimal("100000") and capturado[0].total_por_vencer == 0


async def test_factura_pagada_no_aparece(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await _seed_factura(
            s, pendiente="0", estado="pagada", fecha_vencimiento=today_co()
        )
        await _config(s)
        cuentas = await PagarService(SqlPagarRepository(s)).cuentas_por_pagar(today_co())

    assert cuentas == []                               # pendiente = 0 → fuera del escaneo


# --- aislamiento multi-tenant -------------------------------------------------
async def test_aislamiento_empresa_a_nunca_ve_cuentas_de_b(tenant_factory):
    empresa_a = await tenant_factory()
    empresa_b = await tenant_factory()

    async with AsyncSession(empresa_a.engine, expire_on_commit=False) as sa:
        await _seed_factura(sa, factura_id="A-1", fecha_vencimiento=today_co())
        await _config(sa)
    async with AsyncSession(empresa_b.engine, expire_on_commit=False) as sb:
        await _config(sb)

        cuentas_b = await PagarService(SqlPagarRepository(sb)).cuentas_por_pagar(today_co())
        registro_b: list[str] = []
        r_b = await PagarService(SqlPagarRepository(sb)).procesar_avisos(
            ahora=_ahora(), enviar=_fake_enviar(registro_b)
        )
        await sb.commit()

    async with AsyncSession(empresa_a.engine, expire_on_commit=False) as sa:
        cuentas_a = await PagarService(SqlPagarRepository(sa)).cuentas_por_pagar(today_co())

    assert cuentas_b == [] and registro_b == [] and r_b.avisos_enviados == 0
    assert [c.factura_id for c in cuentas_a] == ["A-1"]   # A ve lo suyo; B no ve nada de A
