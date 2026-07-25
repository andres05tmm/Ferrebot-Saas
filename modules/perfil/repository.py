"""Repositorio del perfil de usuario: único lugar con SQL (regla no negociable #2).

La tabla `usuarios` no tiene modelo ORM (patrón FK-less de caja/ventas), así que aquí va SQL de
texto sobre la sesión del tenant. El historial de acciones es un UNION de las fuentes que YA
atribuyen a la persona: ventas (`vendedor_id`), gastos (`usuario_id`), abonos de fiados
(`usuario_id`, 0065), compras (movimientos ENTRADA `compra:{id}` con `usuario_id`) y aperturas /
cierres de caja. Siempre acotado al PROPIO usuario: el `usuario_id` sale del token, jamás de un
parámetro.
"""
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True, slots=True)
class PerfilFila:
    id: int
    nombre: str
    rol: str
    avatar_url: str | None
    color: str | None
    creado_en: datetime | None


@dataclass(frozen=True, slots=True)
class AccionFila:
    tipo: str          # venta | gasto | abono | compra | caja_apertura | caja_cierre
    ref_id: int
    fecha: datetime
    monto: Decimal | None
    detalle: str
    estado: str        # ok | anulada


@dataclass(frozen=True, slots=True)
class ResumenAcciones:
    ventas: int
    total_vendido: Decimal
    gastos: int
    abonos: int
    compras: int


# UNION del historial. Cada rama proyecta (tipo, ref_id, fecha, monto, detalle, estado) y filtra por
# el usuario; el rango y el orden se aplican afuera. Las compras se atribuyen por sus movimientos
# ENTRADA `compra:{id}` (la tabla no guarda usuario propio).
_SQL_ACCIONES = """
SELECT * FROM (
  SELECT 'venta' AS tipo, v.id AS ref_id, v.fecha, v.total AS monto,
         'Venta N.º ' || v.consecutivo AS detalle,
         CASE WHEN v.estado = 'anulada' THEN 'anulada' ELSE 'ok' END AS estado
  FROM ventas v WHERE v.vendedor_id = :u
  UNION ALL
  SELECT 'gasto', g.id, g.creado_en, g.monto,
         COALESCE(g.concepto, initcap(g.categoria::text)),
         CASE WHEN g.anulado_en IS NULL THEN 'ok' ELSE 'anulada' END
  FROM gastos g WHERE g.usuario_id = :u
  UNION ALL
  SELECT 'abono', m.id, m.creado_en, m.monto,
         'Abono de ' || c.nombre, 'ok'
  FROM fiados_movimientos m
  JOIN fiados f ON f.id = m.fiado_id
  JOIN clientes c ON c.id = f.cliente_id
  WHERE m.tipo = 'abono' AND m.usuario_id = :u
  UNION ALL
  SELECT 'compra', c.id, c.fecha, COALESCE(c.total, 0),
         COALESCE('Compra a ' || p.nombre, 'Compra'), 'ok'
  FROM compras c LEFT JOIN proveedores p ON p.id = c.proveedor_id
  WHERE c.id IN (
    SELECT DISTINCT substring(mi.referencia FROM 8)::bigint
    FROM movimientos_inventario mi
    WHERE mi.tipo = 'ENTRADA' AND mi.referencia LIKE 'compra:%' AND mi.usuario_id = :u
  )
  UNION ALL
  SELECT 'caja_apertura', cj.id, cj.fecha_apertura, cj.saldo_inicial, 'Abrió caja', 'ok'
  FROM caja cj WHERE cj.usuario_id = :u
  UNION ALL
  SELECT 'caja_cierre', cj.id, cj.fecha_cierre, cj.saldo_contado, 'Cerró caja', 'ok'
  FROM caja cj WHERE cj.usuario_id = :u AND cj.fecha_cierre IS NOT NULL
) t
WHERE t.fecha >= :desde
ORDER BY t.fecha DESC
LIMIT :lim OFFSET :off
"""

_SQL_RESUMEN = """
SELECT
  (SELECT count(*) FROM ventas v
     WHERE v.vendedor_id = :u AND v.fecha >= :desde AND v.estado = 'completada') AS ventas,
  (SELECT COALESCE(SUM(v.total), 0) FROM ventas v
     WHERE v.vendedor_id = :u AND v.fecha >= :desde AND v.estado = 'completada') AS total_vendido,
  (SELECT count(*) FROM gastos g
     WHERE g.usuario_id = :u AND g.creado_en >= :desde AND g.anulado_en IS NULL) AS gastos,
  (SELECT count(*) FROM fiados_movimientos m
     WHERE m.tipo = 'abono' AND m.usuario_id = :u AND m.creado_en >= :desde) AS abonos,
  (SELECT count(*) FROM compras c
     WHERE c.fecha >= :desde AND c.id IN (
       SELECT DISTINCT substring(mi.referencia FROM 8)::bigint
       FROM movimientos_inventario mi
       WHERE mi.tipo = 'ENTRADA' AND mi.referencia LIKE 'compra:%' AND mi.usuario_id = :u
     )) AS compras
"""


class SqlPerfilRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def obtener(self, usuario_id: int) -> PerfilFila | None:
        row = (
            await self._s.execute(
                text(
                    "SELECT id, nombre, rol::text AS rol, avatar_url, color, creado_en "
                    "FROM usuarios WHERE id = :u"
                ),
                {"u": usuario_id},
            )
        ).first()
        if row is None:
            return None
        m = row._mapping
        return PerfilFila(
            id=m["id"], nombre=m["nombre"], rol=m["rol"],
            avatar_url=m["avatar_url"], color=m["color"], creado_en=m["creado_en"],
        )

    async def actualizar(
        self, usuario_id: int, *, nombre: str | None = None,
        color: str | None = None, avatar_url: str | None = None,
    ) -> None:
        """Actualiza SOLO los campos provistos del propio usuario. El commit es del llamador."""
        sets, params = [], {"u": usuario_id}
        if nombre is not None:
            sets.append("nombre = :n")
            params["n"] = nombre
        if color is not None:
            sets.append("color = :c")
            params["c"] = color
        if avatar_url is not None:
            sets.append("avatar_url = :a")
            params["a"] = avatar_url
        if not sets:
            return
        await self._s.execute(
            text(f"UPDATE usuarios SET {', '.join(sets)} WHERE id = :u"), params
        )

    async def acciones(
        self, usuario_id: int, *, desde: datetime, limite: int, offset: int
    ) -> list[AccionFila]:
        rows = (
            await self._s.execute(
                text(_SQL_ACCIONES),
                {"u": usuario_id, "desde": desde, "lim": limite, "off": offset},
            )
        ).all()
        return [
            AccionFila(
                tipo=r.tipo, ref_id=r.ref_id, fecha=r.fecha, monto=r.monto,
                detalle=r.detalle, estado=r.estado,
            )
            for r in rows
        ]

    async def resumen(self, usuario_id: int, *, desde: datetime) -> ResumenAcciones:
        row = (
            await self._s.execute(text(_SQL_RESUMEN), {"u": usuario_id, "desde": desde})
        ).one()
        return ResumenAcciones(
            ventas=row.ventas, total_vendido=row.total_vendido or Decimal("0"),
            gastos=row.gastos, abonos=row.abonos, compras=row.compras,
        )
