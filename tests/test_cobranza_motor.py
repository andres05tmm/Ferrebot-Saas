"""Motor de cobranza (ADR 0015) — el job determinista sobre la base efímera real.

Cubre los guardarraíles del MOTOR (no dependen del LLM): ventana horaria, cadencia entre envíos,
tope de recordatorios por ciclo, opt-out (Habeas Data), pausa por promesa vigente (y su vencimiento →
`incumplida`), cierre de ciclo al pagar (contador a 0 + promesa `cumplida`) y que un envío fallido NO
sella el dedup. El envío real (plantilla Kapso) se inyecta como callback y aquí se falsea.
"""
from datetime import datetime, time, timedelta
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.config.timezone import COLOMBIA_TZ, today_co
from modules.cobranza.models import CobranzaConfig
from modules.cobranza.repository import SqlCobranzaRepository
from modules.cobranza.service import CobranzaService, DeudorRecordatorio


def _ahora(hora: int = 10) -> datetime:
    """Un instante de hoy a la hora dada (Colombia): controla la ventana horaria del motor."""
    return datetime.combine(today_co(), time(hora, 0), tzinfo=COLOMBIA_TZ)


def _fake_enviar(registro: list[int], *, ok: bool = True):
    """Callback de envío falso: registra el cliente_id de cada deudor y reporta éxito (`ok`)."""
    async def enviar(deudor: DeudorRecordatorio) -> bool:
        registro.append(deudor.cliente_id)
        return ok
    return enviar


async def _seed_cliente(
    s: AsyncSession, *, nombre: str = "Ana", telefono: str | None = "3001112233",
    saldo: str = "150000",
) -> int:
    cliente_id = (
        await s.execute(
            text(
                "INSERT INTO clientes (nombre, telefono, saldo_fiado) "
                "VALUES (:n, :t, :s) RETURNING id"
            ),
            {"n": nombre, "t": telefono, "s": saldo},
        )
    ).scalar_one()
    await s.commit()
    return cliente_id


async def _config(s: AsyncSession, **valores) -> CobranzaConfig:
    """Config get-or-create con overrides directos (el motor la lee tal cual)."""
    repo = SqlCobranzaRepository(s)
    config = await repo.obtener_config()
    for campo, valor in valores.items():
        setattr(config, campo, valor)
    await s.commit()
    return config


# --- envío + cadencia ---------------------------------------------------------
async def test_envia_sella_y_respeta_cadencia(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        cliente = await _seed_cliente(s)
        await _config(s, cadencia_dias=7)
        svc = CobranzaService(SqlCobranzaRepository(s))

        primera: list[int] = []
        r1 = await svc.procesar_recordatorios(ahora=_ahora(), enviar=_fake_enviar(primera))
        await s.commit()
        segunda: list[int] = []
        r2 = await svc.procesar_recordatorios(ahora=_ahora(11), enviar=_fake_enviar(segunda))
        await s.commit()
        tercera: list[int] = []
        r3 = await svc.procesar_recordatorios(
            ahora=_ahora() + timedelta(days=8), enviar=_fake_enviar(tercera)
        )
        await s.commit()

    assert primera == [cliente] and r1.recordatorios == 1
    assert segunda == [] and r2.recordatorios == 0       # cadencia: 1h después NO toca
    assert tercera == [cliente] and r3.recordatorios == 1  # 8 días después sí


async def test_envio_fallido_no_sella_dedup(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        cliente = await _seed_cliente(s)
        await _config(s)
        svc = CobranzaService(SqlCobranzaRepository(s))
        r = await svc.procesar_recordatorios(ahora=_ahora(), enviar=_fake_enviar([], ok=False))
        await s.commit()
        estado = await SqlCobranzaRepository(s).estado_cliente(cliente)

    assert r.recordatorios == 0
    assert estado.recordatorios_enviados == 0 and estado.ultimo_recordatorio_en is None


async def test_tope_max_recordatorios(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        cliente = await _seed_cliente(s)
        await _config(s, max_recordatorios=2, cadencia_dias=1)
        svc = CobranzaService(SqlCobranzaRepository(s))

        enviados: list[int] = []
        for dias in (0, 2, 4, 6):   # 4 corridas espaciadas: solo 2 pueden enviar
            await svc.procesar_recordatorios(
                ahora=_ahora() + timedelta(days=dias), enviar=_fake_enviar(enviados)
            )
            await s.commit()

    assert enviados == [cliente, cliente]   # tope del ciclo: lo retoma el negocio, no el bot


# --- guardarraíles ---------------------------------------------------------------
async def test_ventana_horaria_no_envia_fuera_de_horario(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await _seed_cliente(s)
        await _config(s, hora_inicio=time(9), hora_fin=time(19))
        svc = CobranzaService(SqlCobranzaRepository(s))
        registro: list[int] = []
        r = await svc.procesar_recordatorios(ahora=_ahora(22), enviar=_fake_enviar(registro))
        await s.commit()

    assert registro == [] and r.recordatorios == 0


async def test_opt_out_y_sin_telefono_se_saltan(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        con_optout = await _seed_cliente(s, nombre="OptOut", telefono="3007770000")
        await _seed_cliente(s, nombre="SinTel", telefono=None)
        cobrable = await _seed_cliente(s, nombre="Cobrable", telefono="3008880000")
        await _config(s)
        repo = SqlCobranzaRepository(s)
        await repo.marcar_opt_out(con_optout, True)
        await s.commit()

        registro: list[int] = []
        r = await CobranzaService(repo).procesar_recordatorios(
            ahora=_ahora(), enviar=_fake_enviar(registro)
        )
        await s.commit()

    assert registro == [cobrable] and r.recordatorios == 1


async def test_config_inactiva_no_envia(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await _seed_cliente(s)
        await _config(s, activo=False)
        registro: list[int] = []
        r = await CobranzaService(SqlCobranzaRepository(s)).procesar_recordatorios(
            ahora=_ahora(), enviar=_fake_enviar(registro)
        )
        await s.commit()

    assert registro == [] and r.recordatorios == 0


# --- promesas de pago --------------------------------------------------------------
async def test_promesa_vigente_pausa_y_vencida_reanuda(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        cliente = await _seed_cliente(s)
        await _config(s)
        repo = SqlCobranzaRepository(s)
        promesa = await repo.crear_promesa(
            cliente, telefono="3001112233", fecha=today_co() + timedelta(days=3)
        )
        await s.commit()
        svc = CobranzaService(repo)

        en_pausa: list[int] = []
        r1 = await svc.procesar_recordatorios(ahora=_ahora(), enviar=_fake_enviar(en_pausa))
        await s.commit()

        vencida: list[int] = []
        r2 = await svc.procesar_recordatorios(
            ahora=_ahora() + timedelta(days=5), enviar=_fake_enviar(vencida)
        )
        await s.commit()
        await s.refresh(promesa)

    assert en_pausa == [] and r1.recordatorios == 0       # la promesa compra silencio
    assert vencida == [cliente] and r2.recordatorios == 1  # vencida con deuda → se reanuda
    assert promesa.estado == "incumplida" and r2.promesas_incumplidas == 1


async def test_cierre_de_ciclo_al_pagar(tenant):
    """Saldo en 0 con recordatorios abiertos → contador a 0 y la promesa vigente queda `cumplida`."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        cliente = await _seed_cliente(s)
        await _config(s)
        repo = SqlCobranzaRepository(s)
        svc = CobranzaService(repo)
        await svc.procesar_recordatorios(ahora=_ahora(), enviar=_fake_enviar([]))
        promesa = await repo.crear_promesa(
            cliente, telefono="3001112233", fecha=today_co() + timedelta(days=3)
        )
        await s.commit()

        # El cliente paga (el abono real lo registra el POS; aquí simulamos el contador saldado).
        await s.execute(
            text("UPDATE clientes SET saldo_fiado = 0 WHERE id = :cid"), {"cid": cliente}
        )
        await s.commit()

        registro: list[int] = []
        r = await svc.procesar_recordatorios(ahora=_ahora(11), enviar=_fake_enviar(registro))
        await s.commit()
        estado = await repo.estado_cliente(cliente)
        await s.refresh(promesa)

    assert r.al_dia == 1 and registro == []
    assert estado.recordatorios_enviados == 0 and estado.ultimo_recordatorio_en is None
    assert promesa.estado == "cumplida"


async def test_recuperado_atribuye_abonos_posteriores_al_recordatorio(tenant):
    """La métrica cuenta SOLO los abonos que siguieron a un recordatorio (ventana de atribución).

    El log `cobranza_recordatorios` es append-only: sobrevive al cierre de ciclo (que resetea el
    estado vivo), por eso "recuperado" no se pierde cuando el cliente queda al día.
    """
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        recordado = await _seed_cliente(s, nombre="Recordada", telefono="3001110000")
        directo = await _seed_cliente(s, nombre="Directo", telefono="3002220000")
        await _config(s)
        repo = SqlCobranzaRepository(s)
        svc = CobranzaService(repo)

        # Solo "Recordada" recibe recordatorio (a "Directo" lo saltamos con opt_out).
        await repo.marcar_opt_out(directo, True)
        await s.commit()
        r = await svc.procesar_recordatorios(ahora=_ahora(), enviar=_fake_enviar([]))
        assert r.recordatorios == 1

        # Ambos abonan DESPUÉS del recordatorio (creado_en explícito: el server default usaría la
        # hora real, que puede caer antes del `ahora` inyectado): solo el abono de "Recordada" es
        # atribuible al agente.
        despues = _ahora() + timedelta(hours=2)
        for cliente, monto in ((recordado, "60000"), (directo, "40000")):
            fiado_id = (
                await s.execute(
                    text(
                        "INSERT INTO fiados (cliente_id, monto, saldo) "
                        "VALUES (:c, 150000, 150000) RETURNING id"
                    ),
                    {"c": cliente},
                )
            ).scalar_one()
            await s.execute(
                text(
                    "INSERT INTO fiados_movimientos (fiado_id, tipo, monto, creado_en) "
                    "VALUES (:f, 'abono', :m, :cuando)"
                ),
                {"f": fiado_id, "m": monto, "cuando": despues},
            )
        await s.commit()

        total = await repo.recuperado(desde=_ahora() - timedelta(days=30))
        # El cierre de ciclo (pago total) NO borra el log: la métrica sobrevive.
        await s.execute(text("UPDATE clientes SET saldo_fiado = 0"))
        await s.commit()
        await svc.procesar_recordatorios(ahora=_ahora(11), enviar=_fake_enviar([]))
        total_tras_cierre = await repo.recuperado(desde=_ahora() - timedelta(days=30))

    assert total == Decimal("60000.00")
    assert total_tras_cierre == Decimal("60000.00")


async def test_saldo_minimo_filtra_deudas_chicas(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await _seed_cliente(s, nombre="Chica", telefono="3001110000", saldo="4000")
        grande = await _seed_cliente(s, nombre="Grande", telefono="3002220000", saldo="90000")
        await _config(s, saldo_minimo=Decimal("5000"))
        registro: list[int] = []
        await CobranzaService(SqlCobranzaRepository(s)).procesar_recordatorios(
            ahora=_ahora(), enviar=_fake_enviar(registro)
        )
        await s.commit()

    assert registro == [grande]
