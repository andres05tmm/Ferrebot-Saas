"""Servicio de caja/gastos: lógica de dominio (arqueo, idempotencia, vínculo gasto→caja).

SQL en el repositorio; hora Colombia con now_co(). Las mutaciones serializan con el lock de la
caja abierta (FOR UPDATE) y el chequeo de idempotencia va DENTRO de esa sección crítica.
"""
from dataclasses import dataclass
from decimal import Decimal

from core.config.timezone import now_co, today_co
from modules.caja.arqueo import calcular_arqueo
from modules.caja.errors import CajaNoAbierta, GastoInexistente
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
class ArqueoVivo:
    """Cuadre de la caja abierta AHORA (para el panel de caja): componentes + esperado, sin cierre."""

    caja: Caja
    ventas_efectivo: Decimal
    ingresos: Decimal
    egresos: Decimal
    saldo_esperado: Decimal


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

    async def _abierta(
        self, usuario_id: int, *, modo_empresa: bool, lock: bool = False
    ) -> Caja | None:
        """La caja que aplica a la operación: en modo empresa (`caja_obligatoria`, un cajón físico
        compartido) es LA caja abierta de la empresa sin importar quién la abrió; si no, la del usuario."""
        if modo_empresa:
            return await self._repo.caja_abierta_empresa(lock=lock)
        return await self._repo.caja_abierta(usuario_id, lock=lock)

    async def actual(self, usuario_id: int, *, modo_empresa: bool = False) -> Caja | None:
        return await self._abierta(usuario_id, modo_empresa=modo_empresa)

    async def arqueo(self, usuario_id: int, *, modo_empresa: bool = False) -> ArqueoVivo | None:
        """Cuadre en vivo de la caja abierta del usuario, o None si no hay caja abierta.

        Reusa los MISMOS agregados y la MISMA fórmula del cierre (`calcular_arqueo`): el `saldo_esperado`
        que ve el cajero en el panel es idéntico al que se compara al cerrar. `saldo_contado=0` es un
        placeholder (solo se usa `saldo_esperado`; la diferencia se descarta)."""
        caja = await self._abierta(usuario_id, modo_empresa=modo_empresa)
        if caja is None:
            return None
        agg = await self._repo.agregados(caja, hasta=now_co(), todos_vendedores=modo_empresa)
        esperado = calcular_arqueo(
            saldo_inicial=caja.saldo_inicial, ventas_efectivo=agg.ventas_efectivo,
            ingresos=agg.ingresos, egresos=agg.egresos, saldo_contado=Decimal(0),
        ).saldo_esperado
        return ArqueoVivo(
            caja=caja, ventas_efectivo=agg.ventas_efectivo, ingresos=agg.ingresos,
            egresos=agg.egresos, saldo_esperado=esperado,
        )

    async def abrir(
        self, *, usuario_id: int, saldo_inicial: Decimal, modo_empresa: bool = False
    ) -> ResultadoApertura:
        # En modo empresa el candado anti-carrera es global: si CUALQUIERA ya abrió el cajón, la
        # segunda apertura (otro cajero, doble clic del modal) es replay de la misma caja.
        existente = await self._abierta(usuario_id, modo_empresa=modo_empresa, lock=True)
        if existente is not None:
            return ResultadoApertura(existente, replay=True)   # ya hay una abierta: idempotente
        caja = await self._repo.crear_caja(
            usuario_id=usuario_id, saldo_inicial=saldo_inicial, fecha=now_co()
        )
        return ResultadoApertura(caja, replay=False)

    async def cerrar(
        self, *, usuario_id: int, saldo_contado: Decimal, modo_empresa: bool = False
    ) -> Caja:
        caja = await self._abierta(usuario_id, modo_empresa=modo_empresa, lock=True)
        if caja is None:
            raise CajaNoAbierta(usuario_id)
        fecha_cierre = now_co()
        agg = await self._repo.agregados(caja, hasta=fecha_cierre, todos_vendedores=modo_empresa)
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
        modo_empresa: bool = False,
    ) -> ResultadoMovimiento:
        caja = await self._abierta(usuario_id, modo_empresa=modo_empresa, lock=True)
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
        modo_empresa: bool = False,
    ) -> ResultadoGasto:
        """Registra el gasto (+ su egreso de caja). Si `factura_proveedor_id` viene, salda esa cuenta
        por pagar generando SU único abono (ADR 0028): no se duplica el abono.

        El chequeo de idempotencia va ANTES de crear el abono: un replay devuelve el gasto previo sin
        crear un segundo abono (invariante "no duplicar el abono").

        Los campos del vertical construcción (spec 09) son opcionales: `obra_id`/`maquina_id` imputan el
        gasto (sigue siendo un gasto de caja normal que postea SU egreso — invariante de caja intacto), y
        los metadatos del bot (`origen_registro`/`telegram_*`/`requiere_revision`) alimentan la bandeja
        de revisión.
        """
        caja = await self._abierta(usuario_id, modo_empresa=modo_empresa, lock=True)
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

        gasto = await self._repo.insertar_gasto(
            caja_id=caja.id, usuario_id=usuario_id, categoria=categoria, monto=monto,
            concepto=concepto, idempotency_key=idempotency_key,
            proveedor_id=proveedor_id, factura_proveedor_id=factura_proveedor_id,
            obra_id=obra_id, maquina_id=maquina_id, categoria_gasto=categoria_gasto,
            metodo_pago=metodo_pago, numero_referencia=numero_referencia,
            comprobante_url=comprobante_url, origen_registro=origen_registro,
            telegram_user_id=telegram_user_id, telegram_message_id=telegram_message_id,
            requiere_revision=requiere_revision,
        )
        if factura_proveedor_id is not None:
            assert self._prov is not None   # validado arriba
            _, abono_id = await self._prov.crear_abono_devolver_id(
                factura_id=factura_proveedor_id, monto=monto, fecha=today_co(),
            )
            await self._repo.set_abono_gasto(gasto, abono_id=abono_id)
        return ResultadoGasto(gasto, replay=False)

    async def listar_revision(self, *, limite: int = 100, offset: int = 0) -> list[Gasto]:
        """Bandeja de revisión (spec 09): gastos con `requiere_revision = true` (normalmente del bot con
        baja confianza), más recientes primero. Acotada a la empresa del request por la sesión del tenant."""
        return await self._repo.listar_gastos(
            requiere_revision=True, limite=limite, offset=offset
        )

    async def aprobar_gasto(self, gasto_id: int) -> Gasto:
        """Aprueba un gasto de la bandeja (baja `requiere_revision`). Idempotente: aprobar dos veces deja
        el mismo resultado. Falla con `GastoInexistente` si el id no existe (default seguro)."""
        gasto = await self._repo.obtener_gasto(gasto_id)
        if gasto is None:
            raise GastoInexistente(gasto_id)
        return await self._repo.marcar_revisado(gasto)
