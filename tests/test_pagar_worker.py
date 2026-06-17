"""Cron de cuentas por pagar (ADR 0019, Fase 2): el seam de envío INTERNO del worker.

El barrido multi-tenant del cron `avisos_pagar` es smoke-manual (como los demás crons del repo: la
lógica determinista vive en `PagarService`, ya cubierta en test_pagar_motor). Aquí se prueba el
callback REAL `_hacer_enviar_pagar` integrado con el motor sobre la base efímera del tenant —el seam
exacto que arma el cron— y sus invariantes de Fase 2:

  * publica el evento INTERNO `pagar_aviso` (SSE, sin costo de plantilla) y SOLO un envío exitoso
    sella el dedup;
  * idempotencia end-to-end: una segunda corrida dentro de la cadencia no reenvía ni resella;
  * aislamiento multi-tenant: el callback de la empresa A no ve ni toca cuentas de B.

Se espía `apps.worker.main.publish` para capturar el evento sin necesitar un listener de pg_notify; el
sellado del dedup (repo) es independiente de la publicación, así que el espía no lo altera.
"""
from datetime import datetime, time, timedelta
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

import apps.worker.main as worker
from core.config.timezone import COLOMBIA_TZ, today_co
from modules.pagar.repository import SqlPagarRepository
from modules.pagar.service import PagarService


def _ahora(hora: int = 10) -> datetime:
    return datetime.combine(today_co(), time(hora, 0), tzinfo=COLOMBIA_TZ)


def _espia_publish(monkeypatch):
    """Reemplaza `apps.worker.main.publish` por un recorder; devuelve la lista de eventos capturados."""
    eventos: list[tuple] = []

    async def fake_publish(session, event, data):
        eventos.append((event, data))

    monkeypatch.setattr(worker, "publish", fake_publish)
    return eventos


async def _seed_factura(s: AsyncSession, *, factura_id="F-001", pendiente="100000", fecha_vencimiento=None):
    await s.execute(
        text(
            "INSERT INTO facturas_proveedores "
            "(id, proveedor, total, pagado, pendiente, estado, fecha, fecha_vencimiento) "
            "VALUES (:id, 'Tornillos SA', :pend, 0, :pend, 'pendiente', :f, :fv)"
        ),
        {"id": factura_id, "pend": pendiente, "f": today_co(), "fv": fecha_vencimiento},
    )
    await s.commit()
    return factura_id


async def _config(s: AsyncSession, **valores):
    repo = SqlPagarRepository(s)
    config = await repo.obtener_config()
    for campo, valor in valores.items():
        setattr(config, campo, valor)
    await s.commit()
    return config


async def test_callback_interno_publica_evento_y_sella_dedup(tenant, monkeypatch):
    eventos = _espia_publish(monkeypatch)
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        factura = await _seed_factura(s, fecha_vencimiento=today_co() + timedelta(days=2))
        await _config(s, dias_aviso_previo=3, cadencia_dias=3)
        svc = PagarService(SqlPagarRepository(s))

        # 1ª corrida: el callback REAL del worker publica el aviso interno y el motor sella el dedup.
        r1 = await svc.procesar_avisos(ahora=_ahora(), enviar=worker._hacer_enviar_pagar(s))
        await s.commit()
        # 2ª corrida dentro de la cadencia: no reenvía ni resella (idempotencia end-to-end).
        r2 = await svc.procesar_avisos(ahora=_ahora(11), enviar=worker._hacer_enviar_pagar(s))
        await s.commit()

        estado = await SqlPagarRepository(s).estado_factura(factura)

    assert r1.avisos_enviados == 1 and r1.facturas_notificadas == 1
    assert len(eventos) == 1 and eventos[0][0] == "pagar_aviso"
    assert eventos[0][1]["facturas"] == 1 and eventos[0][1]["total_por_vencer"] == "100000.00"
    assert r2.avisos_enviados == 0 and len(eventos) == 1   # no se publicó un segundo evento
    assert estado.avisos_enviados == 1                     # dedup sellado una sola vez


async def test_callback_interno_aislamiento_a_no_toca_b(tenant_factory, monkeypatch):
    eventos = _espia_publish(monkeypatch)
    empresa_a = await tenant_factory()
    empresa_b = await tenant_factory()

    async with AsyncSession(empresa_a.engine, expire_on_commit=False) as sa:
        await _seed_factura(sa, factura_id="A-1", fecha_vencimiento=today_co())
        await _config(sa)

    # La corrida de B (sin cuentas) con el callback real no publica nada ni sella nada.
    async with AsyncSession(empresa_b.engine, expire_on_commit=False) as sb:
        await _config(sb)
        r_b = await PagarService(SqlPagarRepository(sb)).procesar_avisos(
            ahora=_ahora(), enviar=worker._hacer_enviar_pagar(sb)
        )
        await sb.commit()

    assert r_b.avisos_enviados == 0 and eventos == []
    # A conserva su cuenta intacta (B nunca la tocó).
    async with AsyncSession(empresa_a.engine, expire_on_commit=False) as sa:
        cuentas_a = await PagarService(SqlPagarRepository(sa)).cuentas_por_pagar(today_co())
    assert [c.factura_id for c in cuentas_a] == ["A-1"]


def test_avisos_pagar_registrado_en_cron_jobs():
    """El cron quedó cableado en el runtime ARQ (no solo definido)."""
    funcs = {getattr(c, "coroutine", None) for c in worker.WorkerSettings.cron_jobs}
    assert worker.avisos_pagar in funcs
