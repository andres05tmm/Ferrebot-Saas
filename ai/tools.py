"""Catálogo de herramientas nativas (ai-tools.md §5). Reemplazan los tags de FerreBot.

Cada herramienta declara: su `ToolSpec` canónico (lo que ve el modelo, derivado del JSON Schema
de su modelo Pydantic), su `rol_min` (RBAC) y su `feature` (capacidad; None = núcleo). El
`handler` traduce los args validados a una llamada al MISMO servicio de dominio que usa el bypass
y la API REST — nunca toca la base directamente — y normaliza el resultado al envelope (§3).

Los rieles (producto/precio/confirmación) NO viven aquí: los corre el despachador antes de invocar
el handler (ADR 0005, decisión c). Aquí solo está el cableado herramienta→servicio y el mapeo de
errores de dominio a los códigos estables del envelope.
"""
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from ai.envelope import Contexto, ErrorTool, Resultado
from core.llm.base import ToolSpec
from modules.caja.errors import CajaNoAbierta
from modules.caja.service import CajaService
from modules.clientes.schemas import ClienteCrear
from modules.clientes.service import ClientesService
from modules.fiados.errors import ClienteInexistente, FiadoInexistente, SobreAbono
from modules.fiados.service import FiadosService
from modules.ventas.errors import LineaInvalida, ProductoNoEncontrado, StockInsuficiente
from modules.ventas.schemas import MetodoPago, VentaCrear, VentaDetalleCrear
from modules.ventas.service import VentaService


# --- Dependencias del turno (servicios atados a la sesión del tenant) --------
@dataclass(frozen=True, slots=True)
class Deps:
    ventas: VentaService
    caja: CajaService
    fiados: FiadosService
    clientes: ClientesService


# --- Args de cada herramienta (lo único que provee el modelo) ----------------
class ItemVentaArg(BaseModel):
    producto_id: int | None = None
    descripcion: str | None = None
    cantidad: Decimal = Field(gt=0)
    precio_unitario: Decimal | None = Field(default=None, ge=0)
    # Explícito: True solo si el USUARIO dijo el precio. El despachador NO lo infiere; el riel de
    # precio confía en esta bandera para no cuestionar un precio declarado (ADR 0005).
    precio_dicho_por_usuario: bool = False

    @model_validator(mode="after")
    def _validar_varia(self) -> "ItemVentaArg":
        # Misma regla que VentaDetalleCrear: una venta varia (sin producto_id) exige descripción y
        # precio. Se valida aquí para que el despachador lo capture como `validacion` (recuperable)
        # y no reviente dentro del handler al construir el schema de dominio.
        if self.producto_id is None and (self.precio_unitario is None or not self.descripcion):
            raise ValueError("Un ítem sin producto_id requiere descripcion y precio_unitario")
        return self


class RegistrarVentaArgs(BaseModel):
    items: list[ItemVentaArg] = Field(min_length=1)
    metodo_pago: MetodoPago
    cliente_id: int | None = None       # requerido si metodo_pago = fiado


class RegistrarGastoArgs(BaseModel):
    categoria: str = Field(min_length=1)
    monto: Decimal = Field(gt=0)
    concepto: str | None = None


class RegistrarFiadoArgs(BaseModel):
    cliente_id: int
    venta_id: int | None = None
    monto: Decimal = Field(gt=0)


class AbonarFiadoArgs(BaseModel):
    # Abono por `fiado_id` (el modelo lo resuelve antes con una consulta). El abono por
    # `cliente_id` agregando sus fiados queda fuera de este alcance (el servicio abona por fiado).
    fiado_id: int
    monto: Decimal = Field(gt=0)


class CrearClienteArgs(ClienteCrear):
    """Mismos campos que ClienteCrear (ai-tools.md §5.4)."""


# --- Handlers: args validados + contexto → servicio de dominio → envelope ----
async def _registrar_venta(args: RegistrarVentaArgs, ctx: Contexto, deps: Deps) -> Resultado | ErrorTool:
    lineas = [
        VentaDetalleCrear(
            producto_id=it.producto_id,
            descripcion=it.descripcion,
            cantidad=it.cantidad,
            # El catálogo es la fuente de verdad: solo se pasa override si el usuario declaró el
            # precio, o si es venta varia (sin producto_id, el precio es obligatorio).
            precio_unitario=(
                it.precio_unitario
                if (it.precio_dicho_por_usuario or it.producto_id is None)
                else None
            ),
        )
        for it in args.items
    ]
    datos = VentaCrear(
        metodo_pago=args.metodo_pago,
        cliente_id=args.cliente_id,
        origen=ctx.origen,
        idempotency_key=ctx.idempotency_key,
        lineas=lineas,
    )
    try:
        res = await deps.ventas.registrar_venta(datos, vendedor_id=ctx.usuario_id)
    except StockInsuficiente as exc:
        return ErrorTool("stock_insuficiente", str(exc), recuperable=True)
    except ProductoNoEncontrado as exc:
        return ErrorTool("producto_no_encontrado", str(exc), recuperable=True)
    except LineaInvalida as exc:
        return ErrorTool("validacion", str(exc), recuperable=True)
    v = res.venta
    return Resultado(
        data={
            "venta_id": v.id, "consecutivo": v.consecutivo, "subtotal": str(v.subtotal),
            "impuestos": str(v.impuestos), "total": str(v.total), "metodo_pago": v.metodo_pago,
        },
        resumen=f"Venta #{v.consecutivo} por ${v.total} ({v.metodo_pago}).",
        evento="venta_registrada",
        idempotente="duplicada" if res.replay else "aplicada",
    )


async def _registrar_gasto(args: RegistrarGastoArgs, ctx: Contexto, deps: Deps) -> Resultado | ErrorTool:
    try:
        res = await deps.caja.registrar_gasto(
            usuario_id=ctx.usuario_id, categoria=args.categoria, monto=args.monto,
            concepto=args.concepto, idempotency_key=ctx.idempotency_key,
        )
    except CajaNoAbierta as exc:
        return ErrorTool("caja_cerrada", str(exc), recuperable=True)
    g = res.gasto
    return Resultado(
        data={"gasto_id": g.id, "categoria": args.categoria, "monto": str(args.monto)},
        resumen=f"Gasto de ${args.monto} en {args.categoria} registrado.",
        evento="gasto_registrado",
        idempotente="duplicada" if res.replay else "aplicada",
    )


async def _registrar_fiado(args: RegistrarFiadoArgs, ctx: Contexto, deps: Deps) -> Resultado | ErrorTool:
    try:
        res = await deps.fiados.crear(
            cliente_id=args.cliente_id, venta_id=args.venta_id, monto=args.monto,
            idempotency_key=ctx.idempotency_key,
        )
    except ClienteInexistente as exc:
        return ErrorTool("cliente_no_encontrado", str(exc), recuperable=True)
    f = res.fiado
    return Resultado(
        data={"fiado_id": f.id, "cliente_id": f.cliente_id, "monto": str(args.monto)},
        resumen=f"Fiado de ${args.monto} registrado.",
        evento="fiado_registrado",
        idempotente="duplicada" if res.replay else "aplicada",
    )


async def _abonar_fiado(args: AbonarFiadoArgs, ctx: Contexto, deps: Deps) -> Resultado | ErrorTool:
    try:
        res = await deps.fiados.abonar(
            fiado_id=args.fiado_id, monto=args.monto, idempotency_key=ctx.idempotency_key,
        )
    except FiadoInexistente as exc:
        return ErrorTool("cliente_no_encontrado", str(exc), recuperable=True)
    except SobreAbono as exc:
        return ErrorTool("validacion", str(exc), recuperable=True)
    m = res.movimiento
    return Resultado(
        data={"fiado_id": m.fiado_id, "movimiento_id": m.id, "abono": str(args.monto)},
        resumen=f"Abono de ${args.monto} aplicado.",
        evento="fiado_abonado",
        idempotente="duplicada" if res.replay else "aplicada",
    )


async def _crear_cliente(args: CrearClienteArgs, ctx: Contexto, deps: Deps) -> Resultado | ErrorTool:
    res = await deps.clientes.crear(ClienteCrear(**args.model_dump()))
    c = res.cliente
    return Resultado(
        data={"id": c.id, "creado": res.creado},
        resumen=(f"Cliente {c.nombre} creado." if res.creado else f"El cliente {c.nombre} ya existía."),
    )


# --- Tabla del catálogo ------------------------------------------------------
ArgsModel = type[BaseModel]
Handler = Callable[[BaseModel, Contexto, Deps], Awaitable[Resultado | ErrorTool]]


@dataclass(frozen=True, slots=True)
class Tool:
    nombre: str
    descripcion: str
    args_model: ArgsModel
    rol_min: str
    feature: str | None
    handler: Handler
    # Política de rieles del despachador (ADR 0005, decisión c):
    valida_productos: bool = False   # R1+R2 (solo registrar_venta)
    confirmable: bool = False        # R3 (gasto/fiado/abono)

    @property
    def spec(self) -> ToolSpec:
        """Catálogo canónico para el modelo; los parámetros salen del JSON Schema del args_model."""
        return ToolSpec(
            name=self.nombre,
            description=self.descripcion,
            parameters=self.args_model.model_json_schema(),
        )


CATALOGO: tuple[Tool, ...] = (
    Tool(
        nombre="registrar_venta",
        descripcion="Registra una venta. El backend calcula totales e IVA; nunca envíes totales.",
        args_model=RegistrarVentaArgs, rol_min="vendedor", feature=None,
        handler=_registrar_venta, valida_productos=True,
    ),
    Tool(
        nombre="registrar_gasto",
        descripcion="Registra un gasto (egreso de caja). Requiere caja abierta.",
        args_model=RegistrarGastoArgs, rol_min="vendedor", feature=None,
        handler=_registrar_gasto, confirmable=True,
    ),
    Tool(
        nombre="registrar_fiado",
        descripcion="Registra un crédito (fiado) a un cliente.",
        args_model=RegistrarFiadoArgs, rol_min="vendedor", feature="fiados",
        handler=_registrar_fiado, confirmable=True,
    ),
    Tool(
        nombre="abonar_fiado",
        descripcion="Registra un abono a un fiado existente (por fiado_id).",
        args_model=AbonarFiadoArgs, rol_min="vendedor", feature="fiados",
        handler=_abonar_fiado, confirmable=True,
    ),
    Tool(
        nombre="crear_cliente",
        descripcion="Crea un cliente. Si ya existe por documento, devuelve el existente.",
        args_model=CrearClienteArgs, rol_min="vendedor", feature=None,
        handler=_crear_cliente,
    ),
)

POR_NOMBRE: dict[str, Tool] = {t.nombre: t for t in CATALOGO}
