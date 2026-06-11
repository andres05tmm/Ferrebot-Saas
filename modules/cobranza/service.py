"""Motor del pack cobranza (ADR 0015): determinista, igual para todos los tenants.

El agente NUNCA calcula saldos ni decide a quién recordar: aquí viven las reglas (cadencia, tope,
ventana horaria, opt-out, promesas) y la lectura del saldo real (`clientes.saldo_fiado`). El envío
del recordatorio (plantilla paga de WhatsApp) se inyecta como callback `enviar` — mismo seam que la
reconfirmación de agenda: solo un envío exitoso sella el dedup.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal

from modules.cobranza.errors import (
    ClienteNoIdentificado,
    FechaPromesaInvalida,
    PagoReportadoInexistente,
    SinDeuda,
)
from modules.cobranza.models import PagoReportado, PromesaPago
from modules.cobranza.repository import SqlCobranzaRepository
from modules.cobranza.schemas import CobranzaConfigActualizar, DeudorLeer

# Tope fijo del horizonte de una promesa de pago (no configurable: una promesa a meses no es promesa).
_MAX_DIAS_PROMESA = 30


@dataclass(frozen=True, slots=True)
class InfoSaldo:
    """Lo que el agente puede decirle al que escribe: SU saldo y SU promesa vigente. Nada más."""

    cliente_id: int
    nombre: str
    saldo: Decimal
    promesa_fecha: date | None


@dataclass(frozen=True, slots=True)
class DeudorRecordatorio:
    """Deudor elegido por el motor para un recordatorio (lo que ve el callback de envío)."""

    cliente_id: int
    nombre: str
    telefono: str
    saldo: Decimal


@dataclass(frozen=True, slots=True)
class ResumenCobranza:
    """Resultado de una corrida del cron: envíos, promesas vencidas y ciclos cerrados."""

    recordatorios: int = 0
    promesas_incumplidas: int = 0
    al_dia: int = 0


# Callback que envía el recordatorio a UN deudor (lo provee el worker con la plantilla de Kapso).
# Devuelve True si el envío fue exitoso (solo entonces se sella el dedup). Inyectable: los tests lo falsean.
EnviarRecordatorio = Callable[[DeudorRecordatorio], Awaitable[bool]]


class CobranzaService:
    def __init__(self, repo: SqlCobranzaRepository) -> None:
        self._repo = repo

    # --- cara al deudor (herramientas del agente, acotadas al teléfono) -------
    async def saldo_de(self, telefono: str) -> InfoSaldo:
        """Saldo y promesa vigente del cliente que escribe. Lanza si el teléfono no es de un cliente."""
        cliente = await self._repo.cliente_por_telefono(telefono)
        if cliente is None:
            raise ClienteNoIdentificado(telefono)
        promesa = await self._repo.promesa_vigente(cliente.id)
        return InfoSaldo(
            cliente_id=cliente.id, nombre=cliente.nombre, saldo=cliente.saldo_fiado,
            promesa_fecha=promesa.fecha_promesa if promesa else None,
        )

    async def prometer_pago(self, telefono: str, fecha: date, *, hoy: date) -> PromesaPago:
        """Registra la promesa de pago del que escribe (futura, ≤ 30 días). Reemplaza la vigente."""
        cliente = await self._repo.cliente_por_telefono(telefono)
        if cliente is None:
            raise ClienteNoIdentificado(telefono)
        if cliente.saldo_fiado <= 0:
            raise SinDeuda(str(cliente.id))
        if fecha <= hoy:
            raise FechaPromesaInvalida("La fecha de pago debe ser futura.")
        if fecha > hoy + timedelta(days=_MAX_DIAS_PROMESA):
            raise FechaPromesaInvalida(
                f"La fecha de pago no puede pasar de {_MAX_DIAS_PROMESA} días."
            )
        return await self._repo.crear_promesa(cliente.id, telefono=telefono, fecha=fecha)

    async def reportar_pago(self, telefono: str, *, nota: str | None = None) -> PagoReportado:
        """Registra el "ya pagué" del que escribe → bandeja por verificar del dashboard."""
        cliente = await self._repo.cliente_por_telefono(telefono)
        if cliente is None:
            raise ClienteNoIdentificado(telefono)
        return await self._repo.crear_pago_reportado(cliente.id, telefono=telefono, nota=nota)

    async def optar_fuera(self, telefono: str) -> None:
        """Opt-out de recordatorios (Habeas Data): el motor deja de escribirle. La deuda no cambia."""
        cliente = await self._repo.cliente_por_telefono(telefono)
        if cliente is None:
            raise ClienteNoIdentificado(telefono)
        await self._repo.marcar_opt_out(cliente.id, True)

    # --- corrida del cron (worker) ---------------------------------------------
    async def procesar_recordatorios(
        self, *, ahora: datetime, enviar: EnviarRecordatorio
    ) -> ResumenCobranza:
        """Una corrida determinista sobre la base del tenant. `ahora` se inyecta (hora Colombia).

        1) Cierre de ciclo: quien ya pagó (saldo 0 con recordatorios abiertos) → contador a 0 y su
           promesa vigente queda `cumplida`. Corre aunque la ventana horaria esté cerrada.
        2) Ventana horaria: fuera de `[hora_inicio, hora_fin)` NO se envía nada.
        3) Por deudor (saldo > mínimo, con teléfono): salta `opt_out`; una promesa vigente no vencida
           PAUSA los recordatorios (la promesa compra silencio); vencida con deuda → `incumplida` y se
           reanuda. Cadencia (`cadencia_dias`) y tope (`max_recordatorios`) acotan los envíos.
        4) Solo un `enviar` exitoso sella el dedup (fallo de red → se reintenta en la próxima corrida).
        """
        config = await self._repo.obtener_config()
        if not config.activo:
            return ResumenCobranza()
        al_dia = await self._repo.cerrar_al_dia()
        if not (config.hora_inicio <= ahora.time() < config.hora_fin):
            return ResumenCobranza(al_dia=al_dia)

        enviados = 0
        incumplidas = 0
        hoy = ahora.date()
        cadencia = timedelta(days=config.cadencia_dias)
        for fila in await self._repo.deudores(saldo_minimo=config.saldo_minimo):
            if not fila["telefono"]:
                continue
            estado = await self._repo.estado_cliente(fila["cliente_id"])
            if estado.opt_out:
                continue
            promesa = await self._repo.promesa_vigente(fila["cliente_id"])
            if promesa is not None:
                if promesa.fecha_promesa >= hoy:
                    continue                      # promesa vigente: silencio hasta que venza
                await self._repo.marcar_promesa(promesa, "incumplida")
                incumplidas += 1
            if estado.recordatorios_enviados >= config.max_recordatorios:
                continue                          # tope del ciclo: lo retoma el negocio (humano)
            if (
                estado.ultimo_recordatorio_en is not None
                and ahora - estado.ultimo_recordatorio_en < cadencia
            ):
                continue                          # cadencia: aún no toca
            deudor = DeudorRecordatorio(
                cliente_id=fila["cliente_id"], nombre=fila["nombre"],
                telefono=fila["telefono"], saldo=fila["saldo"],
            )
            if await enviar(deudor):
                await self._repo.sellar_recordatorio(deudor.cliente_id, cuando=ahora)
                enviados += 1
        return ResumenCobranza(
            recordatorios=enviados, promesas_incumplidas=incumplidas, al_dia=al_dia
        )

    # --- dashboard (página Cartera) ----------------------------------------------
    async def listar_deudores(self) -> list[DeudorLeer]:
        config = await self._repo.obtener_config()
        return [
            DeudorLeer(**fila) for fila in await self._repo.deudores(saldo_minimo=config.saldo_minimo)
        ]

    async def obtener_config(self):
        return await self._repo.obtener_config()

    async def guardar_config(self, datos: CobranzaConfigActualizar):
        return await self._repo.guardar_config(datos)

    async def listar_promesas(self, *, estado: str | None = None) -> list[PromesaPago]:
        return await self._repo.listar_promesas(estado=estado)

    async def listar_pagos_reportados(self, *, solo_pendientes: bool = True) -> list[PagoReportado]:
        return await self._repo.listar_pagos_reportados(solo_pendientes=solo_pendientes)

    async def verificar_pago_reportado(self, pago_id: int) -> PagoReportado:
        pago = await self._repo.pago_reportado_por_id(pago_id)
        if pago is None:
            raise PagoReportadoInexistente(str(pago_id))
        return await self._repo.marcar_pago_verificado(pago)

    async def fijar_opt_out(self, cliente_id: int, valor: bool) -> None:
        await self._repo.marcar_opt_out(cliente_id, valor)
