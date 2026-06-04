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
from dataclasses import dataclass

from pydantic import ValidationError

from ai.envelope import Contexto, ErrorTool, Resultado
from ai.ports import CatalogoPrecios, ProductoCatalogo, UmbralesStore
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
from core.llm.factory import ConfigStore, KeyStore, LLMResuelto, PlataformaLLM, Turno, get_llm
from core.money import cuantizar
from modules.inventario.precios import obtener_precio_para_cantidad

# Respuesta del despachador: éxito/fallo de herramienta o un corte conversacional del riel.
Respuesta = Resultado | ErrorTool | Preguntar | Confirmar


@dataclass(frozen=True, slots=True)
class Recursos:
    """Lo que el despachador necesita del tenant para una ejecución (atado a su sesión)."""

    deps: Deps                  # servicios de dominio (venta/caja/fiados/clientes)
    catalogo: CatalogoPrecios   # resolución de precios para los rieles
    umbrales: UmbralesStore     # umbrales por empresa (config_empresa)


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
        return await get_llm(
            empresa_id, turno=turno, config_store=self._config_store,
            key_store=self._key_store, plataforma=self._plataforma,
        )

    # --- Catálogo expuesto al modelo (RBAC + capacidades) ---------------------
    def exponer_catalogo(self, ctx: Contexto) -> list:
        """Solo las herramientas que el rol alcanza y la empresa tiene habilitadas (ADR 0005)."""
        return [t.spec for t in catalogo_visible(ctx)]

    # --- Ejecución de una herramienta (rieles incluidos) ----------------------
    async def ejecutar(self, tool_call: ToolCall, ctx: Contexto, recursos: Recursos) -> Respuesta:
        tool = POR_NOMBRE.get(tool_call.name)
        if tool is None:
            return ErrorTool("error_interno", f"Herramienta desconocida: {tool_call.name}")

        # RBAC y capacidades: defensa en profundidad (además del filtrado de exponer_catalogo).
        if not satisface(ctx.rol, tool.rol_min):
            return ErrorTool("permiso_denegado", f"{tool.nombre} requiere rol {tool.rol_min}")
        if not ctx.tiene_capacidad(tool.feature):
            return ErrorTool("capacidad_no_habilitada", f"{tool.nombre} no está habilitada")

        # Validación de args (Pydantic). Argumentos inválidos del modelo → recuperable.
        try:
            args = tool.args_model(**tool_call.arguments)
        except ValidationError as exc:
            return ErrorTool("validacion", str(exc), recuperable=True)

        # Rieles ANTES de ejecutar (no muta nada si cortan).
        corte = await self._rieles(tool, args, ctx, recursos)
        if corte is not None:
            return corte

        return await tool.handler(args, ctx, recursos.deps)

    # --- Rieles ---------------------------------------------------------------
    async def _rieles(
        self, tool: Tool, args, ctx: Contexto, recursos: Recursos
    ) -> Preguntar | Confirmar | None:
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
    ) -> Preguntar | None:
        """R1 (producto desconocido/ambiguo) y R2 (precio dudoso) sobre los ítems con catálogo."""
        resueltos: dict[int, ProductoCatalogo | None] = {}
        items_r1: list[ItemResuelto] = []
        for it in args.items:
            if it.producto_id is None:           # venta varia: ítem libre, no toca catálogo
                continue
            prod = await recursos.catalogo.obtener(it.producto_id)
            resueltos[it.producto_id] = prod
            referencia = prod.nombre if prod is not None else f"producto {it.producto_id}"
            candidatos = 1 if (prod is not None and prod.activo) else 0
            items_r1.append(ItemResuelto(referencia, candidatos))

        decision = riel_producto(items_r1)
        if isinstance(decision, Preguntar):
            return decision

        items_precio: list[ItemPrecio] = []
        for it in args.items:
            if it.producto_id is None or it.precio_unitario is None:
                continue
            prod = resueltos[it.producto_id]
            if prod is None:                      # ya cubierto por R1; defensa
                continue
            total_catalogo, _ = obtener_precio_para_cantidad(prod.esquema, it.cantidad)
            total_modelo = cuantizar(it.precio_unitario * it.cantidad)
            items_precio.append(
                ItemPrecio(prod.nombre, total_modelo, total_catalogo, it.precio_dicho_por_usuario)
            )

        if items_precio:
            tol = await recursos.umbrales.cargar(ctx.tenant_id)
            decision = riel_precio(
                items_precio,
                tolerancia_pct=tol.precio_tolerancia_pct,
                tolerancia_min=tol.precio_tolerancia_min,
            )
            if isinstance(decision, Preguntar):
                return decision
        return None


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
