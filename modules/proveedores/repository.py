"""Repositorio de cuentas por pagar: único lugar con SQL del módulo (regla no negociable #2).

`pagado`/`pendiente`/`estado` de una factura son DERIVADOS de sus abonos: al registrar un abono se
recalculan (pagado = Σ abonos; pendiente = total − pagado, con clamp a 0; estado = 'pagada' si
pendiente ≤ 0). Decimal en dinero; la sesión del tenant es la transacción.
"""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from core.money import cuantizar
from modules.compras.models import Proveedor
from modules.proveedores.models import AbonoProveedor, FacturaProveedor
from modules.proveedores.schemas import FacturaProveedorLeer, ProveedorLeer


@dataclass(frozen=True, slots=True)
class ResumenDatos:
    total_adeudado: Decimal
    facturas_pendientes: int


class SqlProveedoresRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def listar_proveedores(self) -> list[ProveedorLeer]:
        """Proveedores registrados (id/nombre/nit), ordenados por nombre — para los desplegables."""
        filas = (
            await self._s.execute(select(Proveedor).order_by(Proveedor.nombre))
        ).scalars().all()
        return [ProveedorLeer.model_validate(p) for p in filas]

    async def nombre_proveedor(self, proveedor_id: int) -> str | None:
        return (
            await self._s.execute(select(Proveedor.nombre).where(Proveedor.id == proveedor_id))
        ).scalar_one_or_none()

    async def estado_proveedores(self, *, hoy: date) -> list[dict]:
        """Una fila por proveedor con su deuda, su ritmo de entrega y su última compra.

        Todo en UNA consulta con subconsultas agregadas (nada de N+1): la lista del tab pinta
        directo de aquí. `vencido` cuenta solo las facturas con vencimiento explícito ya pasado —
        una factura sin fecha de vencimiento no se declara vencida por su cuenta.
        """
        filas = (
            await self._s.execute(
                text(
                    """
                    SELECT p.id, p.nombre, p.nit, p.telefono, p.contacto_nombre, p.contacto_telefono,
                           COALESCE(d.pendiente, 0)     AS saldo_pendiente,
                           COALESCE(d.vencido, 0)       AS vencido,
                           COALESCE(d.facturas, 0)      AS facturas_pendientes,
                           COALESCE(pp.en_camino, 0)    AS pedidos_en_camino,
                           pp.lead_time_horas,
                           c.ultima_compra
                      FROM proveedores p
                      LEFT JOIN (
                            SELECT proveedor_id,
                                   SUM(pendiente) AS pendiente,
                                   SUM(CASE WHEN fecha_vencimiento IS NOT NULL
                                             AND fecha_vencimiento < :hoy THEN pendiente ELSE 0 END)
                                       AS vencido,
                                   COUNT(*) AS facturas
                              FROM facturas_proveedores
                             WHERE pendiente > 0 AND proveedor_id IS NOT NULL
                             GROUP BY proveedor_id
                      ) d ON d.proveedor_id = p.id
                      LEFT JOIN (
                            SELECT proveedor_id,
                                   COUNT(*) FILTER (WHERE estado = 'pedido') AS en_camino,
                                   AVG(EXTRACT(EPOCH FROM (fecha_recepcion - fecha_pedido)) / 3600.0)
                                       FILTER (WHERE fecha_recepcion IS NOT NULL) AS lead_time_horas
                              FROM pedidos_proveedor
                             GROUP BY proveedor_id
                      ) pp ON pp.proveedor_id = p.id
                      LEFT JOIN (
                            SELECT proveedor_id, MAX(fecha)::date AS ultima_compra
                              FROM compras WHERE proveedor_id IS NOT NULL GROUP BY proveedor_id
                      ) c ON c.proveedor_id = p.id
                     ORDER BY COALESCE(d.pendiente, 0) DESC, p.nombre
                    """
                ),
                {"hoy": hoy},
            )
        ).all()
        return [dict(f._mapping) for f in filas]

    async def movimientos_cuenta(
        self, proveedor_id: int, *, desde: date, hasta: date
    ) -> tuple[Decimal, list[dict]]:
        """Saldo anterior al rango + facturas y abonos del rango, en orden cronológico.

        El saldo corrido lo arma el servicio (lógica pura); aquí solo el SQL. Los abonos traen el
        medio por el que salió la plata (0068): efectivo del cajón, guardado o banco.
        """
        anterior = (
            await self._s.execute(
                text(
                    """
                    SELECT COALESCE((SELECT SUM(total) FROM facturas_proveedores
                                      WHERE proveedor_id = :p AND fecha < :desde), 0)
                         - COALESCE((SELECT SUM(a.monto) FROM facturas_abonos a
                                       JOIN facturas_proveedores f ON f.id = a.factura_id
                                      WHERE f.proveedor_id = :p AND a.fecha < :desde), 0)
                    """
                ),
                {"p": proveedor_id, "desde": desde},
            )
        ).scalar_one()

        filas = (
            await self._s.execute(
                text(
                    """
                    SELECT f.fecha, 'factura' AS tipo, f.id AS referencia, f.descripcion,
                           f.total AS cargo, 0 AS abono, NULL AS medio
                      FROM facturas_proveedores f
                     WHERE f.proveedor_id = :p AND f.fecha BETWEEN :desde AND :hasta
                    UNION ALL
                    SELECT a.fecha, 'abono', a.factura_id, NULL,
                           0, a.monto, a.origen_fondos
                      FROM facturas_abonos a
                      JOIN facturas_proveedores f ON f.id = a.factura_id
                     WHERE f.proveedor_id = :p AND a.fecha BETWEEN :desde AND :hasta
                     ORDER BY 1, 2 DESC
                    """
                ),
                {"p": proveedor_id, "desde": desde, "hasta": hasta},
            )
        ).all()
        return Decimal(anterior), [dict(f._mapping) for f in filas]

    async def aging_proveedor(self, proveedor_id: int, *, hoy: date) -> dict:
        """Deuda del proveedor por antigüedad (0-30 / 31-60 / 61-90 / 90+), como los contables."""
        fila = (
            await self._s.execute(
                text(
                    """
                    SELECT COALESCE(SUM(pendiente), 0) AS total,
                           COALESCE(SUM(CASE WHEN :hoy - fecha <= 30 THEN pendiente ELSE 0 END), 0) AS d0_30,
                           COALESCE(SUM(CASE WHEN :hoy - fecha BETWEEN 31 AND 60 THEN pendiente ELSE 0 END), 0) AS d31_60,
                           COALESCE(SUM(CASE WHEN :hoy - fecha BETWEEN 61 AND 90 THEN pendiente ELSE 0 END), 0) AS d61_90,
                           COALESCE(SUM(CASE WHEN :hoy - fecha > 90 THEN pendiente ELSE 0 END), 0) AS d90_mas,
                           COALESCE(SUM(CASE WHEN fecha_vencimiento IS NOT NULL AND fecha_vencimiento < :hoy
                                             THEN pendiente ELSE 0 END), 0) AS vencido
                      FROM facturas_proveedores
                     WHERE proveedor_id = :p AND pendiente > 0
                    """
                ),
                {"p": proveedor_id, "hoy": hoy},
            )
        ).one()
        return dict(fila._mapping)

    async def existe(self, factura_id: str) -> bool:
        return (
            await self._s.execute(
                select(FacturaProveedor.id).where(FacturaProveedor.id == factura_id).limit(1)
            )
        ).first() is not None

    async def crear_factura(
        self, *, factura_id: str, proveedor: str, descripcion: str | None,
        total: Decimal, fecha: date, usuario_id: int | None,
        fecha_vencimiento: date | None = None, proveedor_id: int | None = None,
    ) -> FacturaProveedorLeer:
        """INSERT con pagado=0, pendiente=total, estado='pendiente' (montos cuantizados a centavos).

        `fecha_vencimiento` es opcional: NULL deja que el motor de pagar lo derive (sin cambios)."""
        total = cuantizar(total)
        orm = FacturaProveedor(
            id=factura_id, proveedor=proveedor, proveedor_id=proveedor_id,
            descripcion=descripcion, total=total,
            pagado=Decimal("0.00"), pendiente=total, estado="pendiente", fecha=fecha,
            fecha_vencimiento=fecha_vencimiento, usuario_id=usuario_id,
        )
        self._s.add(orm)
        await self._s.flush()
        return FacturaProveedorLeer.model_validate(orm)

    async def obtener(
        self, factura_id: str, *, bloquear: bool = False
    ) -> FacturaProveedorLeer | None:
        """`bloquear=True` toma FOR UPDATE: el check de sobre-abono del servicio debe leer el pendiente
        DENTRO de la sección crítica (dos abonos concurrentes pasarían ambos el check sin el lock)."""
        stmt = select(FacturaProveedor).where(FacturaProveedor.id == factura_id)
        if bloquear:
            stmt = stmt.with_for_update()
        orm = (await self._s.execute(stmt)).scalar_one_or_none()
        return FacturaProveedorLeer.model_validate(orm) if orm is not None else None

    async def mapa_por_ids(self, ids: list[str]) -> dict[str, FacturaProveedorLeer]:
        """Facturas cuyo id ∈ `ids`, indexadas por id (una consulta; evita N+1 al componer recibidas)."""
        if not ids:
            return {}
        filas = (
            await self._s.execute(
                select(FacturaProveedor).where(FacturaProveedor.id.in_(ids))
            )
        ).scalars().all()
        return {f.id: FacturaProveedorLeer.model_validate(f) for f in filas}

    async def registrar_partes_pago(
        self,
        *,
        ref_tipo: str,
        ref_id: int,
        partes,
        caja_movimiento_id: int | None = None,
    ) -> None:
        """Guarda cómo se repartió un pago al proveedor (0068). La parte que salió del cajón queda
        enlazada a su movimiento de caja; las otras son la plata que salió sin pasar por la caja."""
        for parte in partes:
            await self._s.execute(
                text(
                    "INSERT INTO pagos_proveedor (ref_tipo, ref_id, origen, monto, caja_movimiento_id) "
                    "VALUES (:t, :r, :o, :m, :cm)"
                ),
                {
                    "t": ref_tipo, "r": ref_id, "o": parte.origen, "m": parte.monto,
                    "cm": caja_movimiento_id if parte.origen == "caja" else None,
                },
            )
        await self._s.flush()

    async def resolver_proveedor_por_nombre(self, nombre: str) -> int | None:
        """Id del proveedor cuyo nombre casa (sin distinguir mayúsculas ni espacios), o None."""
        return (
            await self._s.execute(
                select(Proveedor.id).where(
                    func.lower(func.btrim(Proveedor.nombre)) == nombre.strip().lower()
                ).limit(1)
            )
        ).scalar_one_or_none()

    async def asignar_proveedor(self, factura_id: str, proveedor_id: int) -> FacturaProveedorLeer:
        """Enlaza una factura huérfana (nombre que no casó) con su proveedor real."""
        orm = (
            await self._s.execute(
                select(FacturaProveedor).where(FacturaProveedor.id == factura_id)
            )
        ).scalar_one()
        orm.proveedor_id = proveedor_id
        await self._s.flush()
        return FacturaProveedorLeer.model_validate(orm)

    async def set_origen_abono(
        self, abono_id: int, *, origen_fondos: str, caja_movimiento_id: int | None
    ) -> None:
        """Deja en el abono de dónde salió la plata y, si fue del cajón, con qué movimiento."""
        await self._s.execute(
            text(
                "UPDATE facturas_abonos SET origen_fondos = :o, caja_movimiento_id = :m "
                "WHERE id = :a"
            ),
            {"o": origen_fondos, "m": caja_movimiento_id, "a": abono_id},
        )
        await self._s.flush()

    async def actualizar_total(self, factura_id: str, *, total: Decimal) -> FacturaProveedorLeer:
        """Cambia el total de la factura y recalcula pendiente/estado desde los abonos ya hechos.

        Lo usa la corrección de una compra a crédito: si la mercancía costó otra cosa, la deuda tiene
        que seguirla. El servicio ya rechazó el caso de abonos por encima del total nuevo."""
        orm = (
            await self._s.execute(
                select(FacturaProveedor).where(FacturaProveedor.id == factura_id).with_for_update()
            )
        ).scalar_one()
        orm.total = cuantizar(total)
        pendiente = cuantizar(orm.total - orm.pagado)
        orm.pendiente = pendiente if pendiente > 0 else Decimal("0.00")
        orm.estado = "pagada" if orm.pendiente <= 0 else "pendiente"
        await self._s.flush()
        return FacturaProveedorLeer.model_validate(orm)

    async def crear_abono_y_recalcular(
        self, *, factura_id: str, monto: Decimal, fecha: date
    ) -> FacturaProveedorLeer:
        """Inserta el abono y recalcula pagado/pendiente/estado de la factura (en la misma tx)."""
        leer, _ = await self.crear_abono_devolver_id(factura_id=factura_id, monto=monto, fecha=fecha)
        return leer

    async def crear_abono_devolver_id(
        self, *, factura_id: str, monto: Decimal, fecha: date
    ) -> tuple[FacturaProveedorLeer, int]:
        """Como `crear_abono_y_recalcular`, pero devuelve también el id del abono creado.

        Lo usa el flujo gasto→CxP (ADR 0028): el gasto guarda ese id (`gastos.abono_proveedor_id`)
        para que sea su ÚNICO abono (candado anti-duplicación). Misma tx: recálculo consistente.
        """
        orm = (
            await self._s.execute(
                select(FacturaProveedor).where(FacturaProveedor.id == factura_id).with_for_update()
            )
        ).scalar_one()
        abono = AbonoProveedor(factura_id=factura_id, monto=monto, fecha=fecha)
        self._s.add(abono)
        await self._s.flush()

        pagado = (
            await self._s.execute(
                select(func.coalesce(func.sum(AbonoProveedor.monto), 0)).where(
                    AbonoProveedor.factura_id == factura_id
                )
            )
        ).scalar_one()
        pagado = cuantizar(Decimal(pagado))
        pendiente = cuantizar(orm.total - pagado)
        if pendiente < 0:
            pendiente = Decimal("0.00")
        orm.pagado = pagado
        orm.pendiente = pendiente
        orm.estado = "pagada" if pendiente <= 0 else "pendiente"
        await self._s.flush()
        return FacturaProveedorLeer.model_validate(orm), abono.id

    async def listar(self, *, estado: str | None = None) -> list[FacturaProveedorLeer]:
        stmt = select(FacturaProveedor)
        if estado is not None:
            stmt = stmt.where(FacturaProveedor.estado == estado)
        stmt = stmt.order_by(FacturaProveedor.fecha.desc(), FacturaProveedor.id.desc())
        filas = (await self._s.execute(stmt)).scalars().all()
        return [FacturaProveedorLeer.model_validate(f) for f in filas]

    async def resumen(self) -> ResumenDatos:
        """Total pendiente (estado != 'pagada') y número de facturas pendientes."""
        total, n = (
            await self._s.execute(
                select(
                    func.coalesce(func.sum(FacturaProveedor.pendiente), 0),
                    func.count(),
                ).where(FacturaProveedor.estado != "pagada")
            )
        ).one()
        return ResumenDatos(total_adeudado=cuantizar(Decimal(total)), facturas_pendientes=int(n))

    async def set_foto(
        self, factura_id: str, *, url: str, nombre: str | None
    ) -> FacturaProveedorLeer | None:
        orm = (
            await self._s.execute(select(FacturaProveedor).where(FacturaProveedor.id == factura_id))
        ).scalar_one_or_none()
        if orm is None:
            return None
        orm.foto_url = url
        orm.foto_nombre = nombre
        await self._s.flush()
        return FacturaProveedorLeer.model_validate(orm)
