"""Herramientas de agente del pack Pedidos (ADR 0016), de cara al cliente en WhatsApp.

Capa fina sobre el motor (`modules/pedidos/service.py`): cada herramienta traduce los args del
modelo a una llamada al servicio y normaliza la salida al envelope común. NO reimplementa lógica de
pedidos (resolución de catálogo, precios, tarifas, transiciones) — eso vive en el motor.

GUARDARRAÍL DE SEGURIDAD (no negociable): el **tenant** y el **teléfono del cliente** viajan en el
`Contexto` que inyecta el adaptador de canal (el número que escribe), **nunca** como args del modelo.
`armar_pedido`/`confirmar_pedido`/`estado_mi_pedido` operan SOLO sobre el pedido de ese teléfono.
El agente jamás inventa productos, precios ni tarifas de domicilio: si el catálogo no resuelve, la
herramienta devuelve sugerencias o un error recuperable. Se exponen solo con el flag `pack_pedidos`.
"""
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from decimal import Decimal

from pydantic import BaseModel, Field, ValidationError

from ai.envelope import Contexto, ErrorTool, Resultado
from core.config.timezone import now_co
from core.llm.base import ToolCall, ToolSpec
from core.logging import get_logger
from modules.pagos.service import PagosService

log = get_logger("ai.pedidos_tools")
from modules.pedidos.errors import (
    CocinaCerrada,
    PedidoMuyChico,
    ProductoNoEncontrado,
    SinBorrador,
    StockInsuficiente,
)
from modules.pedidos.service import ItemPedido, PedidosService


@dataclass(frozen=True, slots=True)
class PedidosDeps:
    """Dependencias del turno: el motor de pedidos (+ pagos OPCIONAL) atados a la sesión del tenant.

    `pagos=None` = sin frente de pagos: el pedido se confirma igual (cobro contraentrega/manual).
    Con `pagos` y la capacidad `pagos_online`, al confirmar se crea la solicitud de cobro (ADR 0013):
    con PSP del tenant trae link/QR real; sin PSP queda `manual` (etiqueta "pendiente de pago").
    """

    pedidos: PedidosService
    pagos: PagosService | None = None


# --- helpers ----------------------------------------------------------------
def _pesos(monto) -> str:
    return "$" + f"{Decimal(monto):,.0f}".replace(",", ".")


def _telefono(ctx: Contexto) -> str | None:
    return ctx.cliente_telefono or None


def _resumen_pedido(pedido) -> str:
    lineas = "; ".join(f"{i.cantidad:g}× {i.nombre} ({_pesos(i.subtotal)})" for i in pedido.items)
    return f"{lineas}. Subtotal: {_pesos(pedido.subtotal)}."


def _solo_telefono(crudo: str) -> str | None:
    """Normaliza el teléfono de contacto a dígitos (+ opcional). None si no parece un teléfono."""
    limpio = "".join(c for c in crudo if c.isdigit() or c == "+")
    digitos = limpio.lstrip("+")
    if not (7 <= len(digitos) <= 15):
        return None
    return limpio


def _data_pedido(pedido) -> dict:
    return {
        "pedido_id": pedido.id, "estado": pedido.estado,
        "subtotal": str(pedido.subtotal), "domicilio": str(pedido.costo_domicilio),
        "total": str(pedido.total),
        "items": [
            {"nombre": i.nombre, "cantidad": str(i.cantidad), "subtotal": str(i.subtotal)}
            for i in pedido.items
        ],
    }


_SIN_TELEFONO = ErrorTool(
    "contexto_invalido", "Falta el teléfono del cliente en el contexto del canal."
)


def _es_transferencia(metodo_pago: str) -> bool:
    """¿El método de pago elegido es una transferencia (Bancolombia/Nequi)? Match laxo por texto."""
    m = (metodo_pago or "").strip().lower()
    return "transfer" in m or "nequi" in m or "bancolombia" in m


async def _leer_datos_pago(tenant_id: int) -> tuple[str | None, str | None]:
    """`(titular, numero)` de la cuenta de transferencia del negocio (config_empresa, control DB).

    Aislado en una función para (a) reusar el read de `core.tenancy.config_empresa` y (b) ser
    monkeypatcheable en tests sin control DB. Degrada a (None, None) ante cualquier fallo: el agente
    informa el total igual (nunca inventa un número de cuenta)."""
    from core.db.session import control_session
    from core.tenancy.config_empresa import cargar_datos_pago

    try:
        async with control_session() as cs:
            return await cargar_datos_pago(cs, tenant_id)
    except Exception:  # noqa: BLE001 — sin datos de pago el flujo sigue (solo total)
        log.warning("pedidos_datos_pago_fallo", tenant_id=tenant_id, exc_info=True)
        return None, None


def _instruccion_transferencia(total, titular: str | None, numero: str | None) -> str:
    """Texto con el TOTAL exacto a transferir + los datos de pago del negocio (o solo el total)."""
    base = f" Para confirmar el pago, transfiere EXACTAMENTE {_pesos(total)}"
    if numero:
        destino = f" a {numero}" + (f" ({titular})" if titular else "")
        return base + destino + " y te aviso apenas entre el pago. 🙌"
    return base + " y envíame el comprobante; te aviso apenas entre el pago. 🙌"

_ESTADOS_LEGIBLES = {
    "recibido": "en armado (aún sin confirmar)",
    "confirmado": "confirmado — la cocina ya lo tiene",
    "en_preparacion": "en preparación 👨‍🍳",
    "en_camino": "en camino 🛵",
    "entregado": "entregado ✅",
    "cancelado": "cancelado",
}


# --- args (lo único que provee el modelo; el teléfono NUNCA va aquí) ---------
class VerMenuArgs(BaseModel):
    buscar: str = Field(default="", max_length=80)   # vacío = lista general


class ItemArgs(BaseModel):
    producto: str = Field(min_length=1, max_length=120)   # como lo dice el cliente
    cantidad: Decimal = Field(gt=0, le=999)


class ArmarPedidoArgs(BaseModel):
    items: list[ItemArgs] = Field(min_length=1, max_length=30)
    notas: str = Field(default="", max_length=300)   # "sin cebolla", etc.


class ConfirmarPedidoArgs(BaseModel):
    direccion: str = Field(min_length=5, max_length=200)
    barrio: str = Field(default="", max_length=80)
    metodo_pago: str = Field(min_length=1, max_length=40)   # efectivo | transferencia | datáfono…
    nombre: str = Field(default="", max_length=80)
    # Teléfono REAL para que el domiciliario coordine la entrega. En canales con identidad opaca
    # (Telegram: "tg:{chat_id}") es OBLIGATORIO pedirlo; en WhatsApp el default es el del cliente.
    telefono_contacto: str = Field(default="", max_length=30)


class EstadoMiPedidoArgs(BaseModel):
    """Sin parámetros: SIEMPRE el pedido del teléfono del contexto."""


# --- handlers ---------------------------------------------------------------
async def _ver_menu(args: VerMenuArgs, ctx: Contexto, deps: PedidosDeps) -> Resultado | ErrorTool:
    menu = await deps.pedidos.ver_menu(args.buscar)
    if not menu:
        detalle = (
            f"No encontré '{args.buscar}' en el menú." if args.buscar.strip()
            else "Por ahora no hay productos disponibles."
        )
        return Resultado(data={"menu": []}, resumen=detalle + " Ofrece escalar si insiste.")
    data = [
        {"id": p["id"], "nombre": p["nombre"], "precio": str(p["precio_venta"]),
         "unidad": p["unidad_medida"]}
        for p in menu
    ]
    partes = [f"{p['nombre']} ({_pesos(p['precio_venta'])})" for p in menu]
    return Resultado(data={"menu": data}, resumen="Disponible: " + "; ".join(partes) + ".")


async def _armar_pedido(
    args: ArmarPedidoArgs, ctx: Contexto, deps: PedidosDeps
) -> Resultado | ErrorTool:
    telefono = _telefono(ctx)
    if telefono is None:
        return _SIN_TELEFONO
    items = [ItemPedido(producto=i.producto, cantidad=i.cantidad) for i in args.items]
    try:
        res = await deps.pedidos.armar_pedido(
            telefono, items, ahora=now_co(), notas=args.notas or None,
            idempotency_key=ctx.idempotency_key,
        )
    except CocinaCerrada:
        return ErrorTool(
            "cocina_cerrada",
            "En este momento no estamos recibiendo pedidos (fuera del horario). Dile con amabilidad "
            "el horario si lo sabes por responder_faq, o que escriba más tarde.",
            recuperable=False,
        )
    except ProductoNoEncontrado as exc:
        detalle = f"No encontré '{exc.nombre}' en el catálogo."
        if exc.sugerencias:
            detalle += " ¿Quiso decir: " + ", ".join(exc.sugerencias) + "? Confirma con el cliente."
        return ErrorTool("producto_no_encontrado", detalle, recuperable=True)
    except StockInsuficiente as exc:
        return ErrorTool(
            "stock_insuficiente",
            f"De '{exc.nombre}' solo quedan {exc.disponible}. Ofrece ajustar la cantidad.",
            recuperable=True,
        )
    pedido = res.pedido
    return Resultado(
        data=_data_pedido(pedido),
        resumen=(
            f"Pedido armado 🛒 {_resumen_pedido(pedido)} "
            "Pide la dirección, el barrio y el método de pago para confirmarlo."
        ),
        evento="pedido_armado",
        idempotente="duplicada" if res.replay else "aplicada",
    )


async def _confirmar_pedido(
    args: ConfirmarPedidoArgs, ctx: Contexto, deps: PedidosDeps
) -> Resultado | ErrorTool:
    telefono = _telefono(ctx)
    if telefono is None:
        return _SIN_TELEFONO
    # Domicilio necesita a quién entregarle: nombre + teléfono REAL. En canales de identidad
    # opaca (Telegram, "tg:...") el teléfono no se puede inferir → se exige antes de confirmar.
    # En WhatsApp el propio número del cliente es el default natural.
    contacto = _solo_telefono(args.telefono_contacto)
    if telefono.startswith("tg:"):
        if not args.nombre.strip() or not contacto:
            return ErrorTool(
                "faltan_datos_entrega",
                "Para el domicilio necesitas el NOMBRE del cliente y un TELÉFONO de contacto real "
                "(el domiciliario lo llama al entregar). Pídelos con amabilidad y vuelve a confirmar.",
                recuperable=True,
            )
    elif not contacto:
        contacto = telefono
    try:
        pedido, estimado = await deps.pedidos.confirmar_pedido(
            telefono, direccion=args.direccion, barrio=args.barrio,
            metodo_pago=args.metodo_pago, nombre=args.nombre or None,
            telefono_contacto=contacto,
        )
    except SinBorrador:
        return ErrorTool(
            "sin_pedido", "No hay un pedido en armado: primero arma el pedido con armar_pedido.",
            recuperable=True,
        )
    except PedidoMuyChico as exc:
        return ErrorTool(
            "pedido_muy_chico",
            f"El pedido mínimo es {_pesos(exc.minimo)}. Ofrece agregar algo más.",
            recuperable=True,
        )
    resumen = (
        f"¡Pedido #{pedido.id} confirmado! ✅ Total {_pesos(pedido.total)} "
        f"(domicilio {_pesos(pedido.costo_domicilio)}). Tiempo estimado ~{estimado} min. "
        f"🛵 El domiciliario se comunicará al {contacto} para coordinar la entrega."
    )
    data = _data_pedido(pedido) | {"tiempo_estimado_min": estimado}
    # Frente de pagos (ADR 0013): se crea la solicitud de cobro del pedido (idempotente por origen)
    #   - con `pagos_online`: con PSP trae link real → se le manda al cliente.
    #   - con método de pago TRANSFERENCIA: cobro en modo manual (sin link) para que el conciliador de
    #     transferencias pueda casarlo por monto; se le informa el total exacto + datos de la cuenta.
    es_transferencia = _es_transferencia(args.metodo_pago)
    if deps.pagos is not None and (ctx.tiene_capacidad("pagos_online") or es_transferencia):
        cobro = await deps.pagos.crear_cobro(
            origen="pedido", origen_id=pedido.id, monto=pedido.total,
            descripcion=f"Pedido #{pedido.id}", cliente_telefono=telefono,
        )
        data["cobro"] = {"cobro_id": cobro.id, "url": cobro.url, "estado": cobro.estado}
        if cobro.url:
            resumen += f" Puede pagar de una vez aquí: {cobro.url}"
        elif es_transferencia:
            titular, numero = await _leer_datos_pago(ctx.tenant_id)
            resumen += _instruccion_transferencia(pedido.total, titular, numero)
    return Resultado(
        data=data,
        resumen=resumen,
        evento="pedido_confirmado",
        idempotente="aplicada",
    )


async def _estado_mi_pedido(
    args: EstadoMiPedidoArgs, ctx: Contexto, deps: PedidosDeps
) -> Resultado | ErrorTool:
    telefono = _telefono(ctx)
    if telefono is None:
        return _SIN_TELEFONO
    pedido = await deps.pedidos.estado_de(telefono)
    if pedido is None:
        return Resultado(data={"pedido": None}, resumen="No tiene pedidos registrados.")
    legible = _ESTADOS_LEGIBLES.get(pedido.estado, pedido.estado)
    return Resultado(
        data=_data_pedido(pedido),
        resumen=f"Su pedido #{pedido.id} está {legible}. Total {_pesos(pedido.total)}.",
    )


# --- catálogo ---------------------------------------------------------------
Handler = Callable[[BaseModel, Contexto, PedidosDeps], Awaitable[Resultado | ErrorTool]]


@dataclass(frozen=True, slots=True)
class PedidosTool:
    """Herramienta del pack: lo que ve el modelo (spec) + su handler. Gated por `feature`."""

    nombre: str
    descripcion: str
    args_model: type[BaseModel]
    handler: Handler
    feature: str = "pack_pedidos"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.nombre,
            description=self.descripcion,
            parameters=self.args_model.model_json_schema(),
        )


CATALOGO_PEDIDOS: tuple[PedidosTool, ...] = (
    PedidosTool(
        nombre="ver_menu",
        descripcion=(
            "Muestra los productos disponibles (nombre y precio), o busca uno por nombre. "
            "Úsala SIEMPRE antes de ofrecer; nunca inventes productos ni precios. Solo lectura."
        ),
        args_model=VerMenuArgs, handler=_ver_menu,
    ),
    PedidosTool(
        nombre="armar_pedido",
        descripcion=(
            "Arma (o reemplaza) el pedido del cliente con los ítems que pidió (nombre + cantidad). "
            "Valida contra el catálogo y el stock real. Tras armarlo, pide dirección, barrio y "
            "método de pago para confirmar."
        ),
        args_model=ArmarPedidoArgs, handler=_armar_pedido,
    ),
    PedidosTool(
        nombre="confirmar_pedido",
        descripcion=(
            "Confirma el pedido armado con la dirección EXACTA de entrega, el barrio (calcula el "
            "costo del domicilio), el método de pago, el NOMBRE del cliente y su TELÉFONO de "
            "contacto (el domiciliario lo llama al entregar). Solo confirma cuando el cliente haya "
            "dado todos esos datos; si falta el nombre o el teléfono, pídelos primero."
        ),
        args_model=ConfirmarPedidoArgs, handler=_confirmar_pedido,
    ),
    PedidosTool(
        nombre="estado_mi_pedido",
        descripcion="Consulta el estado del último pedido del cliente que escribe. Solo lectura.",
        args_model=EstadoMiPedidoArgs, handler=_estado_mi_pedido,
    ),
)

POR_NOMBRE: dict[str, PedidosTool] = {t.nombre: t for t in CATALOGO_PEDIDOS}


def catalogo_visible(ctx: Contexto) -> list[PedidosTool]:
    """Herramientas del pack visibles para la empresa (solo si tiene el flag `pack_pedidos`)."""
    return [t for t in CATALOGO_PEDIDOS if ctx.tiene_capacidad(t.feature)]


def exponer_catalogo(ctx: Contexto) -> list[ToolSpec]:
    """Specs que ve el modelo (filtradas por capacidad), listas para el runtime del agente."""
    return [t.spec for t in catalogo_visible(ctx)]


async def ejecutar(tool_call: ToolCall, ctx: Contexto, deps: PedidosDeps) -> Resultado | ErrorTool:
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
