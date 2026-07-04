"""Servicio de caja/gastos: lógica de dominio (arqueo, idempotencia, vínculo gasto→caja).

SQL en el repositorio; hora Colombia con now_co(). Las mutaciones serializan con el lock de la
caja abierta (FOR UPDATE) y el chequeo de idempotencia va DENTRO de esa sección crítica.
"""
from dataclasses import dataclass
from decimal import Decimal

from core.config.timezone import now_co, today_co
from modules.caja.arqueo import calcular_arqueo
from modules.caja.errors import CajaNoAbierta
from modules.caja.models import Caja, CajaMovimiento, Gasto
from modules.caja.repository import SqlCajaRepository
from modules.proveedores.errors import (
    AbonoInvalido,
    FacturaProveedorInexistente,
)
from modules.proveedores.repository import SqlProveedoresRepository


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
    def __init__(
        self, repo: SqlCajaRepository, prov_repo: SqlProveedoresRepository | None = None
    ) -> None:
        self._repo = repo
        # Opcional: solo el router de caja lo cablea (misma sesión) para el vínculo gasto→CxP.
        # El canal del bot registra gastos simples y no necesita este seam.
        self._prov = prov_repo

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
        proveedor_id: int | None = None,
        factura_proveedor_id: str | None = None,
    ) -> ResultadoGasto:
        """Registra el gasto (+ su egreso de caja). Si `factura_proveedor_id` viene, salda esa cuenta
        por pagar generando SU único abono (ADR 0028): no se duplica el abono.

        El chequeo de idempotencia va ANTES de crear el abono: un replay devuelve el gasto previo sin
        crear un segundo abono (invariante "no duplicar el abono").
        """
        caja = await self._repo.caja_abierta(usuario_id, lock=True)
        if caja is None:
            raise CajaNoAbierta(usuario_id)
        if idempotency_key:
            previo = await self._repo.gasto_por_key(idempotency_key)
            if previo is not None:
                return ResultadoGasto(previo, replay=True)

        # Validación del vínculo a CxP ANTES de mover nada (defaults seguros: no gasto a medias).
        if factura_proveedor_id is not None:
            if self._prov is None:
                raise RuntimeError("registrar_gasto con vínculo a CxP requiere el repo de proveedores")
            factura = await self._prov.obtener(factura_proveedor_id)
            if factura is None:
                raise FacturaProveedorInexistente(factura_proveedor_id)
            if monto > factura.pendiente:
                raise AbonoInvalido(
                    f"El gasto {monto} excede el pendiente {factura.pendiente} de la factura "
                    f"{factura_proveedor_id!r}"
                )

        # El vínculo a CxP solo lo usa el router de caja (HTTP); el canal del bot registra gastos
        # simples. Pasamos los kwargs de enlace SOLO cuando hay enlace, para no exigirlos del repo
        # en las rutas que no lo tienen.
        enlace = (
            {"proveedor_id": proveedor_id, "factura_proveedor_id": factura_proveedor_id}
            if (proveedor_id is not None or factura_proveedor_id is not None)
            else {}
        )
        gasto = await self._repo.insertar_gasto(
            caja_id=caja.id, usuario_id=usuario_id, categoria=categoria, monto=monto,
            concepto=concepto, idempotency_key=idempotency_key, **enlace,
        )
        if factura_proveedor_id is not None:
            assert self._prov is not None   # validado arriba
            _, abono_id = await self._prov.crear_abono_devolver_id(
                factura_id=factura_proveedor_id, monto=monto, fecha=today_co(),
            )
            await self._repo.set_abono_gasto(gasto, abono_id=abono_id)
        return ResultadoGasto(gasto, replay=False)
