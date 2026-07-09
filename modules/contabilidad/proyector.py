"""Proyector: evento operativo → UN asiento contable, idempotente (ADR 0030).

La clave del evento (`venta:{id}`, `gasto:{id}`, …) es la `idempotency_key` del asiento: proyectar el
mismo evento dos veces devuelve el mismo asiento (replay), nunca duplica. El ledger es una capa
DERIVADA: no toca las tablas origen ni el arqueo de caja.

Convenciones de cuenta de contrapartida de cobro/pago:
- venta/devolución/abono en `efectivo` → Caja; `fiado` → Clientes; cualquier otro método → Bancos.
- una compra se asienta contra Proveedores (cuenta por pagar): `compras` no lleva método de pago, así
  que el financiamiento queda en la CxP y se concilia aparte (ver ADR 0030, decisiones no obvias).
- retención en `venta` (nos retienen) → anticipo de impuesto (activo); en `compra` (retenemos) →
  retención por pagar (pasivo). El INC no se asienta en v1 (no incrementa el total cobrado; ADR 0027).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from core.logging import get_logger
from core.money import cuantizar
from modules.contabilidad import puc_seed as puc
from modules.contabilidad.errors import ProyeccionInvalida
from modules.contabilidad.fuente_repository import (
    CompraEvento,
    DevolucionEvento,
    FuenteContableRepository,
    GastoEvento,
    RetencionEvento,
    VentaEvento,
)
from modules.contabilidad.ledger import LedgerService, ResultadoAsiento
from modules.contabilidad.schemas import AsientoCrear, LineaAsiento

log = get_logger("contabilidad.proyector")


def _cuenta_cobro(metodo: str) -> str:
    if metodo == "efectivo":
        return puc.CAJA
    if metodo == "fiado":
        return puc.CLIENTES
    return puc.BANCOS


def _debit(codigo: str, monto: Decimal, desc: str | None = None) -> LineaAsiento:
    return LineaAsiento(cuenta_codigo=codigo, direction="debit", amount=monto, descripcion=desc)


def _credit(codigo: str, monto: Decimal, desc: str | None = None) -> LineaAsiento:
    return LineaAsiento(cuenta_codigo=codigo, direction="credit", amount=monto, descripcion=desc)


@dataclass(frozen=True, slots=True)
class ResumenBackfill:
    """Conteo de asientos creados (nuevos) y omitidos (replay) por tipo de evento."""

    creados: dict[str, int]
    replay: dict[str, int]


class Proyector:
    def __init__(self, ledger: LedgerService, fuente: FuenteContableRepository) -> None:
        self._ledger = ledger
        self._fuente = fuente

    # --- eventos individuales -------------------------------------------------
    async def proyectar_venta(self, venta_id: int) -> ResultadoAsiento:
        ev = await self._fuente.venta(venta_id)
        if ev is None:
            raise ProyeccionInvalida(f"venta {venta_id} inexistente")
        if ev.estado != "completada":
            raise ProyeccionInvalida(f"venta {venta_id} en estado '{ev.estado}': no proyectable")
        if ev.metodo_pago == "mixto" and ev.pagos:
            # Venta MIXTA (F5/0053): el recaudo se reparte entre sus partes reales — la porción
            # efectivo debita CAJA y el resto BANCOS; nada se asienta "como mixto".
            recaudo = [
                _debit(_cuenta_cobro(metodo), monto, f"Recaudo de la venta ({metodo})")
                for metodo, monto in ev.pagos
            ]
        else:
            recaudo = [_debit(_cuenta_cobro(ev.metodo_pago), ev.total, "Recaudo de la venta")]
        lineas = [
            *recaudo,
            _credit(puc.INGRESOS_VENTAS, ev.subtotal, "Ingreso por venta de mercancía"),
        ]
        if ev.impuestos > 0:
            lineas.append(_credit(puc.IVA_GENERADO, ev.impuestos, "IVA generado"))
        if ev.costo > 0:
            lineas.append(_debit(puc.COSTO_VENTAS, cuantizar(ev.costo), "Costo de la mercancía vendida"))
            lineas.append(_credit(puc.INVENTARIO, cuantizar(ev.costo), "Salida de inventario"))
        return await self._registrar(
            ev.fecha, "venta", ev.id, f"venta:{ev.id}", f"Venta {ev.id}", lineas
        )

    async def proyectar_gasto(self, gasto_id: int) -> ResultadoAsiento | None:
        ev = await self._fuente.gasto(gasto_id)
        if ev is None:
            raise ProyeccionInvalida(f"gasto {gasto_id} inexistente")
        if ev.salda_cxp:
            # El pago de la CxP lo asienta el abono a proveedor (evita doble conteo, ADR 0028 D5).
            return None
        cuenta = puc.GASTO_CUENTA_POR_CATEGORIA.get(ev.categoria, puc.GASTO_OTROS)
        lineas = [_debit(cuenta, ev.monto, f"Gasto: {ev.categoria}"), _credit(puc.CAJA, ev.monto)]
        return await self._registrar(
            ev.fecha, "gasto", ev.id, f"gasto:{ev.id}", f"Gasto {ev.id}", lineas
        )

    async def proyectar_abono_fiado(self, mov_id: int) -> ResultadoAsiento:
        ev = await self._fuente.abono_fiado(mov_id)
        if ev is None:
            raise ProyeccionInvalida(f"abono de fiado {mov_id} inexistente")
        lineas = [
            _debit(puc.CAJA, ev.monto, "Recaudo de fiado"),
            _credit(puc.CLIENTES, ev.monto, "Abono a cartera"),
        ]
        return await self._registrar(
            ev.fecha, "abono_fiado", ev.id, f"abono_fiado:{ev.id}", f"Abono de fiado {ev.id}", lineas
        )

    async def proyectar_compra(self, compra_id: int) -> ResultadoAsiento:
        ev = await self._fuente.compra(compra_id)
        if ev is None:
            raise ProyeccionInvalida(f"compra {compra_id} inexistente")
        lineas = [_debit(puc.INVENTARIO, ev.inventario, "Entrada de inventario")]
        if ev.iva > 0:
            lineas.append(_debit(puc.IVA_DESCONTABLE, ev.iva, "IVA descontable"))
        lineas.append(_credit(puc.PROVEEDORES, ev.total, "Cuenta por pagar al proveedor"))
        return await self._registrar(
            ev.fecha, "compra", ev.id, f"compra:{ev.id}", f"Compra {ev.id}", lineas
        )

    async def proyectar_abono_proveedor(self, abono_id: int) -> ResultadoAsiento:
        ev = await self._fuente.abono_proveedor(abono_id)
        if ev is None:
            raise ProyeccionInvalida(f"abono a proveedor {abono_id} inexistente")
        lineas = [
            _debit(puc.PROVEEDORES, ev.monto, "Pago a proveedor"),
            _credit(puc.CAJA, ev.monto),
        ]
        return await self._registrar(
            ev.fecha, "abono_proveedor", ev.id, f"abono_proveedor:{ev.id}",
            f"Abono a proveedor {ev.id}", lineas,
        )

    async def proyectar_devolucion(self, dev_id: int) -> ResultadoAsiento:
        ev = await self._fuente.devolucion(dev_id)
        if ev is None:
            raise ProyeccionInvalida(f"devolución {dev_id} inexistente")
        iva_dev = self._iva_proporcional(ev)
        base_dev = cuantizar(ev.total - iva_dev)
        cobro = puc.CLIENTES if ev.metodo_reintegro == "fiado" else puc.CAJA
        lineas = [_debit(puc.DEVOLUCIONES_VENTAS, base_dev, "Devolución en ventas")]
        if iva_dev > 0:
            lineas.append(_debit(puc.IVA_GENERADO, iva_dev, "Reversa de IVA generado"))
        lineas.append(_credit(cobro, ev.total, "Reintegro de la devolución"))
        if ev.costo > 0:
            lineas.append(_debit(puc.INVENTARIO, cuantizar(ev.costo), "Reingreso de inventario"))
            lineas.append(_credit(puc.COSTO_VENTAS, cuantizar(ev.costo), "Reversa de COGS"))
        return await self._registrar(
            ev.fecha, "devolucion", ev.id, f"devolucion:{ev.id}", f"Devolución {ev.id}", lineas
        )

    async def proyectar_factura_proveedor(self, factura_id: str) -> ResultadoAsiento | None:
        """Factura de proveedor 'suelta' (sin `compra`): débito compras, crédito Proveedores (CxP).

        Sin la factura proyectada, su abono debitaría Proveedores sin un crédito que lo respalde
        (ADR 0030 cabo c). No lleva IVA descontable: `facturas_proveedores` solo trae el total.
        """
        ev = await self._fuente.factura_proveedor(factura_id)
        if ev is None:
            raise ProyeccionInvalida(f"factura de proveedor {factura_id!r} inexistente")
        if ev.total <= 0:
            return None
        lineas = [
            _debit(puc.COMPRAS_PROVEEDOR, ev.total, "Compra a crédito (factura de proveedor)"),
            _credit(puc.PROVEEDORES, ev.total, "Cuenta por pagar al proveedor"),
        ]
        return await self._registrar(
            ev.fecha, "factura_proveedor", None, f"factura_proveedor:{ev.id}",
            f"Factura de proveedor {ev.id}", lineas,
        )

    async def proyectar_retencion(self, retencion_id: int) -> ResultadoAsiento | None:
        ev = await self._fuente.retencion(retencion_id)
        if ev is None:
            raise ProyeccionInvalida(f"retención {retencion_id} inexistente")
        cuentas = puc.RETENCION_CUENTAS.get(ev.tipo)
        if cuentas is None or ev.valor <= 0:
            # INC (u otro) no se asienta en v1: no reduce el pago recibido (ADR 0027 D5).
            return None
        anticipo, por_pagar = cuentas
        if ev.doc_tipo == "venta":
            cobro = _cuenta_cobro(ev.metodo_pago_doc or "efectivo")
            lineas = [
                _debit(anticipo, ev.valor, f"Retención {ev.tipo} practicada por el cliente"),
                _credit(cobro, ev.valor, "Menor recaudo por retención"),
            ]
        else:
            lineas = [
                _debit(puc.PROVEEDORES, ev.valor, "Menor pago por retención practicada"),
                _credit(por_pagar, ev.valor, f"Retención {ev.tipo} por pagar"),
            ]
        return await self._registrar(
            ev.fecha, "retencion", ev.id, f"retencion:{ev.id}", f"Retención {ev.id} ({ev.tipo})", lineas
        )

    # --- backfill (solo hacia adelante desde una fecha) -----------------------
    async def backfill(self, desde: datetime) -> ResumenBackfill:
        """Proyecta todos los eventos con fecha ≥ `desde`. Idempotente (replay no duplica)."""
        creados: dict[str, int] = {}
        replay: dict[str, int] = {}

        async def correr(tipo, tabla, col, proyectar, filtro=""):
            for _id in await self._fuente.ids_desde(tabla, col, desde, filtro):
                res = await proyectar(_id)
                if res is None:
                    continue
                bucket = replay if res.replay else creados
                bucket[tipo] = bucket.get(tipo, 0) + 1

        async def correr_facturas():
            for fid in await self._fuente.ids_facturas_proveedores_desde(desde):
                res = await self.proyectar_factura_proveedor(fid)
                if res is None:
                    continue
                bucket = replay if res.replay else creados
                bucket["factura_proveedor"] = bucket.get("factura_proveedor", 0) + 1

        await correr("compra", "compras", "fecha", self.proyectar_compra)
        await correr_facturas()
        await correr("venta", "ventas", "fecha", self.proyectar_venta, "AND estado='completada'")
        await correr("abono_fiado", "fiados_movimientos", "creado_en", self.proyectar_abono_fiado, "AND tipo='abono'")
        await correr("gasto", "gastos", "creado_en", self.proyectar_gasto)
        await correr("abono_proveedor", "facturas_abonos", "creado_en", self.proyectar_abono_proveedor)
        await correr("devolucion", "devoluciones", "creado_en", self.proyectar_devolucion)
        await correr("retencion", "retenciones_documento", "creado_en", self.proyectar_retencion)
        log.info("backfill_contable", desde=desde.isoformat(), creados=creados, replay=replay)
        return ResumenBackfill(creados=creados, replay=replay)

    # --- helpers --------------------------------------------------------------
    @staticmethod
    def _iva_proporcional(ev: DevolucionEvento) -> Decimal:
        """IVA de la devolución a prorrata del ratio IVA/total de la venta origen (redondeo único)."""
        if ev.venta_total <= 0 or ev.venta_impuestos <= 0:
            return Decimal("0")
        return cuantizar(ev.total * ev.venta_impuestos / ev.venta_total)

    async def _registrar(
        self, fecha, origen_tipo, origen_id, key, descripcion, lineas
    ) -> ResultadoAsiento:
        return await self._ledger.registrar_asiento(
            AsientoCrear(
                fecha=fecha, origen_tipo=origen_tipo, origen_id=origen_id,
                descripcion=descripcion, idempotency_key=key, lineas=lineas,
            )
        )
