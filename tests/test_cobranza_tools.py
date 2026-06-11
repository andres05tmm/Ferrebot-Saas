"""Herramientas de agente del pack Cobranza (`ai/cobranza_tools.py`) contra base efímera real.

Verifica que cada herramienta llama al motor y formatea la salida para el agente, y —lo crítico— el
GUARDARRAÍL de seguridad: el teléfono sale del Contexto del canal (el modelo no puede consultar
deudas ajenas), el número se normaliza por los últimos 10 dígitos ('57300…' del WhatsApp vs '300…'
guardado), `reportar_pago` escala a la bandeja humana, y el catálogo solo se expone con el flag.
"""
from datetime import timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ai.cobranza_tools import CobranzaDeps, ejecutar, exponer_catalogo
from ai.envelope import Contexto, ErrorTool, Resultado
from core.config.timezone import today_co
from core.llm.base import ToolCall
from modules.cobranza.repository import SqlCobranzaRepository
from modules.cobranza.service import CobranzaService
from modules.conversaciones.repository import SqlConversacionRepository
from modules.conversaciones.service import ConversacionService

TEL_A = "3001112233"
TEL_B = "3009998877"
TEL_DESCONOCIDO = "3110000000"


def _deps(s: AsyncSession) -> CobranzaDeps:
    return CobranzaDeps(
        cobranza=CobranzaService(SqlCobranzaRepository(s)),
        conversaciones=ConversacionService(SqlConversacionRepository(s)),
    )


def _ctx(telefono: str | None = TEL_A, *, con_flag: bool = True) -> Contexto:
    capacidades = frozenset({"pack_cobranza"}) if con_flag else frozenset()
    return Contexto(
        tenant_id=1, usuario_id=0, rol="cliente", origen="whatsapp",
        capacidades=capacidades, cliente_telefono=telefono,
    )


def _call(herramienta: str, **arguments) -> ToolCall:
    return ToolCall(id="t", name=herramienta, arguments=arguments)


async def _seed_cliente(
    s: AsyncSession, *, nombre: str, telefono: str, saldo: str = "150000"
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


# --- mi_saldo: acotado al teléfono + normalización -----------------------------
async def test_mi_saldo_es_el_del_que_escribe(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await _seed_cliente(s, nombre="Ana", telefono=TEL_A, saldo="150000")
        await _seed_cliente(s, nombre="Bruno", telefono=TEL_B, saldo="99000")

        r = await ejecutar(_call("mi_saldo"), _ctx(TEL_A), _deps(s))
        assert isinstance(r, Resultado)
        assert r.data["saldo"] == "150000.00" and "Ana" in r.resumen
        assert "99000" not in r.resumen          # jamás la deuda de otro

        # El modelo no puede colar otro teléfono por args: se ignora (no está en el args_model).
        r2 = await ejecutar(_call("mi_saldo", telefono=TEL_B), _ctx(TEL_A), _deps(s))
        assert isinstance(r2, Resultado) and "Ana" in r2.resumen


async def test_mi_saldo_normaliza_prefijo_57_de_whatsapp(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await _seed_cliente(s, nombre="Ana", telefono="300 111 2233")   # como lo guardó el negocio
        r = await ejecutar(_call("mi_saldo"), _ctx(f"57{TEL_A}"), _deps(s))  # como escribe WhatsApp
        assert isinstance(r, Resultado) and "Ana" in r.resumen


async def test_mi_saldo_telefono_desconocido_y_al_dia(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await _seed_cliente(s, nombre="Pago", telefono=TEL_B, saldo="0")

        err = await ejecutar(_call("mi_saldo"), _ctx(TEL_DESCONOCIDO), _deps(s))
        assert isinstance(err, ErrorTool) and err.error == "cliente_no_identificado"

        r = await ejecutar(_call("mi_saldo"), _ctx(TEL_B), _deps(s))
        assert isinstance(r, Resultado) and "al día" in r.resumen


# --- prometer_pago ------------------------------------------------------------
async def test_prometer_pago_valida_fechas(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await _seed_cliente(s, nombre="Ana", telefono=TEL_A)
        deps = _deps(s)

        ayer = (today_co() - timedelta(days=1)).isoformat()
        err = await ejecutar(_call("prometer_pago", fecha=ayer), _ctx(), deps)
        assert isinstance(err, ErrorTool) and err.error == "fecha_invalida" and err.recuperable

        lejos = (today_co() + timedelta(days=45)).isoformat()
        err2 = await ejecutar(_call("prometer_pago", fecha=lejos), _ctx(), deps)
        assert isinstance(err2, ErrorTool) and err2.error == "fecha_invalida"

        manana = (today_co() + timedelta(days=1)).isoformat()
        r = await ejecutar(_call("prometer_pago", fecha=manana), _ctx(), deps)
        assert isinstance(r, Resultado) and r.data["fecha"] == manana


async def test_prometer_pago_reemplaza_la_vigente(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await _seed_cliente(s, nombre="Ana", telefono=TEL_A)
        deps = _deps(s)
        primera = (today_co() + timedelta(days=2)).isoformat()
        segunda = (today_co() + timedelta(days=9)).isoformat()
        await ejecutar(_call("prometer_pago", fecha=primera), _ctx(), deps)
        await ejecutar(_call("prometer_pago", fecha=segunda), _ctx(), deps)
        await s.commit()

        estados = [
            p.estado for p in (
                await s.execute(
                    text("SELECT estado FROM promesas_pago ORDER BY id")
                )
            ).all()
        ]
    assert estados == ["reemplazada", "vigente"]


# --- reportar_pago → bandeja humana -------------------------------------------
async def test_reportar_pago_registra_y_escala_a_humano(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await _seed_cliente(s, nombre="Ana", telefono=TEL_A)
        deps = _deps(s)
        r = await ejecutar(
            _call("reportar_pago", detalle="Transferí por Nequi anoche"), _ctx(), deps
        )
        await s.commit()
        assert isinstance(r, Resultado) and "comprobante" in r.resumen

        pagos = await deps.cobranza.listar_pagos_reportados()
        assert len(pagos) == 1 and pagos[0].nota == "Transferí por Nequi anoche"
        assert not pagos[0].verificado
        # El caso queda en manos humanas: el runtime pausará el agente para este teléfono.
        assert await deps.conversaciones.esta_en_humano(TEL_A)


# --- opt-out --------------------------------------------------------------------
async def test_no_mas_recordatorios_marca_opt_out(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        cliente = await _seed_cliente(s, nombre="Ana", telefono=TEL_A)
        deps = _deps(s)
        r = await ejecutar(_call("no_mas_recordatorios"), _ctx(), deps)
        await s.commit()
        estado = await SqlCobranzaRepository(s).estado_cliente(cliente)

    assert isinstance(r, Resultado) and r.data["opt_out"] is True
    assert estado.opt_out is True


# --- catálogo / gating -----------------------------------------------------------
def test_catalogo_gateado_por_flag():
    assert exponer_catalogo(_ctx(con_flag=False)) == []
    nombres = [spec.name for spec in exponer_catalogo(_ctx())]
    assert nombres == ["mi_saldo", "prometer_pago", "reportar_pago", "no_mas_recordatorios"]


async def test_args_invalidos_y_sin_telefono(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        deps = _deps(s)
        err = await ejecutar(_call("prometer_pago", fecha="no-es-fecha"), _ctx(), deps)
        assert isinstance(err, ErrorTool) and err.error == "validacion" and err.recuperable

        sin_tel = await ejecutar(_call("mi_saldo"), _ctx(telefono=None), deps)
        assert isinstance(sin_tel, ErrorTool) and sin_tel.error == "contexto_invalido"
