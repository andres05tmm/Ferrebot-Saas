"""Despachador único de la capa IA (ADR 0005, decisión c). Agnóstico de proveedor.

Es el dueño de los rieles, el RBAC, las capacidades y la idempotencia. El modelo solo decide qué
herramienta llamar; este módulo es quien valida y ejecuta contra el servicio de dominio (el MISMO
que usa el bypass y la API REST). Alcance de esta fase: el núcleo determinista y testeable —

  - `exponer_catalogo(ctx)` → qué herramientas ve el modelo (filtradas por rol + capacidades).
  - `ejecutar(tool_call, ctx, recursos)` → una ejecución: RBAC → capacidad → validación de args →
     rieles → handler → estado de idempotencia.
  - `seleccionar_proveedor(empresa_id, turno)` → resuelve el LLM vía `get_llm` (proveedor/modelo/key).

El bucle del agente (generate → tool_result → re-prompt → respuesta en lenguaje natural) y el
transporte de Telegram NO van aquí: son fase del bot. Una sola ronda de tool-calls, tope duro 1:
los rieles `Preguntar`/`Confirmar` cortan y vuelven al usuario; la confirmación llega como un turno
nuevo reusando la misma `idempotency_key` (no duplica).
"""
from dataclasses import dataclass, field
from decimal import Decimal

from pydantic import ValidationError

from ai.envelope import Contexto, ErrorTool, Resultado
from ai.limites import Escalar, LimitesEmpresa, PedirConfirmacion, Permitir, evaluar_venta
from ai.ports import CatalogoPrecios, ProductoCatalogo, UmbralesStore
from ai.saneamiento import revisar as revisar_entrada
from ai.rieles import (
    Confirmar,
    ItemPrecio,
    ItemResuelto,
    Preguntar,
    riel_confirmacion,
    riel_precio,
    riel_producto,
)
from ai.tools import CATALOGO, POR_NOMBRE, Deps, RegistrarVentaArgs, Tool
from core.auth.rbac import satisface
from core.llm.base import ToolCall
from core.llm.factory import (
    ConfigStore, KeyStore, LLMResuelto, PlataformaLLM, Turno, get_llm_con_fallback,
)
from core.logging import get_logger
from core.money import cuantizar
from modules.inventario.precios import obtener_precio_para_cantidad

log = get_logger("ai.dispatcher")

# Respuesta del despachador: éxito/fallo de herramienta o un corte conversacional del riel/límite.
Respuesta = Resultado | ErrorTool | Preguntar | Confirmar

# Código de error del envelope cuando un límite por empresa exige un rol superior (no recuperable).
_ERROR_LIMITE = "limite_excedido"
_CENT = Decimal("0.01")


@dataclass(frozen=True, slots=True)
class Recursos:
    """Lo que el despachador necesita del tenant para una ejecución (atado a su sesión).

    `resueltos` es un mapa producto_id → producto **por-turno y fresco** (default_factory crea uno
    nuevo por instancia): el bypass deposita ahí el producto que ya resolvió para que R1 no relea
    Postgres (decisión #5b). NO debe compartirse ni sobrevivir entre turnos —un producto stale haría
    que R1 decida mal—; el composition root construye un `Recursos` nuevo en cada turno.
    """

    deps: Deps                  # servicios de dominio (venta/caja/fiados/clientes)
    catalogo: CatalogoPrecios   # resolución de precios para los rieles
    umbrales: UmbralesStore     # umbrales por empresa (config_empresa)
    resueltos: dict[int, ProductoCatalogo] = field(default_factory=dict)  # pre-cargados por-turno


class Dispatcher:
    """Único punto de ejecución de herramientas IA. Sin estado de tenant: lo recibe por llamada."""

    def __init__(
        self, *, config_store: ConfigStore, key_store: KeyStore, plataforma: PlataformaLLM
    ) -> None:
        self._config_store = config_store
        self._key_store = key_store
        self._plataforma = plataforma

    # --- Selección de proveedor (proveedor/modelo/key por empresa y turno) ----
    async def seleccionar_proveedor(
        self, empresa_id: int, *, turno: Turno = Turno.WORKER
    ) -> LLMResuelto:
        # Con resiliencia (ADR 0023): retry ante transitorios + respaldo si está configurado.
        return await get_llm_con_fallback(
            empresa_id, turno=turno, config_store=self._config_store,
            key_store=self._key_store, plataforma=self._plataforma,
        )

    # --- Catálogo expuesto al modelo (RBAC + capacidades) ---------------------
    def exponer_catalogo(self, ctx: Contexto) -> list:
        """Solo las herramientas que el rol alcanza y la empresa tiene habilitadas (ADR 0005)."""
        return [t.spec for t in catalogo_visible(ctx)]

    # --- Ejecución de una herramienta (rieles incluidos) ----------------------
    async def ejecutar(self, tool_call: ToolCall, ctx: Contexto, recursos: Recursos) -> Respuesta:
        # Saneamiento de entrada (capa LIGERA previa, agnóstica de la herramienta): texto desmesurado,
        # caracteres de control, inyección de instrucciones y números absurdos. Antes de resolver nada.
        motivo = revisar_entrada(tool_call.arguments)
        if motivo is not None:
            log.warning(
                "entrada_rechazada", tenant_id=ctx.tenant_id, tool=tool_call.name,
                motivo=motivo.detalle, recuperable=motivo.recuperable,
            )
            return ErrorTool("validacion", motivo.detalle, recuperable=motivo.recuperable)

        tool = POR_NOMBRE.get(tool_call.name)
        if tool is None:
            return ErrorTool("error_interno", f"Herramienta desconocida: {tool_call.name}")

        # RBAC y capacidades: defensa en profundidad (además del filtrado de exponer_catalogo).
        if not satisface(ctx.rol, tool.rol_min):
            return ErrorTool("permiso_denegado", f"{tool.nombre} requiere rol {tool.rol_min}")
        if not ctx.tiene_capacidad(tool.feature):
            return ErrorTool("capacidad_no_habilitada", f"{tool.nombre} no está habilitada")

        # Validación de args (Pydantic estricta). El detalle crudo (echo de valores, rutas de campos)
        # NO vuelve al modelo/usuario —puede filtrar internals—: mensaje genérico + detalle al log.
        try:
            args = tool.args_model(**tool_call.arguments)
        except ValidationError as exc:
            log.info("args_invalidos", tenant_id=ctx.tenant_id, tool=tool.nombre, detalle=str(exc))
            return ErrorTool("validacion", "Argumentos inválidos para la herramienta.", recuperable=True)

        # Rieles ANTES de ejecutar (no muta nada si cortan).
        corte = await self._rieles(tool, args, ctx, recursos)
        if corte is not None:
            return corte

        return await tool.handler(args, ctx, recursos.deps)

    # --- Rieles + política de límites -----------------------------------------
    async def _rieles(
        self, tool: Tool, args, ctx: Contexto, recursos: Recursos
    ) -> Preguntar | Confirmar | ErrorTool | None:
        if tool.valida_productos:
            corte = await self._rieles_venta(args, ctx, recursos)
            if corte is not None:
                return corte
        if tool.confirmable:
            umbrales = await recursos.umbrales.cargar(ctx.tenant_id)
            decision = riel_confirmacion(
                requiere=umbrales.confirmar_mutaciones,
                confirmado=ctx.confirmado,
                resumen=_resumen_confirmacion(tool.nombre, args),
            )
            if isinstance(decision, Confirmar):
                return decision
        return None

    async def _rieles_venta(
        self, args: RegistrarVentaArgs, ctx: Contexto, recursos: Recursos
    ) -> Preguntar | Confirmar | ErrorTool | None:
        """R1 (producto), R2 (precio) y la POLÍTICA DE LÍMITES (monto/descuento) sobre la venta.

        Resuelve los productos UNA sola vez (cache) y comparte esa resolución entre los rieles y los
        límites (decisión #5b: no relee Postgres). Los rieles viven en ai.rieles y los límites en
        ai.limites —ambos puros—; este método solo orquesta y traduce la decisión al envelope.
        """
        cache: dict[int, ProductoCatalogo | None] = {}
        items_r1: list[ItemResuelto] = []
        for it in args.items:
            if it.producto_id is None:           # venta varia: ítem libre, no toca catálogo
                continue
            # Decisión #5b: si el bypass ya resolvió el producto (recursos.resueltos), no se relee
            # Postgres en el camino caliente; el modelo (sin pre-carga) sí cae al catálogo.
            prod = recursos.resueltos.get(it.producto_id)
            if prod is None:
                prod = await recursos.catalogo.obtener(it.producto_id)
            cache[it.producto_id] = prod
            referencia = prod.nombre if prod is not None else f"producto {it.producto_id}"
            # Venta por producto_id: 0 candidatos (no resuelto/inactivo) o 1 (el nombre del producto).
            candidatos = (prod.nombre,) if (prod is not None and prod.activo) else ()
            items_r1.append(ItemResuelto(referencia, candidatos))

        decision = riel_producto(items_r1)
        if isinstance(decision, Preguntar):
            return decision

        # Una sola lectura de la config de la empresa, reusada por R2 (tolerancia) y por los límites.
        umbrales = await recursos.umbrales.cargar(ctx.tenant_id)

        items_precio: list[ItemPrecio] = []
        for it in args.items:
            if it.producto_id is None or it.precio_unitario is None:
                continue
            prod = cache[it.producto_id]
            if prod is None:                      # ya cubierto por R1; defensa
                continue
            total_catalogo, _ = obtener_precio_para_cantidad(prod.esquema, it.cantidad)
            total_modelo = cuantizar(it.precio_unitario * it.cantidad)
            items_precio.append(
                ItemPrecio(prod.nombre, total_modelo, total_catalogo, it.precio_dicho_por_usuario)
            )

        if items_precio:
            decision = riel_precio(
                items_precio,
                tolerancia_pct=umbrales.precio_tolerancia_pct,
                tolerancia_min=umbrales.precio_tolerancia_min,
            )
            if isinstance(decision, Preguntar):
                return decision

        # Política de límites por empresa (monto de venta + % de descuento): capa separada (ai.limites).
        return self._limites_venta(args, cache, ctx, umbrales.limites)

    def _limites_venta(
        self,
        args: RegistrarVentaArgs,
        cache: dict[int, ProductoCatalogo | None],
        ctx: Contexto,
        limites: LimitesEmpresa,
    ) -> Confirmar | ErrorTool | None:
        """Aplica los límites de la empresa al monto/descuento de la venta (decisión en ai.limites).

        Reusa la resolución de productos (`cache`) para estimar el total y el descuento máximo —no es el
        cálculo fiscal, que vive en el servicio—. Devuelve un corte (Confirmar/ErrorTool) o None.
        """
        if not limites.activos:                  # sin topes configurados: ni se evalúa
            return None
        total, descuento_pct = _montos_venta(args, cache)
        decision = evaluar_venta(
            total=total, descuento_pct=descuento_pct, limites=limites,
            rol=ctx.rol, confirmado=ctx.confirmado,
        )
        if isinstance(decision, Permitir):
            return None
        if isinstance(decision, Escalar):
            log.info(
                "limite_venta_escalado", tenant_id=ctx.tenant_id, rol=ctx.rol,
                rol_requerido=decision.rol_requerido, total=str(total),
                descuento_pct=str(descuento_pct), motivos=list(decision.motivos),
            )
            return ErrorTool(_ERROR_LIMITE, decision.detalle, recuperable=False)
        if isinstance(decision, PedirConfirmacion):
            # Corta y pide un "sí" (mismo turno reusa la idempotency_key → no duplica).
            log.info(
                "limite_venta_confirmacion", tenant_id=ctx.tenant_id, rol=ctx.rol,
                total=str(total), descuento_pct=str(descuento_pct), motivos=list(decision.motivos),
            )
            return Confirmar(decision.resumen)
        return None


def _montos_venta(
    args: RegistrarVentaArgs, cache: dict[int, ProductoCatalogo | None]
) -> tuple[Decimal, Decimal]:
    """(total de la venta, % de descuento máximo de una línea) para la política de límites.

    Estimación con el MISMO motor de precios que los rieles (no es el cálculo fiscal: ese vive en el
    servicio). El descuento de una línea es cuánto baja el precio efectivo frente al de catálogo (0 si
    no hay override o si sube). Reusa la `cache` ya resuelta por los rieles: no agrega lecturas.
    """
    total = Decimal("0")
    descuento_max = Decimal("0")
    for it in args.items:
        if it.producto_id is None:               # venta varia: precio obligatorio (ya validado)
            if it.precio_unitario is not None:
                total += cuantizar(it.precio_unitario * it.cantidad)
            continue
        prod = cache.get(it.producto_id)
        if prod is None:                          # cubierto por R1; defensa
            continue
        total_catalogo, _ = obtener_precio_para_cantidad(prod.esquema, it.cantidad)
        efectivo = (
            cuantizar(it.precio_unitario * it.cantidad)
            if it.precio_unitario is not None else total_catalogo
        )
        total += efectivo
        if total_catalogo > 0 and efectivo < total_catalogo:
            pct = (total_catalogo - efectivo) / total_catalogo * Decimal("100")
            descuento_max = max(descuento_max, pct)
    return cuantizar(total), descuento_max.quantize(_CENT)


def _resumen_confirmacion(nombre: str, args) -> str:
    if nombre == "registrar_gasto":
        return f"Registrar gasto de ${args.monto} en {args.categoria}. ¿Confirmo?"
    if nombre == "registrar_fiado":
        return f"Registrar fiado de ${args.monto} al cliente {args.cliente_id}. ¿Confirmo?"
    if nombre == "abonar_fiado":
        return f"Registrar abono de ${args.monto} al fiado {args.fiado_id}. ¿Confirmo?"
    return "¿Confirmo la operación?"


def catalogo_visible(ctx: Contexto) -> list[Tool]:
    """Herramientas que el rol alcanza y la empresa tiene habilitadas (filtro de exposición)."""
    return [
        t for t in CATALOGO
        if satisface(ctx.rol, t.rol_min) and ctx.tiene_capacidad(t.feature)
    ]
