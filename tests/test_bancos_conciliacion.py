"""Conciliación bancaria (ADR 0028) — invariantes críticos contra base efímera real.

Cubre (TDD de invariantes):
- Ingesta idempotente por `referencia_bancaria`: reprocesar el mismo extracto NO duplica.
- Conciliar NO altera saldos (ventas, CxP, caja): solo enlaza registros existentes.
- Montos ambiguos JAMÁS se auto-concilian (≥2 candidatos → queda no_conciliado).
- Aislamiento multi-tenant: la empresa B no ve movimientos de A.
"""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.config.timezone import now_co
from modules.bancos.repository import SqlBancosRepository
from modules.bancos.schemas import MovimientoBancarioIngesta
from modules.bancos.service import BancosService

_DIA = date(2026, 6, 15)
# Mediodía UTC-5: `::date` cae en 2026-06-15 en cualquier TZ de sesión (asyncpg exige datetime, no str).
_TS = datetime(2026, 6, 15, 10, 0, 0, tzinfo=timezone(timedelta(hours=-5)))


def _svc(s: AsyncSession) -> BancosService:
    return BancosService(SqlBancosRepository(s))


async def _usuario(s: AsyncSession) -> int:
    return (
        await s.execute(text("INSERT INTO usuarios (nombre, rol) VALUES ('V','vendedor') RETURNING id"))
    ).scalar_one()


async def _venta_transferencia(s: AsyncSession, *, uid: int, total: str, consecutivo: int) -> int:
    return (
        await s.execute(
            text(
                "INSERT INTO ventas (consecutivo, vendedor_id, fecha, subtotal, impuestos, total, "
                "metodo_pago, estado) VALUES (:c, :uid, :f, :t, 0, :t, 'transferencia', 'completada') "
                "RETURNING id"
            ),
            {"c": consecutivo, "uid": uid, "f": _TS, "t": total},
        )
    ).scalar_one()


async def _snapshot_saldos(s: AsyncSession) -> dict[str, Decimal]:
    """Suma de los libros que la conciliación NUNCA debe tocar."""
    async def _sum(q: str) -> Decimal:
        return Decimal((await s.execute(text(q))).scalar_one())
    return {
        "ventas": await _sum("SELECT COALESCE(SUM(total),0) FROM ventas"),
        "cxp_pendiente": await _sum("SELECT COALESCE(SUM(pendiente),0) FROM facturas_proveedores"),
        "gastos": await _sum("SELECT COALESCE(SUM(monto),0) FROM gastos"),
        "caja_mov": await _sum("SELECT COALESCE(SUM(monto),0) FROM caja_movimientos"),
        # Desde la fase 2 un abono de fiado también se puede enlazar: enlazarlo no puede mover deuda.
        "fiados_saldo": await _sum("SELECT COALESCE(SUM(saldo),0) FROM fiados"),
    }


async def test_ingesta_idempotente_por_referencia(tenant):
    mov = MovimientoBancarioIngesta(
        referencia_bancaria="REF-001", fecha=_DIA, monto=Decimal("100000"), naturaleza="credito",
    )
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        r1 = await _svc(s).ingestar([mov, mov])   # el mismo extracto trae la línea repetida
        await s.commit()
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        r2 = await _svc(s).ingestar([mov])        # reprocesar el extracto completo otra vez
        await s.commit()

    assert r1.insertados == 1 and r1.duplicados == 1     # dentro de la misma corrida ya deduplica
    assert r2.insertados == 0 and r2.duplicados == 1     # reproceso: cero duplicados nuevos
    async with AsyncSession(tenant.engine) as s:
        n = (
            await s.execute(
                text("SELECT count(*) FROM bancolombia_transferencias WHERE referencia_bancaria='REF-001'")
            )
        ).scalar_one()
    assert n == 1


async def test_conciliar_no_altera_saldos(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid = await _usuario(s)
        await _venta_transferencia(s, uid=uid, total="250000", consecutivo=1)
        await s.commit()
        antes = await _snapshot_saldos(s)

        svc = _svc(s)
        await svc.ingestar([MovimientoBancarioIngesta(
            referencia_bancaria="REF-V1", fecha=_DIA, monto=Decimal("250000"), naturaleza="credito",
        )])
        await s.commit()
        assert await svc.sugerir_pendientes() == 1        # candidato único → sugerido
        await s.commit()

        pendientes = await svc.listar(estado="sugerido")
        mov_id = pendientes[0].movimiento.id
        cand = pendientes[0].candidatos[0]
        leer = await svc.confirmar(mov_id, tipo=cand.tipo, id_interno=cand.id, ahora=now_co())
        await s.commit()
        assert leer.estado_conciliacion == "conciliado"

        despues = await _snapshot_saldos(s)
    assert antes == despues, f"conciliar alteró saldos: {antes} != {despues}"


async def test_montos_ambiguos_nunca_se_autoconcilian(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid = await _usuario(s)
        # DOS ventas idénticas en monto+fecha → ambigüedad.
        await _venta_transferencia(s, uid=uid, total="80000", consecutivo=1)
        await _venta_transferencia(s, uid=uid, total="80000", consecutivo=2)
        await s.commit()

        svc = _svc(s)
        await svc.ingestar([MovimientoBancarioIngesta(
            referencia_bancaria="REF-AMB", fecha=_DIA, monto=Decimal("80000"), naturaleza="credito",
        )])
        await s.commit()
        assert await svc.sugerir_pendientes() == 0        # regla dura: 2 candidatos → NO se toca
        await s.commit()

        pend = await svc.listar(estado=None)
    assert len(pend) == 1
    assert pend[0].movimiento.estado_conciliacion == "no_conciliado"
    assert len(pend[0].candidatos) == 2                   # ambos candidatos listados para resolver a mano


async def test_confirmar_enlace_invalido_no_concilia(tenant):
    from modules.bancos.errors import ConciliacionInvalida
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        svc = _svc(s)
        await svc.ingestar([MovimientoBancarioIngesta(
            referencia_bancaria="REF-X", fecha=_DIA, monto=Decimal("999"), naturaleza="credito",
        )])
        await s.commit()
        mov = (await svc.listar(estado=None))[0].movimiento
        with pytest.raises(ConciliacionInvalida):
            await svc.confirmar(mov.id, tipo="venta", id_interno=424242, ahora=now_co())
        await s.rollback()


async def test_aislamiento_a_no_ve_movimientos_de_b(tenant_factory):
    a = await tenant_factory()
    b = await tenant_factory()
    async with AsyncSession(a.engine, expire_on_commit=False) as s:
        await _svc(s).ingestar([MovimientoBancarioIngesta(
            referencia_bancaria="A-REF", fecha=_DIA, monto=Decimal("1000"), naturaleza="credito",
        )])
        await s.commit()
    async with AsyncSession(b.engine) as s:
        assert await _svc(s).listar(estado=None) == []
    async with AsyncSession(a.engine) as s:
        movs = await _svc(s).listar(estado=None)
    assert [m.movimiento.referencia_bancaria for m in movs] == ["A-REF"]


# --- 0073: un solo libro + "no es venta" -------------------------------------

async def _transferencia_gmail(s: AsyncSession, *, mid: str, monto: str) -> int:
    """Una transferencia como la que deja el correo del banco: sin `referencia_bancaria`."""
    mov = await SqlBancosRepository(s).ingestar_gmail(
        gmail_message_id=mid, fecha=_DIA, monto=Decimal(monto), remitente="JUAN PEREZ",
        descripcion=None, tipo_transaccion="Código QR", hora="09:12",
        cuenta_destino="*3891", referencia="0046052593",
    )
    return mov.id


async def test_listar_incluye_las_que_llegaron_por_el_correo(tenant):
    """La regresión que dejaba el tab vacío: `listar` filtraba `referencia_bancaria IS NOT NULL`.

    Las filas de Gmail nacen con esa columna en NULL, así que desaparecían de la pantalla y del
    match. El ADR 0028 (D1) declaró UN libro de movimientos bancarios; el filtro creaba dos.
    """
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await _transferencia_gmail(s, mid="msg-1", monto="150000")
        await _svc(s).ingestar([MovimientoBancarioIngesta(
            referencia_bancaria="REF-EXT", fecha=_DIA, monto=Decimal("90000"), naturaleza="credito",
        )])
        await s.commit()

        movs = await _svc(s).listar(estado=None)

    origenes = sorted(m.movimiento.origen for m in movs)
    assert origenes == ["extracto", "gmail"]
    gmail = next(m.movimiento for m in movs if m.movimiento.origen == "gmail")
    assert gmail.cuenta_destino == "*3891" and gmail.remitente == "JUAN PEREZ"


async def test_la_ingesta_de_gmail_guarda_cuenta_y_llave(tenant):
    """El parser las extraía desde siempre; hasta 0073 se tiraban antes de llegar a la base."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        mov_id = await _transferencia_gmail(s, mid="msg-cuenta", monto="80000")
        await s.commit()

        fila = (
            await s.execute(
                text("SELECT cuenta_destino, referencia FROM bancolombia_transferencias WHERE id=:i"),
                {"i": mov_id},
            )
        ).one()
    assert fila.cuenta_destino == "*3891"
    assert fila.referencia == "0046052593"


async def test_descartar_saca_del_pendiente_y_se_puede_deshacer(tenant):
    """"No es venta": la plata de la casa sale de la lista sin borrarse de la base."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        mov_id = await _transferencia_gmail(s, mid="msg-personal", monto="500000")
        await s.commit()
        svc = _svc(s)

        leer = await svc.descartar(mov_id, descartar=True, ahora=now_co())
        await s.commit()
        assert leer.descartado_en is not None
        assert [m.movimiento.id for m in await svc.listar(estado=None)] == []
        # Sigue existiendo: se puede consultar pidiéndolos explícitamente.
        con_descartados = await svc.listar(estado=None, incluir_descartados=True)
        assert [m.movimiento.id for m in con_descartados] == [mov_id]

        await svc.descartar(mov_id, descartar=False, ahora=now_co())
        await s.commit()
        assert [m.movimiento.id for m in await svc.listar(estado=None)] == [mov_id]


async def test_descartar_dos_veces_conserva_el_primer_sello(tenant):
    """Idempotencia (regla #8): repetir la acción no cambia nada, ni la fecha del sello."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        mov_id = await _transferencia_gmail(s, mid="msg-idem", monto="70000")
        await s.commit()
        svc = _svc(s)

        primero = await svc.descartar(mov_id, descartar=True, ahora=now_co())
        await s.commit()
        segundo = await svc.descartar(mov_id, descartar=True, ahora=now_co())
        await s.commit()

    assert primero.descartado_en == segundo.descartado_en


async def test_descartar_un_sugerido_libera_la_venta(tenant):
    """INVARIANTE de dinero: la venta que quedó colgando debe volver a ofrecerse.

    El filtro anti-doble-uso da por tomado todo interno enlazado en estado `sugerido`. Si al
    descartar una transferencia no se suelta su sugerencia, esa venta queda secuestrada para
    siempre: la transferencia que SÍ la pagó nunca volvería a verla como candidata.
    """
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid = await _usuario(s)
        await _venta_transferencia(s, uid=uid, total="120000", consecutivo=77)
        await s.commit()
        svc = _svc(s)

        personal = await _transferencia_gmail(s, mid="msg-confunde", monto="120000")
        await s.commit()
        assert await svc.sugerir_pendientes() == 1      # se enganchó a la venta por monto+fecha
        await s.commit()

        await svc.descartar(personal, descartar=True, ahora=now_co())
        await s.commit()

        # La de verdad llega después y la venta tiene que estar libre.
        real = await _transferencia_gmail(s, mid="msg-real", monto="120000")
        await s.commit()
        assert await svc.sugerir_pendientes() == 1
        await s.commit()

        sugeridos = await svc.listar(estado="sugerido")

    assert [m.movimiento.id for m in sugeridos] == [real]


# --- fase 2: que el match sirva (mixtas, fiados y el día en hora Colombia) ----

async def _cliente(s: AsyncSession, nombre: str) -> int:
    return (
        await s.execute(text("INSERT INTO clientes (nombre) VALUES (:n) RETURNING id"), {"n": nombre})
    ).scalar_one()


async def _venta_mixta(
    s: AsyncSession, *, uid: int, cliente_id: int | None, total: str, parte: str, consecutivo: int
) -> int:
    """Venta cobrada en dos partes (0053): una en efectivo y otra por transferencia."""
    venta_id = (
        await s.execute(
            text(
                "INSERT INTO ventas (consecutivo, cliente_id, vendedor_id, fecha, subtotal, impuestos, "
                "total, metodo_pago, estado) VALUES (:c, :cl, :uid, :f, :t, 0, :t, 'mixto', 'completada') "
                "RETURNING id"
            ),
            {"c": consecutivo, "cl": cliente_id, "uid": uid, "f": _TS, "t": total},
        )
    ).scalar_one()
    for metodo, monto in (("efectivo", str(Decimal(total) - Decimal(parte))), ("transferencia", parte)):
        await s.execute(
            text("INSERT INTO ventas_pagos (venta_id, metodo, monto) VALUES (:v, :m, :n)"),
            {"v": venta_id, "m": metodo, "n": monto},
        )
    return venta_id


async def _abono_de_fiado(s: AsyncSession, *, cliente_id: int, monto: str) -> int:
    """El cliente que debía y paga: devuelve el id del movimiento de abono."""
    fiado_id = (
        await s.execute(
            text(
                "INSERT INTO fiados (cliente_id, monto, saldo, creado_en) "
                "VALUES (:cl, :m, 0, :f) RETURNING id"
            ),
            {"cl": cliente_id, "m": monto, "f": _TS},
        )
    ).scalar_one()
    return (
        await s.execute(
            text(
                "INSERT INTO fiados_movimientos (fiado_id, tipo, monto, creado_en) "
                "VALUES (:fi, 'abono', :m, :f) RETURNING id"
            ),
            {"fi": fiado_id, "m": monto, "f": _TS},
        )
    ).scalar_one()


async def test_venta_mixta_es_candidata_por_el_monto_de_su_parte(tenant):
    """El cliente pagó una parte en efectivo y otra por transferencia: al banco llegó SOLO la parte.

    Comparar contra el total de la venta la volvía invisible para el match, aunque los reportes por
    método sí la expanden en sus partes desde 0053.
    """
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid = await _usuario(s)
        cli = await _cliente(s, "PEDRO RAMIREZ")
        venta_id = await _venta_mixta(
            s, uid=uid, cliente_id=cli, total="200000", parte="120000", consecutivo=91
        )
        await s.commit()
        svc = _svc(s)
        mov_id = await _transferencia_gmail(s, mid="msg-mixta", monto="120000")
        await s.commit()

        assert await svc.sugerir_pendientes() == 1        # calzó por la parte, no por el total
        await s.commit()
        sugerido = (await svc.listar(estado="sugerido"))[0]

    assert sugerido.movimiento.id == mov_id
    assert sugerido.movimiento.conciliado_con_tipo == "venta"     # el enlace es la venta, no media venta
    assert sugerido.movimiento.conciliado_con_id == venta_id
    assert sugerido.candidatos[0].cliente == "PEDRO RAMIREZ"


async def test_abono_de_fiado_es_candidato_y_se_concilia(tenant):
    """El caso que nombró el dueño: el cliente que debía paga su fiado por transferencia."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        cli = await _cliente(s, "ANA LOPEZ")
        abono_id = await _abono_de_fiado(s, cliente_id=cli, monto="60000")
        await s.commit()
        svc = _svc(s)
        mov_id = await _transferencia_gmail(s, mid="msg-fiado", monto="60000")
        await s.commit()

        assert await svc.sugerir_pendientes() == 1
        await s.commit()
        cand = (await svc.listar(estado="sugerido"))[0].candidatos[0]
        assert (cand.tipo, cand.id, cand.cliente) == ("abono_fiado", abono_id, "ANA LOPEZ")

        leer = await svc.confirmar(mov_id, tipo=cand.tipo, id_interno=cand.id, ahora=now_co())
        await s.commit()

    assert leer.estado_conciliacion == "conciliado"
    assert leer.conciliado_con_tipo == "abono_fiado"


async def test_las_tres_fuentes_del_mismo_monto_siguen_siendo_ambiguas(tenant):
    """La regla dura del ADR 0028 no se relaja al sumar fuentes: ≥2 candidatos → decide una persona.

    Una variante de ambigüedad por cada fuente de crédito: venta entera, parte de una mixta y abono
    de fiado, todas por $45.000 el mismo día.
    """
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid = await _usuario(s)
        cli = await _cliente(s, "LUIS DIAZ")
        await _venta_transferencia(s, uid=uid, total="45000", consecutivo=92)
        await _venta_mixta(s, uid=uid, cliente_id=cli, total="70000", parte="45000", consecutivo=94)
        await _abono_de_fiado(s, cliente_id=cli, monto="45000")
        await s.commit()
        svc = _svc(s)
        await _transferencia_gmail(s, mid="msg-ambiguo-2", monto="45000")
        await s.commit()

        assert await svc.sugerir_pendientes() == 0
        await s.commit()
        pend = await svc.listar(estado=None)

    assert pend[0].movimiento.estado_conciliacion == "no_conciliado"
    assert sorted(c.tipo for c in pend[0].candidatos) == ["abono_fiado", "venta", "venta"]


async def test_el_dia_del_match_es_el_dia_colombiano(tenant):
    """Una venta de las 7 p. m. pertenece a ESE día en Colombia, no al siguiente.

    `venta.fecha::date` se resolvía con el `TimeZone` de la sesión de Postgres: con la sesión en UTC,
    una venta de las 19:00 (= 00:00 UTC del día siguiente) dejaba de calzar con la transferencia que
    la pagó. Regla no negociable #4.
    """
    noche = datetime(2026, 6, 15, 19, 0, 0, tzinfo=timezone(timedelta(hours=-5)))
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid = await _usuario(s)
        await s.execute(
            text(
                "INSERT INTO ventas (consecutivo, vendedor_id, fecha, subtotal, impuestos, total, "
                "metodo_pago, estado) VALUES (93, :uid, :f, 75000, 0, 75000, 'transferencia', 'completada')"
            ),
            {"uid": uid, "f": noche},
        )
        await s.commit()
        await _transferencia_gmail(s, mid="msg-noche", monto="75000")
        await s.commit()

        # LOCAL: se deshace al cerrar la transacción, no contamina la conexión del pool.
        await s.execute(text("SET LOCAL TIME ZONE 'UTC'"))
        sugeridos = await _svc(s).sugerir_pendientes()
        await s.rollback()

    assert sugeridos == 1


# --- fase 3: cuánta plata entró ----------------------------------------------

async def _credito(
    s: AsyncSession, *, mid: str, monto: str, cuenta: str | None, fecha: date = _DIA
) -> int:
    mov = await SqlBancosRepository(s).ingestar_gmail(
        gmail_message_id=mid, fecha=fecha, monto=Decimal(monto), remitente="ALGUIEN",
        descripcion=None, tipo_transaccion=None, hora=None, cuenta_destino=cuenta, referencia=None,
    )
    return mov.id


async def test_totales_separan_lo_del_negocio_de_lo_personal(tenant):
    """`total == negocio + personal` por construcción: los de arriba son la suma de las cuentas."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await _credito(s, mid="t1", monto="100000", cuenta="*3891")
        await _credito(s, mid="t2", monto="250000", cuenta="*6485")
        casa = await _credito(s, mid="t3", monto="80000", cuenta="*3891")
        await s.commit()
        svc = _svc(s)
        await svc.descartar(casa, descartar=True, ahora=now_co())
        await s.commit()

        tot = await svc.totales(desde=_DIA, hasta=_DIA, alias={"*3891": "Andrés"})

    assert tot.total == Decimal("430000")
    assert tot.total_negocio == Decimal("350000")
    assert tot.total_personal == Decimal("80000")
    assert tot.total == tot.total_negocio + tot.total_personal
    # Lo descartado NO desaparece del total: entró a la cuenta, solo no es del negocio.
    por_cuenta = {c.cuenta: c for c in tot.por_cuenta}
    assert por_cuenta["*3891"].total == Decimal("180000")
    assert por_cuenta["*3891"].total_negocio == Decimal("100000")
    assert por_cuenta["*3891"].alias == "Andrés"
    assert por_cuenta["*6485"].alias is None      # sin alias configurado: la UI muestra el número


async def test_la_cuenta_que_el_parser_no_leyo_se_muestra_no_se_esconde(tenant):
    """`cuenta_destino` NULL es "sin identificar", no un movimiento que se cae del reporte."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await _credito(s, mid="t-sin", monto="45000", cuenta=None)
        await s.commit()
        tot = await _svc(s).totales(desde=_DIA, hasta=_DIA, alias={})

    assert tot.total == Decimal("45000")
    assert [c.cuenta for c in tot.por_cuenta] == [None]


async def test_los_totales_respetan_el_periodo_y_cuentan_lo_sin_clasificar(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await _credito(s, mid="t-dentro", monto="10000", cuenta="*3891")
        await _credito(s, mid="t-fuera", monto="999999", cuenta="*3891", fecha=date(2026, 7, 1))
        conciliado = await _credito(s, mid="t-ok", monto="20000", cuenta="*3891")
        await s.commit()
        uid = await _usuario(s)
        await _venta_transferencia(s, uid=uid, total="20000", consecutivo=95)
        await s.commit()
        svc = _svc(s)
        await svc.sugerir_pendientes()
        await s.commit()
        cand = next(
            m.candidatos[0] for m in await svc.listar(estado="sugerido") if m.movimiento.id == conciliado
        )
        await svc.confirmar(conciliado, tipo=cand.tipo, id_interno=cand.id, ahora=now_co())
        await s.commit()

        tot = await svc.totales(desde=_DIA, hasta=_DIA, alias={})

    assert tot.total == Decimal("30000")          # el de julio queda fuera del período
    assert tot.sin_clasificar == 1                # el conciliado ya no cuenta como pendiente


async def test_totales_no_ven_los_debitos(tenant):
    """Los egresos de esas cuentas se mezclan con lo personal: el dueño decidió no llevarlos."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await _svc(s).ingestar([
            MovimientoBancarioIngesta(
                referencia_bancaria="D-1", fecha=_DIA, monto=Decimal("70000"), naturaleza="debito",
            ),
            MovimientoBancarioIngesta(
                referencia_bancaria="C-1", fecha=_DIA, monto=Decimal("30000"), naturaleza="credito",
            ),
        ])
        await s.commit()
        tot = await _svc(s).totales(desde=_DIA, hasta=_DIA, alias={})

    assert tot.total == Decimal("30000")


async def test_totales_aislados_entre_empresas(tenant_factory):
    """La plata que entró a la empresa A no puede aparecer en el reporte de B."""
    a = await tenant_factory()
    b = await tenant_factory()
    async with AsyncSession(a.engine, expire_on_commit=False) as s:
        await _credito(s, mid="a-1", monto="500000", cuenta="*3891")
        await s.commit()
    async with AsyncSession(b.engine) as s:
        tot_b = await _svc(s).totales(desde=_DIA, hasta=_DIA, alias={})
    async with AsyncSession(a.engine) as s:
        tot_a = await _svc(s).totales(desde=_DIA, hasta=_DIA, alias={})

    assert tot_b.total == Decimal("0") and tot_b.por_cuenta == []
    assert tot_a.total == Decimal("500000")


def test_un_alias_mal_escrito_no_tumba_el_reporte():
    """Los alias son etiquetas: cualquier basura en la config degrada a mostrar el número de cuenta."""
    from modules.bancos.config import parsear_alias

    assert parsear_alias('{"*3891": "Andrés"}') == {"*3891": "Andrés"}
    assert parsear_alias(None) == {}
    assert parsear_alias("  ") == {}
    assert parsear_alias("{no es json") == {}
    assert parsear_alias('["*3891"]') == {}          # JSON válido, pero no un objeto
    assert parsear_alias('{"*3891": null}') == {}    # alias vacío: mejor el número que "None"


async def test_no_se_puede_descartar_uno_ya_conciliado(tenant):
    """Está enlazado a una venta real: marcarlo personal es contradictorio."""
    from modules.bancos.errors import ConciliacionInvalida

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid = await _usuario(s)
        await _venta_transferencia(s, uid=uid, total="330000", consecutivo=88)
        await s.commit()
        svc = _svc(s)
        mov_id = await _transferencia_gmail(s, mid="msg-conciliado", monto="330000")
        await s.commit()
        await svc.sugerir_pendientes()
        await s.commit()
        cand = (await svc.listar(estado="sugerido"))[0].candidatos[0]
        await svc.confirmar(mov_id, tipo=cand.tipo, id_interno=cand.id, ahora=now_co())
        await s.commit()

        with pytest.raises(ConciliacionInvalida):
            await svc.descartar(mov_id, descartar=True, ahora=now_co())
