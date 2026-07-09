"""Catálogo de herramientas nativas (ai-tools.md §5). Reemplazan los tags de FerreBot.

Cada herramienta declara: su `ToolSpec` canónico (lo que ve el modelo, derivado del JSON Schema
de su modelo Pydantic), su `rol_min` (RBAC) y su `feature` (capacidad; None = núcleo). El
`handler` traduce los args validados a una llamada al MISMO servicio de dominio que usa el bypass
y la API REST — nunca toca la base directamente — y normaliza el resultado al envelope (§3).

Los rieles (producto/precio/confirmación) NO viven aquí: los corre el despachador antes de invocar
el handler (ADR 0005, decisión c). Aquí solo está el cableado herramienta→servicio y el mapeo de
errores de dominio a los códigos estables del envelope.
"""
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai.envelope import Contexto, ErrorTool, Resultado
from core.llm.base import ToolSpec
from modules.caja.errors import CajaNoAbierta
from modules.caja.service import CajaService
from modules.clientes.schemas import ClienteCrear
from modules.clientes.service import ClientesService
from modules.fiados.errors import ClienteInexistente, FiadoInexistente, SobreAbono
from modules.fiados.service import FiadosService
from modules.ventas.errors import (
    IdempotenciaConflicto,
    LineaInvalida,
    ProductoNoEncontrado,
    StockInsuficiente,
)
from modules.ventas.schemas import MetodoPago, VentaCrear, VentaDetalleCrear
from modules.ventas.service import VentaService


class CierreVentaPort(Protocol):
    """Puerto del cierre fiscal post-venta (POS/FE según capacidad + intención, ADR 0014). Lo cumple
    `modules.facturacion.pos_hook.CierrePos`; jamás lanza (el cierre no rompe la venta).

    `intencion` ('pos'|'fe'|None) es la intención de documento por venta; None → default por capacidad.
    Hoy el handler no la pasa (default None); elegirla/persistirla en la UI es una fase posterior."""

    async def cerrar(
        self, venta_id: int, *, tenant_id: int, capacidades: frozenset[str],
        intencion: str | None = None,
    ) -> None: ...


# --- Dependencias del turno (servicios atados a la sesión del tenant) --------
@dataclass(frozen=True, slots=True)
class Deps:
    ventas: VentaService
    caja: CajaService
    fiados: FiadosService
    clientes: ClientesService
    # Cierre fiscal de mostrador (POS electrónico). Opcional: None cuando la plataforma no lo cablea
    # (tests, despliegues sin facturación); el handler de venta lo invoca solo si está presente.
    cierre_pos: CierreVentaPort | None = None
    # Guard de caja (toggle `caja_obligatoria` del control DB, paridad con POST /ventas del API):
    # loader por empresa que dice si la venta exige caja abierta. None = guard apagado (default
    # seguro: tests y despliegues sin el toggle no cambian).
    caja_obligatoria: Callable[[int], Awaitable[bool]] | None = None


# Topes de cordura por campo (rango razonable de mostrador). Defensa además del saneamiento previo
# (ai.saneamiento) y de los rieles/límites: un valor mayor es absurdo en este dominio.
MAX_CANTIDAD = Decimal("100000")        # cien mil unidades en una línea
MAX_MONTO = Decimal("1000000000")       # mil millones COP en un precio/monto


# --- Args de cada herramienta (lo único que provee el modelo) ----------------
class ArgsTool(BaseModel):
    """Base de los args de herramienta: validación ESTRICTA — rechaza campos no declarados.

    `extra="forbid"` evita que el modelo (o un mensaje malicioso) cuele parámetros no contemplados; se
    refleja como `additionalProperties: false` en el JSON Schema que ve el modelo.
    """

    model_config = ConfigDict(extra="forbid")


class ItemVentaArg(ArgsTool):
    producto_id: int | None = Field(default=None, gt=0)
    descripcion: str | None = None
    cantidad: Decimal = Field(gt=0, le=MAX_CANTIDAD)
    precio_unitario: Decimal | None = Field(default=None, ge=0, le=MAX_MONTO)
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


class RegistrarVentaArgs(ArgsTool):
    items: list[ItemVentaArg] = Field(min_length=1, max_length=200)
    metodo_pago: MetodoPago
    cliente_id: int | None = Field(default=None, gt=0)   # requerido si metodo_pago = fiado


class RegistrarGastoArgs(ArgsTool):
    categoria: str = Field(min_length=1)
    monto: Decimal = Field(gt=0, le=MAX_MONTO)
    concepto: str | None = None


class RegistrarFiadoArgs(ArgsTool):
    cliente_id: int = Field(gt=0)
    venta_id: int | None = Field(default=None, gt=0)
    monto: Decimal = Field(gt=0, le=MAX_MONTO)


class AbonarFiadoArgs(ArgsTool):
    # Abono por `fiado_id` (el modelo lo resuelve antes con una consulta). El abono por
    # `cliente_id` agregando sus fiados queda fuera de este alcance (el servicio abona por fiado).
    fiado_id: int = Field(gt=0)
    monto: Decimal = Field(gt=0, le=MAX_MONTO)


class CrearClienteArgs(ClienteCrear):
    """Mismos campos que ClienteCrear (ai-tools.md §5.4), pero estricto: sin campos no declarados."""

    model_config = ConfigDict(extra="forbid")


class ConsultarVentasDiaArgs(ArgsTool):
    """Sin parámetros: la consulta es SIEMPRE de hoy (zona Colombia)."""


class ConsultarProductoArgs(ArgsTool):
    nombre: str = Field(min_length=1)


class RegistrarAliasArgs(ArgsTool):
    # `termino` = como lo dicen los clientes/vendedores (variante, typo, apodo). `reemplazo` = el
    # nombre canónico que el catálogo SÍ conoce. El backend guarda el alias global y la búsqueda lo usa.
    termino: str = Field(min_length=1, max_length=80)
    reemplazo: str = Field(min_length=1, max_length=120)


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
    # Guard de caja (paridad con POST /ventas del API): con `caja_obligatoria` ON y sin caja abierta
    # EN LA EMPRESA (modo un-cajón), la venta no se registra — el arqueo del día debe cuadrar desde
    # la primera venta. Solo si el tenant tiene la capacidad `caja` (default seguro ante misconfig).
    if (
        deps.caja_obligatoria is not None
        and ctx.tiene_capacidad("caja")
        and await deps.caja_obligatoria(ctx.tenant_id)
        and await deps.caja.actual(ctx.usuario_id, modo_empresa=True) is None
    ):
        return ErrorTool(
            "caja_no_abierta",
            "No hay caja abierta. Abre la caja con el efectivo actual (dashboard → Caja) y "
            "vuelve a intentar la venta.",
            recuperable=True,
        )
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
    except ClienteInexistente as exc:
        # Venta fiada: el cargo al ledger valida el cliente en la misma transacción.
        return ErrorTool("cliente_no_encontrado", str(exc), recuperable=True)
    except IdempotenciaConflicto as exc:
        return ErrorTool("idempotencia_conflicto", str(exc), recuperable=False)
    v = res.venta
    # Cierre fiscal de mostrador (ADR 0012 D2): este handler es la convergencia de TODO el canal del bot
    # (bypass, confirmación y modelo re-despachan aquí). Solo en venta NUEVA; idempotente y excluyente
    # con la FE (D1). Nunca rompe la venta (el puerto se traga sus fallos). Capacidades del Contexto.
    if not res.replay and deps.cierre_pos is not None:
        await deps.cierre_pos.cerrar(v.id, tenant_id=ctx.tenant_id, capacidades=ctx.capacidades)
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
        # `nombre` va en la data para el writer de memoria_entidades (último cliente mencionado, ADR 0024).
        data={"id": c.id, "nombre": c.nombre, "creado": res.creado},
        resumen=(f"Cliente {c.nombre} creado." if res.creado else f"El cliente {c.nombre} ya existía."),
    )


# --- Handlers de SOLO LECTURA (consulta) ------------------------------------
# No mutan: devuelven Resultado sin `evento` ni `idempotente`.
async def _consultar_ventas_dia(
    args: ConsultarVentasDiaArgs, ctx: Contexto, deps: Deps
) -> Resultado | ErrorTool:
    """Resumen de ventas de HOY (cantidad y total). Solo lectura.

    Scope RBAC: el vendedor solo ve las suyas (vendedor_id=ctx.usuario_id); admin/super_admin ven todas
    (vendedor_id=None). Sin ventas → conteo 0 y un resumen explícito.
    """
    vendedor_id = ctx.usuario_id if ctx.rol == "vendedor" else None
    ventas = await deps.ventas.listar_dia(vendedor_id=vendedor_id)
    if not ventas:
        return Resultado(
            data={"conteo": 0, "total": "0", "ventas": []},
            resumen="Hoy no hay ventas registradas.",
        )
    total = sum((v.total for v in ventas), Decimal("0"))
    etiqueta = "venta" if len(ventas) == 1 else "ventas"
    return Resultado(
        data={
            "conteo": len(ventas),
            "total": str(total),
            "ventas": [
                {"consecutivo": v.consecutivo, "total": str(v.total), "metodo_pago": v.metodo_pago}
                for v in ventas
            ],
        },
        resumen=f"Hoy hay {len(ventas)} {etiqueta} por ${total}.",
    )


# Token de TIPO en un nombre de producto (vinilo/cuñete por tipo): "Tipo 2" / "T2" / "T 2" → "2".
# El precio de estas familias depende del TIPO, no del color (docs/goal-mejoras-lija-vinilo.md, Bug 2);
# detectar el tipo permite colapsar la consulta de valor por tipo en vez de enumerar colores. Genérico
# (no nombra "vinilo"): cualquier familia que use la notación "Tipo N"/"TN" se beneficia; las que no
# llevan token de tipo siguen el camino de enumerar candidatos (no se tocan).
_RE_TIPO = re.compile(r"\b(?:tipo\s*|t\s?)(\d)\b")


def _token_tipo(nombre: str) -> str | None:
    """El dígito del tipo si el nombre lo declara ("...Tipo 2..."/"...T2..."), o None."""
    m = _RE_TIPO.search(nombre.lower())
    return m.group(1) if m else None


def _prefijo_comun(nombres: list[str]) -> str:
    """Prefijo común palabra-a-palabra de varios nombres ("Vinilo Davinci T1 Azul", "...T1 Negro"
    → "Vinilo Davinci T1"). Sirve de etiqueta del TIPO sin el color que los diferencia."""
    palabras = [n.split() for n in nombres]
    comun: list[str] = []
    for grupo in zip(*palabras):
        if len(set(grupo)) == 1:
            comun.append(grupo[0])
        else:
            break
    return " ".join(comun) or nombres[0]


def _detalle_fracciones(prod) -> str:
    if not prod.fracciones:
        return ""
    fracs = ", ".join(f"{fr.etiqueta} ${fr.precio_total}" for fr in prod.fracciones)
    return f" Fracciones: {fracs}."


def _resultado_producto(p) -> Resultado:
    """Sobre de un único producto resuelto: valor + unidad + fracciones (stock solo en data)."""
    return Resultado(
        data={
            "id": p.id, "nombre": p.nombre, "unidad_medida": p.unidad_medida,
            "precio": str(p.precio), "stock": str(p.stock),
            "fracciones": [
                {"etiqueta": fr.etiqueta, "precio_total": str(fr.precio_total)} for fr in p.fracciones
            ],
        },
        resumen=f"{p.nombre} ({p.unidad_medida}): ${p.precio}.{_detalle_fracciones(p)}",
    )


def _colapsar_por_tipo(nombre_consultado: str, matches: list) -> Resultado | None:
    """Consulta de valor de una familia por TIPO (vinilo/cuñetes): None si no aplica (→ enumerar).

    Solo actúa si TODOS los candidatos declaran un tipo (T1/T2/T3). Si comparten tipo y valor,
    responde ese valor SIN listar colores; si hay varios tipos, pregunta por TIPO (nunca por color).
    Un set con algún candidato sin tipo (p. ej. "Vinilo ICO") cae al camino de enumerar (devuelve None).
    """
    tipos = [_token_tipo(m.nombre) for m in matches]
    if any(t is None for t in tipos):
        return None
    grupos: dict[str, list] = {}
    for tipo, m in zip(tipos, matches):
        grupos.setdefault(tipo, []).append(m)

    if len(grupos) == 1:
        if len({m.precio for m in matches}) != 1:
            return None                              # mismo tipo pero valores distintos → enumerar
        rep = max(matches, key=lambda m: len(m.fracciones))   # el más completo para las fracciones
        etiqueta = _prefijo_comun([m.nombre for m in matches])
        return Resultado(
            data={
                "tipo": etiqueta, "precio": str(rep.precio), "unidad_medida": rep.unidad_medida,
                "fracciones": [
                    {"etiqueta": fr.etiqueta, "precio_total": str(fr.precio_total)}
                    for fr in rep.fracciones
                ],
                "candidatos": [{"id": m.id, "nombre": m.nombre} for m in matches],
            },
            resumen=f"{etiqueta} ({rep.unidad_medida}): ${rep.precio}.{_detalle_fracciones(rep)}",
        )

    # Varios tipos → preguntar por TIPO (no por color). Una opción por tipo, con su valor.
    opciones = []
    for tipo in sorted(grupos):
        ms = grupos[tipo]
        etiqueta = _prefijo_comun([m.nombre for m in ms])
        opciones.append({"tipo": tipo, "etiqueta": etiqueta, "precio": str(ms[0].precio)})
    texto = ", ".join(f"Tipo {o['tipo']} ${o['precio']}" for o in opciones)
    pregunta = "¿" + " o ".join(f"Tipo {o['tipo']}" for o in opciones) + "?"
    return Resultado(
        data={"opciones_por_tipo": opciones},
        resumen=f"El valor de «{nombre_consultado}» depende del tipo: {texto}. {pregunta}",
    )


async def _consultar_producto(
    args: ConsultarProductoArgs, ctx: Contexto, deps: Deps
) -> Resultado | ErrorTool:
    """Valor de un producto por su nombre. Solo lectura (espejo de `riel_producto`):

    0 coincidencias → ErrorTool recuperable; una → devuelve el valor (y sus fracciones). Varias: si
    son variantes de TIPO de la misma familia (vinilo/cuñetes), colapsa por tipo/valor (responde el
    valor sin listar colores, o pregunta por TIPO); si no, enumera los candidatos y pregunta cuál. El
    stock va solo en `data` (es una consulta de valor; en cero no debe sugerir que no se puede vender).
    """
    matches = await deps.ventas.buscar_producto_por_nombre(args.nombre)
    if not matches:
        return ErrorTool(
            "producto_no_encontrado",
            f"No encontré ningún producto para «{args.nombre}».",
            recuperable=True,
        )
    if len(matches) > 1:
        por_tipo = _colapsar_por_tipo(args.nombre, matches)
        if por_tipo is not None:
            return por_tipo
        nombres = ", ".join(m.nombre for m in matches)
        return Resultado(
            data={"candidatos": [{"id": m.id, "nombre": m.nombre} for m in matches]},
            resumen=f"Hay varios productos que coinciden con «{args.nombre}»: {nombres}. ¿Cuál?",
        )
    return _resultado_producto(matches[0])


async def _registrar_alias(args: RegistrarAliasArgs, ctx: Contexto, deps: Deps) -> Resultado | ErrorTool:
    """Enseña un alias de búsqueda al catálogo ("apréndete que 'la gruesa' es el tornillo 8x1").

    Reemplaza el `/alias` del bot viejo por una herramienta del agente: el modelo la invoca cuando el
    dueño/vendedor pide recordar una variante. Muta (crea/actualiza una fila en `aliases`); no toca
    stock ni caja, así que no pasa por rieles de producto/precio (no es una venta).
    """
    creado = await deps.ventas.registrar_alias(args.termino, args.reemplazo)
    verbo = "Aprendí" if creado else "Actualicé"
    return Resultado(
        data={"termino": args.termino.strip().lower(), "reemplazo": args.reemplazo.strip(),
              "creado": creado},
        resumen=f"{verbo} que «{args.termino}» es «{args.reemplazo}».",
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
        args_model=RegistrarVentaArgs, rol_min="vendedor", feature="ventas",
        handler=_registrar_venta, valida_productos=True,
    ),
    Tool(
        nombre="registrar_gasto",
        descripcion="Registra un gasto (egreso de caja). Requiere caja abierta.",
        args_model=RegistrarGastoArgs, rol_min="vendedor", feature="caja",
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
    Tool(
        nombre="consultar_ventas_dia",
        descripcion="Consulta el resumen de ventas de hoy (cantidad y total). Solo lectura.",
        args_model=ConsultarVentasDiaArgs, rol_min="vendedor", feature="ventas",
        handler=_consultar_ventas_dia,   # read-only: valida_productos/confirmable = False (defaults)
    ),
    Tool(
        nombre="consultar_producto",
        descripcion="Consulta el precio y el stock de un producto por su nombre. Solo lectura.",
        args_model=ConsultarProductoArgs, rol_min="vendedor", feature="ventas",
        handler=_consultar_producto,     # read-only: valida_productos/confirmable = False (defaults)
    ),
    Tool(
        # Aprender un alias/typo del catálogo ("la gruesa" = "tornillo 8x1"). Solo admin: cambia cómo
        # busca TODO el tenant, no debe quedar en manos de cualquier vendedor.
        nombre="registrar_alias",
        descripcion=("Enseña un alias de búsqueda cuando el usuario pide recordar que una palabra o "
                     "typo se refiere a un producto (ej: 'apréndete que la gruesa es el tornillo 8x1')."),
        args_model=RegistrarAliasArgs, rol_min="admin", feature="ventas",
        handler=_registrar_alias,        # muta aliases; no es venta → sin rieles de producto/precio
    ),
)

POR_NOMBRE: dict[str, Tool] = {t.nombre: t for t in CATALOGO}
