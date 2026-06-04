"""Servicio de caja/gastos: lógica de dominio (arqueo, idempotencia, vínculo gasto→caja).

SQL en el repositorio; hora Colombia con now_co(). Las mutaciones serializan con el lock de la
caja abierta (FOR UPDATE) y el chequeo de idempotencia va DENTRO de esa sección crítica.
"""
from dataclasses import dataclass
from decimal import Decimal

from core.config.timezone import now_co
from modules.caja.arqueo import calcular_arqueo
from modules.caja.errors import CajaNoAbierta
from modules.caja.models import Caja, CajaMovimiento, Gasto
from modules.caja.repository import SqlCajaRepository


@dataclass(frozen=True, slots=True)
class ResultadoApertura:
    caja: Caja
    replay: bool


@dataclass(frozen=True, slots=True)
class ResultadoMovimiento:
    movimiento: CajaMovimiento
    replay: bool


@dataclass(frozen=True, slots=True)
class ResultadoGasto:
    gasto: Gasto
    replay: bool


class CajaService:
    def __init__(self, repo: SqlCajaRepository) -> None:
        self._repo = repo

    async def actual(self, usuario_id: int) -> Caja | None:
        return await self._repo.caja_abierta(usuario_id)

    async def abrir(self, *, usuario_id: int, saldo_inicial: Decimal) -> ResultadoApertura:
        existente = await self._repo.caja_abierta(usuario_id, lock=True)
        if existente is not None:
            return ResultadoApertura(existente, replay=True)   # ya hay una abierta: idempotente
        caja = await self._repo.crear_caja(
            usuario_id=usuario_id, saldo_inicial=saldo_inicial, fecha=now_co()
        )
        return ResultadoApertura(caja, replay=False)

    async def cerrar(self, *, usuario_id: int, saldo_contado: Decimal) -> Caja:
        caja = await self._repo.caja_abierta(usuario_id, lock=True)
        if caja is None:
            raise CajaNoAbierta(usuario_id)
        fecha_cierre = now_co()
        agg = await self._repo.agregados(caja, hasta=fecha_cierre)
        arqueo = calcular_arqueo(
            saldo_inicial=caja.saldo_inicial,
            ventas_efectivo=agg.ventas_efectivo,
            ingresos=agg.ingresos,
            egresos=agg.egresos,          # ya incluye los gastos: fuente única caja_movimientos
            saldo_contado=saldo_contado,
        )
        return await self._repo.cerrar(
            caja, saldo_esperado=arqueo.saldo_esperado, saldo_contado=saldo_contado,
            diferencia=arqueo.diferencia, fecha_cierre=fecha_cierre,
        )

    async def registrar_movimiento(
        self,
        *,
        usuario_id: int,
        tipo: str,
        monto: Decimal,
        concepto: str | None,
        idempotency_key: str | None = None,
    ) -> ResultadoMovimiento:
        caja = await self._repo.caja_abierta(usuario_id, lock=True)
        if caja is None:
            raise CajaNoAbierta(usuario_id)
        if idempotency_key:
            previo = await self._repo.movimiento_por_key(idempotency_key)
            if previo is not None:
                return ResultadoMovimiento(previo, replay=True)
        movimiento = await self._repo.insertar_movimiento(
            caja_id=caja.id, tipo=tipo, monto=monto, concepto=concepto,
            idempotency_key=idempotency_key,
        )
        return ResultadoMovimiento(movimiento, replay=False)

    async def registrar_gasto(
        self,
        *,
        usuario_id: int,
        categoria: str,
        monto: Decimal,
        concepto: str | None,
        idempotency_key: str | None = None,
    ) -> ResultadoGasto:
        caja = await self._repo.caja_abierta(usuario_id, lock=True)
        if caja is None:
            raise CajaNoAbierta(usuario_id)
        if idempotency_key:
            previo = await self._repo.gasto_por_key(idempotency_key)
            if previo is not None:
                return ResultadoGasto(previo, replay=True)
        gasto = await self._repo.insertar_gasto(
            caja_id=caja.id, usuario_id=usuario_id, categoria=categoria, monto=monto,
            concepto=concepto, idempotency_key=idempotency_key,
        )
        return ResultadoGasto(gasto, replay=False)
