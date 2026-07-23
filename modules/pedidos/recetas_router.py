"""Router de recetas/BOM (F6 Pack Restaurante, ADR 0032 D9). Gateado por `recetas` (404 sin él).

Ver la receta y su COSTO DE PLATO (Σ costo_promedio × cantidad — reusa COGS, ADR 0025) es de
staff; editarla es de admin. Sin SQL aquí.
"""
from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth import Principal, require_role
from core.auth.features import require_feature
from core.db.session import get_tenant_db
from modules.pedidos.repository import SqlPedidosRepository

router = APIRouter(
    prefix="/recetas", tags=["recetas"],
    dependencies=[Depends(require_feature("recetas"))],
)


class InsumoReceta(BaseModel):
    insumo_id: int
    cantidad: Decimal = Field(gt=0)


class RecetaEditar(BaseModel):
    insumos: list[InsumoReceta] = Field(max_length=50)


def _respuesta(insumos: list[dict]) -> dict:
    costo = sum(
        ((i["costo_unitario"] or Decimal("0")) * i["cantidad"] for i in insumos), Decimal("0")
    )
    return {
        "insumos": [
            {
                "insumo_id": i["insumo_id"], "nombre": i["nombre"],
                "cantidad": str(i["cantidad"]),
                "costo_unitario": str(i["costo_unitario"]) if i["costo_unitario"] is not None else None,
            }
            for i in insumos
        ],
        "costo_plato": str(costo),
    }


@router.get("/ingenieria")
async def ingenieria_menu(
    dias: int = 30,
    session: AsyncSession = Depends(get_tenant_db),
    _user: Principal = Depends(require_role("vendedor")),
) -> list[dict]:
    """Reporte de ingeniería de menú (F7 / ADR 0032): margen (F6) × rotación por plato con receta."""
    filas = await SqlPedidosRepository(session).ingenieria_menu(dias=max(1, min(dias, 365)))
    return [
        {
            **f,
            "precio_venta": str(f["precio_venta"]), "costo_plato": str(f["costo_plato"]),
            "margen": str(f["margen"]), "rotacion": str(f["rotacion"]),
            "margen_total": str(f["margen_total"]),
        }
        for f in filas
    ]


@router.get("/{producto_id}")
async def ver_receta(
    producto_id: int,
    session: AsyncSession = Depends(get_tenant_db),
    _user: Principal = Depends(require_role("vendedor")),
) -> dict:
    return _respuesta(await SqlPedidosRepository(session).receta_de(producto_id))


@router.put("/{producto_id}")
async def editar_receta(
    producto_id: int,
    payload: RecetaEditar,
    session: AsyncSession = Depends(get_tenant_db),
    _user: Principal = Depends(require_role("admin")),
) -> dict:
    repo = SqlPedidosRepository(session)
    if not await repo.producto_activo(producto_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Producto no encontrado")
    await repo.reemplazar_receta(
        producto_id,
        [{"insumo_id": i.insumo_id, "cantidad": i.cantidad} for i in payload.insumos],
    )
    return _respuesta(await repo.receta_de(producto_id))
