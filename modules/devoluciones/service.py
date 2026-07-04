"""Servicio de devoluciones: orquesta stock + dinero + nota crédito en UNA transacción (ADR 0026).

Invariantes críticos (regla #7/#8): la devolución SIEMPRE mueve stock (movimiento DEVOLUCION al costo
del snapshot original) y SIEMPRE su contrapartida de dinero (egreso de caja si fue efectivo, abono al
fiado si fue a crédito) — nunca una sin la otra. La contrapartida se valida ANTES de tocar el stock
(caja abierta / fiado existente): si falla, la transacción entera se revierte y no persiste nada.
Idempotente por `idempotency_key` (misma key + mismo payload → replay; payload distinto → 409, FF-1).
"""
from dataclasses import dataclass
from decimal import Decimal

from core.money import cuantizar
from modules.caja.repository import SqlCajaRepository
from modules.devoluciones.errors import (
    CajaRequerida,
    DevolucionConflicto,
    DevolucionExcedeVenta,
    FiadoNoEncontrado,
    LineaNoVendida,
    NadaPorDevolver,
    VentaNoEncontrada,
)
from modules.devoluciones.models import Devolucion
from modules.devoluciones.repository import (
    LineaResueltaDev,
    LineaVendida,
    SqlDevolucionesRepository,
)
from modules.devoluciones.schemas import DevolucionCrear
from modules.facturacion.notas import NotasService
from modules.fiados.service import FiadosService


@dataclass(frozen=True, slots=True)
class ResultadoDevolucion:
    devolucion: Devolucion
    replay: bool  # True si se devolvió una devolución ya existente (idempotencia)


def _firma_detalle(dev: Devolucion) -> list[tuple]:
    return sorted(
        (str(d.producto_id), str(Decimal(d.cantidad).normalize())) for d in dev.detalles
    )


class DevolucionesService:
    def __init__(
        self,
        repo: SqlDevolucionesRepository,
        *,
        caja: SqlCajaRepository,
        fiados: FiadosService,
        notas: NotasService | None = None,
    ) -> None:
        self._repo = repo
        self._caja = caja
        self._fiados = fiados
        self._notas = notas

    async def devolver(self, datos: DevolucionCrear, *, usuario_id: int) -> ResultadoDevolucion:
        # 1) Idempotencia estricta (FF-1): misma key + mismo payload → replay; payload distinto → 409.
        if datos.idempotency_key:
            prev = await self._repo.buscar_por_idempotency(datos.idempotency_key)
            if prev is not None:
                if not self._mismo_payload(prev, datos):
                    raise DevolucionConflicto(datos.idempotency_key)
                return ResultadoDevolucion(prev, replay=True)

        # 2) Venta origen.
        venta = await self._repo.cabecera_venta(datos.venta_id)
        if venta is None:
            raise VentaNoEncontrada(datos.venta_id)

        # 3) Resolver líneas devueltas (total o parcial) + total del reintegro. El acumulado de
        # devoluciones previas acota lo devolvible (anti sobre-devolución con keys distintas).
        vendidas = await self._repo.lineas_vendidas(datos.venta_id)
        devuelto = await self._repo.devuelto_por_venta(datos.venta_id)
        lineas = self._resolver(vendidas, datos, devuelto)
        total = cuantizar(sum((ln.total_linea for ln in lineas), Decimal("0")))
        metodo = "fiado" if venta.metodo_pago == "fiado" else "efectivo"

        # 4) Contrapartida VALIDADA antes de tocar stock (nada mueve stock sin contrapartida).
        caja_abierta = None
        fiado = None
        if metodo == "efectivo":
            caja_abierta = await self._caja.caja_abierta(usuario_id, lock=True)
            if caja_abierta is None:
                raise CajaRequerida(usuario_id)
        else:
            fiado = await self._repo.fiado_de_venta(datos.venta_id)
            if fiado is None:
                raise FiadoNoEncontrado(datos.venta_id)

        factura_id = await self._repo.factura_aceptada_de_venta(datos.venta_id)

        # 5) Cabecera + detalle (ancla de idempotencia).
        dev = await self._repo.crear_devolucion(
            venta_id=venta.id, total=total, metodo_reintegro=metodo, motivo=datos.motivo,
            usuario_id=usuario_id, idempotency_key=datos.idempotency_key, lineas=lineas,
        )

        # 6) Stock: movimiento DEVOLUCION al costo snapshot + restaura inventario.
        await self._repo.reingresar_stock(dev.id, lineas, usuario_id)

        # 7) Dinero: egreso de caja (efectivo) o abono al fiado (crédito).
        if metodo == "efectivo":
            await self._caja.insertar_movimiento(
                caja_id=caja_abierta.id, tipo="egreso", monto=total,
                concepto=f"Devolución venta {venta.id}", referencia=f"devolucion:{dev.id}",
            )
        else:
            saldo = fiado.saldo or Decimal("0")
            monto_abono = min(total, saldo)   # no sobre-abonar si ya había pagos parciales
            if monto_abono > 0:
                await self._fiados.abonar(
                    fiado_id=fiado.id, monto=monto_abono,
                    idempotency_key=f"devolucion-fiado:{dev.id}",
                )

        # 8) Nota crédito si la venta fue transmitida a DIAN (vía obligatoria, no borrado físico).
        if factura_id is not None and self._notas is not None:
            nota = await self._notas.emitir_nota_credito(
                venta_id=venta.id, factura_id=factura_id, total=total, motivo=datos.motivo,
                idempotency_key=f"devolucion-nc:{dev.id}",
            )
            await self._repo.vincular_nota(dev.id, nota.id)

        await self._repo.emitir_evento(dev)
        return ResultadoDevolucion(dev, replay=False)

    def _resolver(
        self, vendidas: list[LineaVendida], datos: DevolucionCrear,
        devuelto: dict[int | None, Decimal],
    ) -> list[LineaResueltaDev]:
        """Resuelve las líneas devolvibles acotadas por el acumulado de devoluciones previas.

        Total (`lineas=None`) → el REMANENTE de cada línea (vendido − ya devuelto); si no queda nada
        → NadaPorDevolver. Una línea varia (sin producto_id) solo entra en la primera devolución (no
        hay cómo rastrearla individualmente después). Parcial → solo catálogo; las cantidades pedidas
        se AGREGAN por producto (dos líneas del mismo producto en el payload suman) y
        pedido + ya devuelto ≤ vendido, o DevolucionExcedeVenta."""
        if datos.lineas is None:
            resueltas: list[LineaResueltaDev] = []
            for v in vendidas:
                if v.producto_id is None:
                    if not devuelto:   # varia: solo en la PRIMERA devolución de la venta
                        resueltas.append(self._linea(v, v.cantidad))
                    continue
                remanente = v.cantidad - devuelto.get(v.producto_id, Decimal("0"))
                if remanente > 0:
                    resueltas.append(self._linea(v, remanente))
            if not resueltas:
                raise NadaPorDevolver(datos.venta_id)
            return resueltas

        por_producto = {v.producto_id: v for v in vendidas if v.producto_id is not None}
        pedido_por_producto: dict[int, Decimal] = {}
        for pedida in datos.lineas:
            pedido_por_producto[pedida.producto_id] = (
                pedido_por_producto.get(pedida.producto_id, Decimal("0")) + pedida.cantidad
            )
        resueltas = []
        for producto_id, cantidad in pedido_por_producto.items():
            vendida = por_producto.get(producto_id)
            if vendida is None:
                raise LineaNoVendida(producto_id)
            if cantidad + devuelto.get(producto_id, Decimal("0")) > vendida.cantidad:
                raise DevolucionExcedeVenta(producto_id)
            resueltas.append(self._linea(vendida, cantidad))
        return resueltas

    @staticmethod
    def _linea(vendida: LineaVendida, cantidad: Decimal) -> LineaResueltaDev:
        return LineaResueltaDev(
            producto_id=vendida.producto_id, descripcion=vendida.descripcion, cantidad=cantidad,
            precio_unitario=vendida.precio_unitario, costo_unitario=vendida.costo_unitario,
            total_linea=cuantizar(vendida.precio_unitario * cantidad),
        )

    def _mismo_payload(self, prev: Devolucion, datos: DevolucionCrear) -> bool:
        """¿El payload entrante coincide con la devolución ya registrada bajo la misma key?

        Compara venta y la firma de líneas. Reusar una key con otro payload es un bug del caller → 409.
        Para una devolución TOTAL la firma se compara contra el detalle persistido (que ya materializó
        todas las líneas vendidas)."""
        if prev.venta_id != datos.venta_id:
            return False
        if datos.lineas is None:
            return True  # total: el detalle persistido ES el total; la venta ya coincidió
        agregado: dict[int, Decimal] = {}
        for ln in datos.lineas:   # misma agregación por producto que `_resolver`
            agregado[ln.producto_id] = agregado.get(ln.producto_id, Decimal("0")) + ln.cantidad
        firma_in = sorted((str(pid), str(cant.normalize())) for pid, cant in agregado.items())
        return firma_in == _firma_detalle(prev)
