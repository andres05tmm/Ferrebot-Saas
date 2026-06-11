"""Herramientas de agente del pack Ventas/Cotizaciones (ADR 0017), de cara al cliente en WhatsApp.

Capa fina sobre el motor (`modules/cotizaciones/service.py`). GUARDARRAÍL CLAVE (del plan): el
agente **nunca inventa precio ni stock** — el precio sale del motor real de inventario (escalonado
por cantidad) y el stock solo se expone si el negocio lo permite (`mostrar_stock`). El **tenant** y
el **teléfono** viajan en el `Contexto` del canal, nunca como args del modelo: el carrito y la
cotización son SIEMPRE del que escribe. Se exponen solo con el flag `pack_ventas`.
"""
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from decimal import Decimal

from pydantic import BaseModel, Field, ValidationError

from ai.envelope import Contexto, ErrorTool, Resultado
from core.config.timezone import today_co
from core.llm.base import ToolCall, ToolSpec
from modules.cotizaciones.errors import CarritoVacio, ProductoNoResuelto
from modules.cotizaciones.service import CotizacionesService, ItemCotizar


@dataclass(frozen=True, slots=True)
class CotizacionesDeps:
    """Dependencias del turno: el motor de cotizaciones atado a la sesión del tenant."""

    cotizaciones: CotizacionesService


def _pesos(monto) -> str:
    return "$" + f"{Decimal(monto):,.0f}".replace(",", ".")


def _telefono(ctx: Contexto) -> str | None:
    return ctx.cliente_telefono or None


def _data_cotizacion(c) -> dict:
    return {
        "cotizacion_id": c.id, "estado": c.estado, "total": str(c.total),
        "vigencia_hasta": str(c.vigencia_hasta) if c.vigencia_hasta else None,
        "items": [
            {"nombre": i.nombre, "cantidad": str(i.cantidad),
             "precio_unitario": str(i.precio_unitario), "subtotal": str(i.subtotal)}
            for i in c.items
        ],
    }


def _resumen_items(c) -> str:
    return "; ".join(
        f"{i.cantidad:g}× {i.nombre} a {_pesos(i.precio_unitario)} = {_pesos(i.subtotal)}"
        for i in c.items
    )


def _error_no_resuelto(exc: ProductoNoResuelto) -> ErrorTool:
    detalle = f"No encontré '{exc.texto}' en el catálogo."
    if exc.sugerencias:
        detalle += " ¿Quiso decir: " + ", ".join(exc.sugerencias) + "? Confirma con el cliente."
    return ErrorTool("producto_no_encontrado", detalle, recuperable=True)


_SIN_TELEFONO = ErrorTool(
    "contexto_invalido", "Falta el teléfono del cliente en el contexto del canal."
)


# --- args (lo único que provee el modelo; el teléfono NUNCA va aquí) ---------
class CotizarProductoArgs(BaseModel):
    producto: str = Field(min_length=1, max_length=120)
    cantidad: Decimal = Field(default=Decimal("1"), gt=0, le=99999)


class ItemArgs(BaseModel):
    producto: str = Field(min_length=1, max_length=120)
    cantidad: Decimal = Field(gt=0, le=99999)


class AgregarArgs(BaseModel):
    items: list[ItemArgs] = Field(min_length=1, max_length=30)


class QuitarArgs(BaseModel):
    producto: str = Field(min_length=1, max_length=120)


class VerCotizacionArgs(BaseModel):
    """Sin parámetros: SIEMPRE la cotización del teléfono del contexto."""


class EmitirArgs(BaseModel):
    """Sin parámetros: emite la cotización abierta del teléfono del contexto."""


# --- handlers ---------------------------------------------------------------
async def _cotizar_producto(
    args: CotizarProductoArgs, ctx: Contexto, deps: CotizacionesDeps
) -> Resultado | ErrorTool:
    try:
        p = await deps.cotizaciones.cotizar(args.producto, args.cantidad)
    except ProductoNoResuelto as exc:
        return _error_no_resuelto(exc)
    stock_txt = ""
    if p.stock is not None:
        stock_txt = f" Hay {p.stock:g} disponibles." if p.stock > 0 else " Sin existencias ahora."
    cantidad_txt = f" por {p.cantidad:g}" if p.cantidad != 1 else ""
    return Resultado(
        data={
            "producto_id": p.producto_id, "nombre": p.nombre, "cantidad": str(p.cantidad),
            "precio_unitario": str(p.precio_unitario), "total": str(p.total), "regla": p.regla,
            "stock": str(p.stock) if p.stock is not None else None,
        },
        resumen=(
            f"{p.nombre}: {_pesos(p.precio_unitario)} c/u{cantidad_txt} → {_pesos(p.total)}."
            + stock_txt
        ),
    )


async def _agregar(args: AgregarArgs, ctx: Contexto, deps: CotizacionesDeps) -> Resultado | ErrorTool:
    telefono = _telefono(ctx)
    if telefono is None:
        return _SIN_TELEFONO
    items = [ItemCotizar(producto=i.producto, cantidad=i.cantidad) for i in args.items]
    try:
        c = await deps.cotizaciones.agregar(telefono, items, idempotency_key=ctx.idempotency_key)
    except ProductoNoResuelto as exc:
        return _error_no_resuelto(exc)
    return Resultado(
        data=_data_cotizacion(c),
        resumen=(
            f"Cotización en armado 🧾 {_resumen_items(c)}. Total: {_pesos(c.total)}. "
            "Pregunta si desea agregar algo más o emitirla."
        ),
        evento="cotizacion_actualizada",
        idempotente="aplicada",
    )


async def _quitar(args: QuitarArgs, ctx: Contexto, deps: CotizacionesDeps) -> Resultado | ErrorTool:
    telefono = _telefono(ctx)
    if telefono is None:
        return _SIN_TELEFONO
    try:
        c = await deps.cotizaciones.quitar(telefono, args.producto)
    except CarritoVacio:
        return ErrorTool("sin_cotizacion", "No hay una cotización en armado.", recuperable=True)
    except ProductoNoResuelto as exc:
        return _error_no_resuelto(exc)
    resumen = (
        f"Quitado. Queda: {_resumen_items(c)}. Total: {_pesos(c.total)}."
        if c.items else "Quitado. La cotización quedó vacía."
    )
    return Resultado(data=_data_cotizacion(c), resumen=resumen, idempotente="aplicada")


async def _ver(args: VerCotizacionArgs, ctx: Contexto, deps: CotizacionesDeps) -> Resultado | ErrorTool:
    telefono = _telefono(ctx)
    if telefono is None:
        return _SIN_TELEFONO
    c = await deps.cotizaciones.ver(telefono)
    if c is None or not c.items:
        return Resultado(data={"cotizacion": None}, resumen="No tiene una cotización en armado.")
    return Resultado(
        data=_data_cotizacion(c),
        resumen=f"Su cotización: {_resumen_items(c)}. Total: {_pesos(c.total)}.",
    )


async def _emitir(args: EmitirArgs, ctx: Contexto, deps: CotizacionesDeps) -> Resultado | ErrorTool:
    telefono = _telefono(ctx)
    if telefono is None:
        return _SIN_TELEFONO
    try:
        c = await deps.cotizaciones.emitir(telefono, hoy=today_co())
    except CarritoVacio:
        return ErrorTool(
            "sin_cotizacion",
            "No hay cotización con ítems para emitir: primero agrega productos.",
            recuperable=True,
        )
    return Resultado(
        data=_data_cotizacion(c),
        resumen=(
            f"Cotización #{c.id} emitida ✅ {_resumen_items(c)}. Total: {_pesos(c.total)}. "
            f"Válida hasta el {c.vigencia_hasta}. Preséntala al cliente línea por línea."
        ),
        evento="cotizacion_emitida",
        idempotente="aplicada",
    )


# --- catálogo ---------------------------------------------------------------
Handler = Callable[[BaseModel, Contexto, CotizacionesDeps], Awaitable[Resultado | ErrorTool]]


@dataclass(frozen=True, slots=True)
class CotizacionesTool:
    """Herramienta del pack: lo que ve el modelo (spec) + su handler. Gated por `feature`."""

    nombre: str
    descripcion: str
    args_model: type[BaseModel]
    handler: Handler
    feature: str = "pack_ventas"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.nombre,
            description=self.descripcion,
            parameters=self.args_model.model_json_schema(),
        )


CATALOGO_COTIZACIONES: tuple[CotizacionesTool, ...] = (
    CotizacionesTool(
        nombre="cotizar_producto",
        descripcion=(
            "Consulta el precio REAL de un producto del catálogo para una cantidad (aplica precio "
            "escalonado/mayorista si corresponde). Úsala SIEMPRE que pregunten '¿a cómo…?' o "
            "'¿tienes…?'; nunca inventes precios ni stock. Solo lectura."
        ),
        args_model=CotizarProductoArgs, handler=_cotizar_producto,
    ),
    CotizacionesTool(
        nombre="agregar_a_cotizacion",
        descripcion=(
            "Agrega productos (nombre + cantidad) a la cotización en armado del cliente; si el "
            "producto ya está, actualiza la cantidad y recotiza el precio."
        ),
        args_model=AgregarArgs, handler=_agregar,
    ),
    CotizacionesTool(
        nombre="quitar_de_cotizacion",
        descripcion="Quita un producto de la cotización en armado del cliente.",
        args_model=QuitarArgs, handler=_quitar,
    ),
    CotizacionesTool(
        nombre="ver_mi_cotizacion",
        descripcion="Muestra la cotización en armado del cliente (ítems y total). Solo lectura.",
        args_model=VerCotizacionArgs, handler=_ver,
    ),
    CotizacionesTool(
        nombre="emitir_cotizacion",
        descripcion=(
            "Cierra y emite la cotización del cliente con su vigencia. Úsala cuando confirme que "
            "eso es todo lo que necesita cotizar."
        ),
        args_model=EmitirArgs, handler=_emitir,
    ),
)

POR_NOMBRE: dict[str, CotizacionesTool] = {t.nombre: t for t in CATALOGO_COTIZACIONES}


def catalogo_visible(ctx: Contexto) -> list[CotizacionesTool]:
    """Herramientas del pack visibles para la empresa (solo si tiene el flag `pack_ventas`)."""
    return [t for t in CATALOGO_COTIZACIONES if ctx.tiene_capacidad(t.feature)]


def exponer_catalogo(ctx: Contexto) -> list[ToolSpec]:
    """Specs que ve el modelo (filtradas por capacidad), listas para el runtime del agente."""
    return [t.spec for t in catalogo_visible(ctx)]


async def ejecutar(
    tool_call: ToolCall, ctx: Contexto, deps: CotizacionesDeps
) -> Resultado | ErrorTool:
    """Frontera de ejecución: valida los args del modelo (Pydantic) y corre el handler.

    Cualquier `cliente_telefono` que el modelo intente colar se ignora (no está en ningún args_model) —
    la identidad sale SIEMPRE del `Contexto`.
    """
    tool = POR_NOMBRE.get(tool_call.name)
    if tool is None:
        return ErrorTool("error_interno", f"Herramienta desconocida: {tool_call.name}")
    try:
        args = tool.args_model(**tool_call.arguments)
    except ValidationError as exc:
        return ErrorTool("validacion", str(exc), recuperable=True)
    return await tool.handler(args, ctx, deps)
