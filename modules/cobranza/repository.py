"""Repositorio del pack cobranza: único lugar con SQL (regla no negociable #2).

El saldo se LEE de `clientes.saldo_fiado` (contador denormalizado cuyo dual-write atómico vive en
`modules/fiados`); este pack jamás lo escribe. La identidad cliente↔WhatsApp es el teléfono,
comparado por los últimos 10 dígitos (normaliza '300 123 4567' vs '573001234567').
"""
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from modules.clientes.models import Cliente
from modules.cobranza.models import CobranzaCliente, CobranzaConfig, PagoReportado, PromesaPago
from modules.cobranza.schemas import CobranzaConfigActualizar


def _sufijo(telefono: str) -> str:
    """Últimos 10 dígitos del teléfono (o todos si tiene menos): la llave de comparación."""
    digitos = "".join(c for c in telefono if c.isdigit())
    return digitos[-10:]


class SqlCobranzaRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    # --- config (una fila, get-or-create con defaults) ------------------------
    async def obtener_config(self) -> CobranzaConfig:
        config = (await self._s.execute(select(CobranzaConfig).limit(1))).scalar_one_or_none()
        if config is None:
            config = CobranzaConfig()
            self._s.add(config)
            await self._s.flush()
        return config

    async def guardar_config(self, datos: CobranzaConfigActualizar) -> CobranzaConfig:
        config = await self.obtener_config()
        for campo, valor in datos.model_dump().items():
            setattr(config, campo, valor)
        await self._s.flush()
        return config

    # --- identidad por teléfono ------------------------------------------------
    async def cliente_por_telefono(self, telefono: str) -> Cliente | None:
        """Cliente cuyo teléfono coincide por los últimos 10 dígitos. None si no hay match."""
        sufijo = _sufijo(telefono)
        if not sufijo:
            return None
        fila = (
            await self._s.execute(
                text(
                    "SELECT id FROM clientes WHERE telefono IS NOT NULL "
                    "AND right(regexp_replace(telefono, '\\D', '', 'g'), 10) = :suf "
                    "ORDER BY id LIMIT 1"
                ),
                {"suf": sufijo},
            )
        ).first()
        if fila is None:
            return None
        return await self._s.get(Cliente, fila.id)

    # --- estado de cobranza por cliente ----------------------------------------
    async def estado_cliente(self, cliente_id: int) -> CobranzaCliente:
        estado = (
            await self._s.execute(
                select(CobranzaCliente).where(CobranzaCliente.cliente_id == cliente_id)
            )
        ).scalar_one_or_none()
        if estado is None:
            estado = CobranzaCliente(cliente_id=cliente_id)
            self._s.add(estado)
            await self._s.flush()
        return estado

    async def marcar_opt_out(self, cliente_id: int, valor: bool) -> CobranzaCliente:
        estado = await self.estado_cliente(cliente_id)
        estado.opt_out = valor
        await self._s.flush()
        return estado

    async def sellar_recordatorio(self, cliente_id: int, *, cuando: datetime) -> CobranzaCliente:
        """Dedup + tope: sella el envío SOLO tras un envío exitoso (lo decide el motor)."""
        estado = await self.estado_cliente(cliente_id)
        estado.ultimo_recordatorio_en = cuando
        estado.recordatorios_enviados += 1
        await self._s.flush()
        return estado

    async def cerrar_al_dia(self) -> int:
        """Cierra el ciclo de quien ya pagó: contador a 0 y su promesa vigente → `cumplida`.

        Devuelve cuántos clientes quedaron al día. Saldo 0 = la deuda se saldó; el próximo fiado
        arranca un ciclo de recordatorios nuevo.
        """
        ids = [
            fila.cliente_id
            for fila in (
                await self._s.execute(
                    text(
                        "SELECT cc.cliente_id FROM cobranza_clientes cc "
                        "JOIN clientes c ON c.id = cc.cliente_id "
                        "WHERE cc.recordatorios_enviados > 0 AND c.saldo_fiado <= 0"
                    )
                )
            ).all()
        ]
        if not ids:
            return 0
        await self._s.execute(
            update(CobranzaCliente)
            .where(CobranzaCliente.cliente_id.in_(ids))
            .values(recordatorios_enviados=0, ultimo_recordatorio_en=None)
        )
        await self._s.execute(
            update(PromesaPago)
            .where(PromesaPago.cliente_id.in_(ids), PromesaPago.estado == "vigente")
            .values(estado="cumplida")
        )
        await self._s.flush()
        return len(ids)

    # --- promesas ---------------------------------------------------------------
    async def promesa_vigente(self, cliente_id: int) -> PromesaPago | None:
        return (
            await self._s.execute(
                select(PromesaPago)
                .where(PromesaPago.cliente_id == cliente_id, PromesaPago.estado == "vigente")
                .order_by(PromesaPago.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

    async def crear_promesa(self, cliente_id: int, *, telefono: str, fecha) -> PromesaPago:
        """Registra la promesa; la vigente anterior (si la hay) queda `reemplazada`."""
        await self._s.execute(
            update(PromesaPago)
            .where(PromesaPago.cliente_id == cliente_id, PromesaPago.estado == "vigente")
            .values(estado="reemplazada")
        )
        promesa = PromesaPago(cliente_id=cliente_id, telefono=telefono, fecha_promesa=fecha)
        self._s.add(promesa)
        await self._s.flush()
        return promesa

    async def marcar_promesa(self, promesa: PromesaPago, estado: str) -> PromesaPago:
        promesa.estado = estado
        await self._s.flush()
        return promesa

    async def listar_promesas(self, *, estado: str | None = None) -> list[PromesaPago]:
        consulta = select(PromesaPago).order_by(PromesaPago.fecha_promesa)
        if estado is not None:
            consulta = consulta.where(PromesaPago.estado == estado)
        return list((await self._s.execute(consulta)).scalars())

    # --- pagos reportados ---------------------------------------------------------
    async def crear_pago_reportado(
        self, cliente_id: int, *, telefono: str, nota: str | None
    ) -> PagoReportado:
        pago = PagoReportado(cliente_id=cliente_id, telefono=telefono, nota=nota)
        self._s.add(pago)
        await self._s.flush()
        return pago

    async def pago_reportado_por_id(self, pago_id: int) -> PagoReportado | None:
        return await self._s.get(PagoReportado, pago_id)

    async def listar_pagos_reportados(self, *, solo_pendientes: bool = True) -> list[PagoReportado]:
        consulta = select(PagoReportado).order_by(PagoReportado.creado_en.desc())
        if solo_pendientes:
            consulta = consulta.where(PagoReportado.verificado.is_(False))
        return list((await self._s.execute(consulta)).scalars())

    # --- deudores (escaneo del motor y página Cartera) -----------------------------
    async def deudores(self, *, saldo_minimo: Decimal = Decimal("0")) -> list[dict]:
        """Clientes con saldo > `saldo_minimo`, con su estado de cobranza y promesa vigente."""
        filas = (
            await self._s.execute(
                text(
                    "SELECT c.id AS cliente_id, c.nombre, c.telefono, c.saldo_fiado AS saldo, "
                    "       COALESCE(cc.opt_out, false) AS opt_out, "
                    "       COALESCE(cc.recordatorios_enviados, 0) AS recordatorios_enviados, "
                    "       cc.ultimo_recordatorio_en, p.fecha_promesa AS promesa_fecha "
                    "FROM clientes c "
                    "LEFT JOIN cobranza_clientes cc ON cc.cliente_id = c.id "
                    "LEFT JOIN LATERAL ("
                    "    SELECT fecha_promesa FROM promesas_pago "
                    "    WHERE cliente_id = c.id AND estado = 'vigente' "
                    "    ORDER BY id DESC LIMIT 1"
                    ") p ON true "
                    "WHERE c.saldo_fiado > :minimo "
                    "ORDER BY c.saldo_fiado DESC"
                ),
                {"minimo": saldo_minimo},
            )
        ).all()
        return [dict(fila._mapping) for fila in filas]
