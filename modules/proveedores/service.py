"""Servicio de cuentas por pagar: validación de dominio sobre el repositorio (sin SQL).

Reglas: id de factura único (409); el abono debe existir la factura (404), ser > 0 y no exceder el
pendiente (422). La fecha por defecto es hoy en hora Colombia (regla #4). El recálculo del saldo lo
hace el repositorio en la misma transacción.
"""
from datetime import date, timedelta
from decimal import Decimal

from core.config.timezone import today_co
from core.money import cuantizar
from modules.proveedores.errors import (
    AbonoInvalido,
    ProveedorDuplicado,
    ProveedorInexistente,
    FacturaProveedorDuplicada,
    FacturaProveedorInexistente,
)
from modules.proveedores.pagos import etiqueta_origen, monto_de_caja, normalizar_partes
from modules.proveedores.repository import SqlProveedoresRepository
from modules.proveedores.schemas import (
    AbonoCrear,
    EstadoCuentaProveedor,
    MovimientoCuenta,
    ProveedorEstado,
    ProveedorGuardar,
    FacturaProveedorCrear,
    FacturaProveedorLeer,
    ProveedorLeer,
    ResumenCxP,
)


# Ventana por defecto del estado de cuenta: 6 meses (decisión del dueño). Más atrás se consulta con
# un rango explícito — el botón de PDF del tab lo usa para llevarse todo el histórico.
_VENTANA_ESTADO_CUENTA_DIAS = 183


class ProveedoresService:
    def __init__(self, repo: SqlProveedoresRepository, *, caja=None) -> None:
        self._repo = repo
        # CajaService atado a la MISMA sesión (lo cablea el router): un abono pagado con el efectivo
        # del cajón tiene que dejar su egreso, o el arqueo queda descuadrado.
        self._caja = caja

    async def listar_proveedores(self) -> list[ProveedorLeer]:
        """Lista de proveedores registrados (id/nombre/nit) para los desplegables del modal."""
        return await self._repo.listar_proveedores()

    async def crear_proveedor(self, datos: ProveedorGuardar) -> ProveedorLeer:
        """Da de alta un proveedor. Nombre repetido → 409: dos fichas del mismo partirían su deuda."""
        if await self._repo.resolver_proveedor_por_nombre(datos.nombre) is not None:
            raise ProveedorDuplicado(datos.nombre)
        return await self._repo.crear_proveedor(datos)

    async def actualizar_proveedor(
        self, proveedor_id: int, datos: ProveedorGuardar
    ) -> ProveedorLeer:
        """Corrige los datos del proveedor. Renombrarlo hacia un nombre ya usado → 409."""
        otro = await self._repo.resolver_proveedor_por_nombre(datos.nombre)
        if otro is not None and otro != proveedor_id:
            raise ProveedorDuplicado(datos.nombre)
        actualizado = await self._repo.actualizar_proveedor(proveedor_id, datos)
        if actualizado is None:
            raise ProveedorInexistente(proveedor_id)
        return actualizado

    async def estado_proveedores(self) -> list[ProveedorEstado]:
        """Cómo va cada proveedor: cuánto se le debe, cuánto está vencido, qué viene en camino y
        cuánto suele tardar. Es la lista del tab de proveedores."""
        filas = await self._repo.estado_proveedores(hoy=today_co())
        return [
            ProveedorEstado(
                **{k: v for k, v in f.items() if k != "lead_time_horas"},
                lead_time_promedio_horas=(
                    round(float(f["lead_time_horas"]), 2) if f["lead_time_horas"] is not None else None
                ),
            )
            for f in filas
        ]

    async def estado_cuenta(
        self, proveedor_id: int, *, desde: date | None, hasta: date | None
    ) -> EstadoCuentaProveedor:
        """Estado de cuenta del proveedor: saldo, antigüedad y el movimiento a movimiento con SALDO
        CORRIDO (cada línea muestra cómo quedó la deuda después de ella).

        Default: los últimos 6 meses (lo que se consulta a diario); con un rango explícito se puede
        ir tan atrás como haga falta — el PDF del tab usa justamente eso.
        """
        hoy = today_co()
        hasta = hasta or hoy
        desde = desde or (hasta - timedelta(days=_VENTANA_ESTADO_CUENTA_DIAS))
        nombre = await self._repo.nombre_proveedor(proveedor_id)
        if nombre is None:
            raise ProveedorInexistente(proveedor_id)

        aging = await self._repo.aging_proveedor(proveedor_id, hoy=hoy)
        saldo_anterior, filas = await self._repo.movimientos_cuenta(
            proveedor_id, desde=desde, hasta=hasta
        )

        saldo_anterior = Decimal(str(saldo_anterior or 0))
        saldo = cuantizar(saldo_anterior)
        movimientos = []
        for f in filas:
            cargo = cuantizar(Decimal(str(f["cargo"] or 0)))
            abono = cuantizar(Decimal(str(f["abono"] or 0)))
            saldo = cuantizar(saldo + cargo - abono)
            movimientos.append(MovimientoCuenta(
                fecha=f["fecha"], tipo=f["tipo"], referencia=f["referencia"],
                descripcion=f["descripcion"], cargo=cargo, abono=abono, saldo=saldo,
                medio=f["medio"],
            ))

        return EstadoCuentaProveedor(
            proveedor_id=proveedor_id, proveedor_nombre=nombre, desde=desde, hasta=hasta,
            saldo_pendiente=cuantizar(Decimal(str(aging["total"]))),
            vencido=cuantizar(Decimal(str(aging["vencido"]))),
            saldo_anterior=cuantizar(saldo_anterior),
            aging={
                tramo: cuantizar(Decimal(str(aging[col])))
                for tramo, col in (
                    ("0-30", "d0_30"), ("31-60", "d31_60"), ("61-90", "d61_90"), ("90+", "d90_mas")
                )
            },
            movimientos=movimientos,
        )

    async def crear_factura(
        self, datos: FacturaProveedorCrear, *, usuario_id: int | None
    ) -> FacturaProveedorLeer:
        """Da de alta la deuda (pendiente=total). Si el id ya existe → FacturaProveedorDuplicada."""
        if await self._repo.existe(datos.id):
            raise FacturaProveedorDuplicada(datos.id)
        # La deuda queda ligada al proveedor (0070): el que venga en el payload o el que case por
        # nombre. Sin coincidencia queda NULL y el dashboard la muestra para asignarla a mano.
        proveedor_id = datos.proveedor_id or await self._repo.resolver_proveedor_por_nombre(
            datos.proveedor
        )
        return await self._repo.crear_factura(
            factura_id=datos.id, proveedor=datos.proveedor, proveedor_id=proveedor_id,
            descripcion=datos.descripcion,
            total=datos.total, fecha=datos.fecha or today_co(),
            fecha_vencimiento=datos.fecha_vencimiento, usuario_id=usuario_id,
        )

    async def registrar_abono(
        self, datos: AbonoCrear, *, usuario_id: int | None = None, modo_empresa: bool = False
    ) -> FacturaProveedorLeer:
        """Registra el abono y devuelve la factura con el saldo recalculado.

        404 si la factura no existe; 422 si el monto excede el pendiente (criterio: no sobre-abonar).
        El check lee el pendiente BAJO LOCK (FOR UPDATE): dos abonos concurrentes se serializan y el
        segundo ve el pendiente ya recalculado (sin el lock ambos pasarían y se sobre-abonaría).
        """
        factura = await self._repo.obtener(datos.factura_id, bloquear=True)
        if factura is None:
            raise FacturaProveedorInexistente(datos.factura_id)
        if datos.monto > factura.pendiente:
            raise AbonoInvalido(
                f"El abono {datos.monto} excede el pendiente {factura.pendiente} de la factura {datos.factura_id!r}"
            )
        leer, abono_id = await self._repo.crear_abono_devolver_id(
            factura_id=datos.factura_id, monto=datos.monto, fecha=datos.fecha or today_co(),
        )
        # Cómo se reparte el abono entre medios (puede ser mixto): solo la parte del cajón mueve
        # la caja; el resto es plata que salió del negocio sin pasar por ella.
        partes = normalizar_partes(datos.pagos, datos.origen_fondos, datos.monto)
        desde_caja = monto_de_caja(partes)
        movimiento_id = None
        if desde_caja > 0:
            if self._caja is None:
                raise RuntimeError("un abono desde caja requiere el servicio de caja cableado")
            res = await self._caja.registrar_movimiento(
                usuario_id=usuario_id, tipo="egreso", monto=desde_caja,
                concepto=f"Abono factura {datos.factura_id}",
                referencia=f"abono:{abono_id}",
                idempotency_key=f"abono:{abono_id}", modo_empresa=modo_empresa,
            )
            movimiento_id = res.movimiento.id
        await self._repo.registrar_partes_pago(
            ref_tipo="abono", ref_id=abono_id, partes=partes, caja_movimiento_id=movimiento_id,
        )
        await self._repo.set_origen_abono(
            abono_id, origen_fondos=etiqueta_origen(partes), caja_movimiento_id=movimiento_id
        )
        return leer

    async def asignar_proveedor(self, factura_id: str, proveedor_id: int) -> FacturaProveedorLeer:
        if await self._repo.obtener(factura_id) is None:
            raise FacturaProveedorInexistente(factura_id)
        return await self._repo.asignar_proveedor(factura_id, proveedor_id)

    async def listar(self, *, estado: str | None) -> list[FacturaProveedorLeer]:
        return await self._repo.listar(estado=estado)

    async def resumen(self) -> ResumenCxP:
        datos = await self._repo.resumen()
        return ResumenCxP(
            total_adeudado=datos.total_adeudado, facturas_pendientes=datos.facturas_pendientes
        )

    async def guardar_foto(
        self, factura_id: str, *, url: str, nombre: str | None
    ) -> FacturaProveedorLeer:
        """Persiste la URL de la foto subida a Cloudinary. 404 si la factura no existe."""
        factura = await self._repo.set_foto(factura_id, url=url, nombre=nombre)
        if factura is None:
            raise FacturaProveedorInexistente(factura_id)
        return factura
