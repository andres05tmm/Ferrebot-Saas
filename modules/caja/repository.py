"""Repositorio de caja/gastos: único lugar con SQL (regla no negociable #2).

Toda mutación de caja pasa por aquí e inserta su `caja_movimientos`; el gasto inserta además su
fila en `gastos` y SU egreso en la misma transacción (brecha §6/§8). Los agregados del arqueo se
calculan desde `caja_movimientos` (egresos ya incluyen los gastos: fuente única, anti-doble-conteo).
Las ventas en efectivo se leen de la tabla `ventas` (saldo_esperado híbrido).
"""
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from core.events import publish
from modules.caja.models import Caja, CajaMovimiento, Gasto


@dataclass(frozen=True, slots=True)
class AgregadosCaja:
    ventas_efectivo: Decimal
    ingresos: Decimal
    egresos: Decimal   # incluye los gastos (cada gasto postea un egreso)


class SqlCajaRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def caja_abierta(self, usuario_id: int, *, lock: bool = False) -> Caja | None:
        stmt = select(Caja).where(Caja.usuario_id == usuario_id, Caja.estado == "abierta")
        if lock:
            stmt = stmt.with_for_update()
        return (await self._s.execute(stmt)).scalar_one_or_none()

    async def crear_caja(self, *, usuario_id: int, saldo_inicial: Decimal, fecha: datetime) -> Caja:
        caja = Caja(
            usuario_id=usuario_id, fecha_apertura=fecha, saldo_inicial=saldo_inicial,
            estado="abierta",
        )
        self._s.add(caja)
        await self._s.flush()
        await publish(self._s, "caja_abierta", {
            "caja_id": caja.id, "usuario_id": usuario_id, "saldo_inicial": str(saldo_inicial),
        })
        return caja

    async def agregados(self, caja: Caja, *, hasta: datetime) -> AgregadosCaja:
        ventas_efectivo = (
            await self._s.execute(
                text(
                    "SELECT COALESCE(SUM(total), 0) FROM ventas "
                    "WHERE vendedor_id = :uid AND metodo_pago = 'efectivo' AND estado = 'completada' "
                    "AND fecha >= :apertura AND fecha <= :hasta"
                ),
                {"uid": caja.usuario_id, "apertura": caja.fecha_apertura, "hasta": hasta},
            )
        ).scalar_one()
        ingresos = await self._suma_movimientos(caja.id, "ingreso")
        egresos = await self._suma_movimientos(caja.id, "egreso")
        return AgregadosCaja(
            ventas_efectivo=Decimal(ventas_efectivo), ingresos=ingresos, egresos=egresos
        )

    async def _suma_movimientos(self, caja_id: int, tipo: str) -> Decimal:
        total = (
            await self._s.execute(
                text(
                    "SELECT COALESCE(SUM(monto), 0) FROM caja_movimientos "
                    "WHERE caja_id = :cid AND tipo = :tipo"
                ),
                {"cid": caja_id, "tipo": tipo},
            )
        ).scalar_one()
        return Decimal(total)

    async def cerrar(
        self,
        caja: Caja,
        *,
        saldo_esperado: Decimal,
        saldo_contado: Decimal,
        diferencia: Decimal,
        fecha_cierre: datetime,
    ) -> Caja:
        caja.saldo_esperado = saldo_esperado
        caja.saldo_contado = saldo_contado
        caja.diferencia = diferencia
        caja.fecha_cierre = fecha_cierre
        caja.estado = "cerrada"
        await self._s.flush()
        await publish(self._s, "caja_cerrada", {
            "caja_id": caja.id, "saldo_esperado": str(saldo_esperado),
            "saldo_contado": str(saldo_contado), "diferencia": str(diferencia),
        })
        return caja

    async def movimiento_por_key(self, idempotency_key: str) -> CajaMovimiento | None:
        return (
            await self._s.execute(
                select(CajaMovimiento).where(CajaMovimiento.idempotency_key == idempotency_key)
            )
        ).scalar_one_or_none()

    async def insertar_movimiento(
        self,
        *,
        caja_id: int,
        tipo: str,
        monto: Decimal,
        concepto: str | None,
        referencia: str | None = None,
        idempotency_key: str | None = None,
    ) -> CajaMovimiento:
        movimiento = CajaMovimiento(
            caja_id=caja_id, tipo=tipo, monto=monto, concepto=concepto,
            referencia=referencia, idempotency_key=idempotency_key,
        )
        self._s.add(movimiento)
        await self._s.flush()
        await publish(self._s, "caja_movimiento", {
            "caja_id": caja_id, "movimiento_id": movimiento.id, "tipo": tipo, "monto": str(monto),
        })
        return movimiento

    async def gasto_por_key(self, idempotency_key: str) -> Gasto | None:
        return (
            await self._s.execute(
                select(Gasto).where(Gasto.idempotency_key == idempotency_key)
            )
        ).scalar_one_or_none()

    async def insertar_gasto(
        self,
        *,
        caja_id: int,
        usuario_id: int,
        categoria: str,
        monto: Decimal,
        concepto: str | None,
        idempotency_key: str | None = None,
        proveedor_id: int | None = None,
        factura_proveedor_id: str | None = None,
        obra_id: int | None = None,
        maquina_id: int | None = None,
        categoria_gasto: str | None = None,
        metodo_pago: str | None = None,
        numero_referencia: str | None = None,
        comprobante_url: str | None = None,
        origen_registro: str | None = None,
        telegram_user_id: str | None = None,
        telegram_message_id: str | None = None,
        requiere_revision: bool | None = None,
    ) -> Gasto:
        """Inserta el gasto y SU egreso de caja en la misma tx (gasto → caja_movimientos).

        `proveedor_id`/`factura_proveedor_id` son el vínculo opcional a CxP (ADR 0028); el abono que
        salda la factura lo enlaza después el servicio con `set_abono_gasto` (un gasto → un abono).

        Los campos del vertical construcción (spec 09) son opcionales. `origen_registro` y
        `requiere_revision` son NOT NULL con server_default (0048): si el caller los pasa como None se
        OMITEN del INSERT para que aplique el default (MANUAL / false), en vez de intentar insertar NULL.
        """
        # obra/máquina/metadatos: nullable, se pasan tal cual (None inserta NULL, que es correcto).
        campos_vertical: dict = {
            "obra_id": obra_id, "maquina_id": maquina_id, "categoria_gasto": categoria_gasto,
            "metodo_pago": metodo_pago, "numero_referencia": numero_referencia,
            "comprobante_url": comprobante_url, "telegram_user_id": telegram_user_id,
            "telegram_message_id": telegram_message_id,
        }
        if origen_registro is not None:
            campos_vertical["origen_registro"] = origen_registro
        if requiere_revision is not None:
            campos_vertical["requiere_revision"] = requiere_revision
        gasto = Gasto(
            categoria=categoria, monto=monto, concepto=concepto,
            caja_id=caja_id, usuario_id=usuario_id, idempotency_key=idempotency_key,
            proveedor_id=proveedor_id, factura_proveedor_id=factura_proveedor_id,
            **campos_vertical,
        )
        self._s.add(gasto)
        await self._s.flush()
        # El egreso NO lleva idempotency_key: el ancla idempotente es la fila `gastos`.
        self._s.add(CajaMovimiento(
            caja_id=caja_id, tipo="egreso", monto=monto,
            concepto=concepto or f"gasto:{categoria}", referencia=f"gasto:{gasto.id}",
        ))
        await self._s.flush()
        await publish(self._s, "gasto_registrado", {
            "gasto_id": gasto.id, "caja_id": caja_id, "categoria": categoria, "monto": str(monto),
        })
        return gasto

    async def set_abono_gasto(self, gasto: Gasto, *, abono_id: int) -> Gasto:
        """Enlaza el abono que ESTE gasto generó (candado anti-duplicación gasto→CxP)."""
        gasto.abono_proveedor_id = abono_id
        await self._s.flush()
        return gasto

    async def listar_gastos(
        self, *, desde: datetime | None = None, hasta: datetime | None = None,
        limite: int = 100, offset: int = 0,
        requiere_revision: bool | None = None, obra_id: int | None = None,
        origen_registro: str | None = None,
    ) -> list[Gasto]:
        stmt = select(Gasto)
        if desde is not None:
            stmt = stmt.where(Gasto.creado_en >= desde)
        if hasta is not None:
            stmt = stmt.where(Gasto.creado_en <= hasta)
        # Filtros del vertical construcción (spec 09): bandeja de revisión, imputación a obra, origen.
        if requiere_revision is not None:
            stmt = stmt.where(Gasto.requiere_revision == requiere_revision)
        if obra_id is not None:
            stmt = stmt.where(Gasto.obra_id == obra_id)
        if origen_registro is not None:
            stmt = stmt.where(Gasto.origen_registro == origen_registro)
        stmt = stmt.order_by(Gasto.creado_en.desc(), Gasto.id.desc()).limit(limite).offset(offset)
        return list((await self._s.execute(stmt)).scalars().all())

    async def obtener_gasto(self, gasto_id: int) -> Gasto | None:
        return (
            await self._s.execute(select(Gasto).where(Gasto.id == gasto_id))
        ).scalar_one_or_none()

    async def marcar_revisado(self, gasto: Gasto) -> Gasto:
        """Baja el flag de revisión (aprobar en la bandeja). Idempotente: aprobar un gasto ya revisado
        lo deja igual (no hay estado que revertir)."""
        gasto.requiere_revision = False
        await self._s.flush()
        return gasto

    # ---- Agregados del cockpit de construcción (Fase 2 del dashboard) ------------------------------
    async def suma_gastos(self, *, inicio: datetime, fin: datetime) -> Decimal:
        """Σ `monto` de los gastos del mes (ventana sobre `creado_en`, TIMESTAMPTZ). KPI de gasto del mes."""
        total = (
            await self._s.execute(
                select(func.coalesce(func.sum(Gasto.monto), 0)).where(
                    Gasto.creado_en >= inicio, Gasto.creado_en <= fin
                )
            )
        ).scalar_one()
        return Decimal(total)

    async def contar_gastos_por_revisar(self) -> int:
        """Cuántos gastos siguen pendientes de revisión (bandeja del bot). Badge del dashboard: es el
        pendiente VIVO (no acotado al mes), un to-do que el dueño debe despachar."""
        n = (
            await self._s.execute(
                select(func.count()).select_from(Gasto).where(Gasto.requiere_revision.is_(True))
            )
        ).scalar_one()
        return int(n)
