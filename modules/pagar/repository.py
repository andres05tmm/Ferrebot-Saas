"""Repositorio del pack pagar: único lugar con SQL del módulo (regla no negociable #2).

Solo LEE `facturas_proveedores` (nunca toca `pendiente`/`pagado`/`estado`: eso es del flujo de abonos
de `modules/proveedores`, con su recálculo). Escribe únicamente el plano de avisos (`pagar_config`,
`pagar_avisos`). La sesión del tenant es la transacción y la frontera del aislamiento.
"""
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from modules.pagar.models import PagarAviso, PagarConfig
from modules.pagar.schemas import PagarConfigActualizar
from modules.proveedores.models import FacturaProveedor


@dataclass(frozen=True, slots=True)
class FacturaPendiente:
    """Cuenta por pagar con saldo + su estado de aviso (lo que el motor clasifica)."""

    factura_id: str
    proveedor: str
    pendiente: Decimal
    fecha: date
    fecha_vencimiento: date | None
    avisos_enviados: int
    ultimo_aviso_en: datetime | None


class SqlPagarRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    # --- config (una fila, get-or-create con defaults) ------------------------
    async def obtener_config(self) -> PagarConfig:
        config = (await self._s.execute(select(PagarConfig).limit(1))).scalar_one_or_none()
        if config is None:
            config = PagarConfig()
            self._s.add(config)
            await self._s.flush()
        return config

    async def guardar_config(self, datos: PagarConfigActualizar) -> PagarConfig:
        config = await self.obtener_config()
        for campo, valor in datos.model_dump().items():
            setattr(config, campo, valor)
        await self._s.flush()
        return config

    # --- escaneo del motor: cuentas con saldo + su estado de aviso ------------
    async def facturas_pendientes(self) -> list[FacturaPendiente]:
        """Facturas con `pendiente > 0` y su estado de aviso (LEFT JOIN), ordenadas por vencimiento.

        Solo lectura: el saldo (`pendiente`) lo mantiene `modules/proveedores`; aquí no se escribe.
        """
        filas = (
            await self._s.execute(
                select(
                    FacturaProveedor.id,
                    FacturaProveedor.proveedor,
                    FacturaProveedor.pendiente,
                    FacturaProveedor.fecha,
                    FacturaProveedor.fecha_vencimiento,
                    func.coalesce(PagarAviso.avisos_enviados, 0).label("avisos_enviados"),
                    PagarAviso.ultimo_aviso_en,
                )
                .outerjoin(PagarAviso, PagarAviso.factura_id == FacturaProveedor.id)
                .where(FacturaProveedor.pendiente > 0)
                .order_by(func.coalesce(FacturaProveedor.fecha_vencimiento, FacturaProveedor.fecha))
            )
        ).all()
        return [
            FacturaPendiente(
                factura_id=f.id,
                proveedor=f.proveedor,
                pendiente=f.pendiente,
                fecha=f.fecha,
                fecha_vencimiento=f.fecha_vencimiento,
                avisos_enviados=f.avisos_enviados,
                ultimo_aviso_en=f.ultimo_aviso_en,
            )
            for f in filas
        ]

    # --- estado de aviso por factura (dedup/cadencia) -------------------------
    async def estado_factura(self, factura_id: str) -> PagarAviso:
        estado = (
            await self._s.execute(
                select(PagarAviso).where(PagarAviso.factura_id == factura_id)
            )
        ).scalar_one_or_none()
        if estado is None:
            estado = PagarAviso(factura_id=factura_id)
            self._s.add(estado)
            await self._s.flush()
        return estado

    async def sellar_avisos(self, factura_ids: list[str], *, cuando: datetime) -> int:
        """Dedup + conteo: sella el aviso de cada factura SOLO tras un envío exitoso (lo decide el motor).

        Idempotente por factura dentro de la corrida (get-or-create del estado). Devuelve cuántas
        facturas quedaron selladas.
        """
        for factura_id in factura_ids:
            estado = await self.estado_factura(factura_id)
            estado.avisos_enviados += 1
            estado.ultimo_aviso_en = cuando
        await self._s.flush()
        return len(factura_ids)
