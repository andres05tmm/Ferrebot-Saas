"""Corpus de evaluación del agente: frases reales de mostrador → herramienta + args esperados.

Tres datasets, cada uno apuntando a un plano distinto del runtime híbrido (bypass + function calling):

  1. `PARSEO`    → `ai.bypass.analizar` (función PURA, sin catálogo): texto → intención de venta o
                   `CaeAlModelo(motivo)`. Mide normalización de slug (typos de cantidad, plurales),
                   parsing de fracciones (`1/2`, `1-1/2`, "medio") y los gates que desactivan el bypass.
  2. `DESPACHO`  → `ai.bypass.Bypass.intentar` contra un catálogo fijo: texto → `ToolCall`
                   `registrar_venta` con `items=[{producto_id, cantidad}]` EXACTOS, o deferido al modelo
                   (typo de producto, fracción inexistente, consulta, cliente). El escalonado/mayorista
                   por umbral lo resuelve el motor de precios y YA NO se difiere. Este es el gate de
                   precisión de la llamada del camino determinista (~60 % de las ventas).
  3. `CONTRATO`  → `ai.dispatcher.Dispatcher.ejecutar` con un `ToolCall` "gold" (el que el MODELO
                   debería emitir para intenciones que el bypass NO maneja: gasto/fiado con montos
                   coloquiales como "20mil"). Verifica el CONTRATO de la función (la herramienta existe,
                   los args validan, el RBAC/capacidad/confirmación cortan donde deben). El mapeo
                   lenguaje-natural → args lo hace el LLM en vivo; su exactitud se mide aparte (evals
                   con claves de proveedor), no aquí, porque en CI las claves van vacías.

Cada frase lleva `tags` para poder filtrar/segmentar resultados (typo, fraccion, mixta, unidad,
plural, coloquial, consulta, cliente, modificacion, escalonado, multiproducto).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal


# ============================ 1) PARSEO (analizar) ============================
@dataclass(frozen=True)
class CasoParseo:
    frase: str
    # Si es venta simple: slug normalizado + componentes de cantidad (entero/fracción).
    producto: str | None
    componentes: tuple[Decimal, ...] | None
    # Si cae al modelo: el motivo exacto que reporta `CaeAlModelo`.
    motivo: str | None
    tags: tuple[str, ...] = ()

    @property
    def es_venta(self) -> bool:
        return self.producto is not None


def _venta(frase, producto, componentes, tags=()) -> CasoParseo:
    return CasoParseo(frase, producto, tuple(Decimal(c) for c in componentes), None, tags)


def _modelo(frase, motivo, tags=()) -> CasoParseo:
    return CasoParseo(frase, None, None, motivo, tags)


PARSEO: tuple[CasoParseo, ...] = (
    # --- ventas simples: cantidad + producto ---
    _venta("3 vinilo", "vinilo", ["3"], ("entero",)),
    _venta("12 puntillas", "puntilla", ["12"], ("entero", "plural")),
    _venta("10 tornillos", "tornillo", ["10"], ("entero", "plural")),
    # --- fracciones ---
    _venta("1/2 manguera", "manguera", ["0.5"], ("fraccion",)),
    _venta("2 1/2 manguera", "manguera", ["2", "0.5"], ("fraccion", "mixta")),
    _venta("1-1/2 cemento", "cemento", ["1", "0.5"], ("fraccion", "mixta")),
    _venta("medio vinilo", "vinilo", ["0.5"], ("fraccion", "escrita")),
    _venta("tres cuartos lija", "lija", ["0.75"], ("fraccion", "escrita")),
    _venta("1 y medio aceite", "aceite", ["1", "0.5"], ("fraccion", "mixta", "escrita")),
    # --- unidad de empaque (al parsear queda en el slug; se quita al resolver catálogo) ---
    _venta("2 galones de thinner", "galon de thinner", ["2"], ("unidad",)),
    # --- gates que desactivan el bypass ---
    _modelo("cuanto vale el vinilo", "consulta", ("consulta",)),
    _modelo("anota un gasto de 20mil en transporte", "consulta", ("consulta", "coloquial", "gasto")),
    _modelo("2 vinilo fiado", "cliente_credito", ("cliente",)),
    _modelo("abono de 20mil al saldo de Juan", "cliente_credito", ("cliente", "coloquial")),
    _modelo("factura los 3 vinilo", "cliente_credito", ("cliente",)),
    _modelo("2 vinilo para Pedro", "cliente_nombre", ("cliente",)),
    # "precio" es token de consulta y se evalúa ANTES que el de modificación; para aislar el gate de
    # modificación se usa una frase sin tokens de consulta/cliente.
    _modelo("quita el vinilo", "modificacion", ("modificacion",)),
    _modelo("2 vinilo, 3 puntillas", "multiproducto", ("multiproducto",)),
    _modelo("hola buenas", "no_parseable", ("ruido",)),
    _modelo("   ", "vacio", ("ruido",)),
)


# ========================= 2) DESPACHO (Bypass.intentar) ======================
@dataclass(frozen=True)
class CasoDespacho:
    frase: str
    # Venta esperada: lista de (producto_id, cantidad) que debe llevar el ToolCall registrar_venta.
    # `None` ⇒ el bypass NO maneja la frase y debe deferir al modelo (intentar → None).
    items: tuple[tuple[int, Decimal], ...] | None
    tags: tuple[str, ...] = ()

    @property
    def es_venta(self) -> bool:
        return self.items is not None


def _ev(frase, items, tags=()) -> CasoDespacho:
    return CasoDespacho(frase, tuple((i, Decimal(c)) for i, c in items), tags)


def _df(frase, tags=()) -> CasoDespacho:
    return CasoDespacho(frase, None, tags)


# IDs según el catálogo fijo en `_harness.py`: vinilo=7, manguera=8, puntilla=9, tornillos caja=10,
# cemento=11 (escalonado), lija=12 (sin fracc.), thinner=13, drywall=14.
DESPACHO: tuple[CasoDespacho, ...] = (
    # --- happy path: herramienta + args exactos ---
    _ev("3 vinilo", [(7, "3")], ("entero",)),
    _ev("12 puntillas", [(9, "12")], ("entero", "plural")),
    _ev("1/2 manguera", [(8, "0.5")], ("fraccion",)),
    _ev("2 1/2 manguera", [(8, "2"), (8, "0.5")], ("fraccion", "mixta")),
    _ev("4 tornillos caja", [(10, "4")], ("entero",)),
    _ev("2 galones de thinner", [(13, "2")], ("unidad",)),   # quita unidad de empaque y resuelve
    _ev("3 cemento", [(11, "3")], ("escalonado",)),          # mayorista por umbral: lo resuelve el motor
    # --- deferidos al modelo (el bypass NO adivina) ---
    _df("3 vnilo", ("typo",)),                  # typo de producto → sin match exacto
    _df("3 drywal", ("typo",)),                 # typo de "drywall"
    _df("5 martillo", ("no_existe",)),          # producto ausente del catálogo
    _df("1/4 lija", ("fraccion", "no_catalogo")),  # fracción no configurada en el producto
    _df("cuanto vale el vinilo", ("consulta",)),
    _df("2 vinilo fiado", ("cliente",)),
)


# ===================== 3) CONTRATO (Dispatcher.ejecutar) ======================
@dataclass(frozen=True)
class CasoContrato:
    descripcion: str
    frase_origen: str              # la frase de mostrador que el modelo traduciría a este ToolCall
    tool: str
    args: dict
    # Contexto del turno (algunas herramientas exigen capacidad/confirmación).
    confirmado: bool = False
    capacidades: frozenset[str] = frozenset()
    # Resultado esperado del despachador: "resultado" | "confirmar" | "error".
    espera: str = "resultado"
    evento: str | None = None      # si espera=resultado y la herramienta muta
    codigo_error: str | None = None  # si espera=error
    tags: tuple[str, ...] = ()


CONTRATO: tuple[CasoContrato, ...] = (
    # "20mil" → 20000: el modelo interpreta el monto coloquial; el despachador ejecuta el gasto.
    CasoContrato(
        descripcion="gasto con monto coloquial 20mil, confirmado",
        frase_origen="anota un gasto de 20mil en transporte",
        tool="registrar_gasto",
        args={"categoria": "transporte", "monto": Decimal("20000"), "concepto": None},
        confirmado=True, espera="resultado", evento="gasto_registrado",
        tags=("coloquial", "gasto"),
    ),
    CasoContrato(
        descripcion="gasto sin confirmar → R3 corta (Confirmar)",
        frase_origen="gasto de 20mil en transporte",
        tool="registrar_gasto",
        args={"categoria": "transporte", "monto": Decimal("20000")},
        confirmado=False, espera="confirmar",
        tags=("gasto", "riel"),
    ),
    CasoContrato(
        descripcion="venta gold del modelo (producto_id + método) ejecuta",
        frase_origen="3 vinilo de contado",
        tool="registrar_venta",
        args={"items": [{"producto_id": 7, "cantidad": Decimal("3")}], "metodo_pago": "efectivo"},
        espera="resultado", evento="venta_registrada",
        tags=("venta",),
    ),
    # fiado: la capacidad "fiados" debe estar habilitada (corta ANTES del handler).
    CasoContrato(
        descripcion="fiado sin la capacidad habilitada → capacidad_no_habilitada",
        frase_origen="fiale 50mil a Pedro",
        tool="registrar_fiado",
        args={"cliente_id": 1, "monto": Decimal("50000")},
        capacidades=frozenset(), espera="error", codigo_error="capacidad_no_habilitada",
        tags=("fiado", "capacidad"),
    ),
    CasoContrato(
        descripcion="fiado con capacidad pero sin confirmar → R3 corta",
        frase_origen="fiale 50mil a Pedro",
        tool="registrar_fiado",
        args={"cliente_id": 1, "monto": Decimal("50000")},
        capacidades=frozenset({"fiados"}), confirmado=False, espera="confirmar",
        tags=("fiado", "capacidad", "riel"),
    ),
    # --- contrato negativo: args malformados del modelo → validacion recuperable ---
    CasoContrato(
        descripcion="venta sin items → validacion",
        frase_origen="(args inválidos del modelo)",
        tool="registrar_venta",
        args={"items": [], "metodo_pago": "efectivo"},
        espera="error", codigo_error="validacion",
        tags=("negativo", "validacion"),
    ),
    CasoContrato(
        descripcion="gasto con monto 0 → validacion",
        frase_origen="(args inválidos del modelo)",
        tool="registrar_gasto",
        args={"categoria": "transporte", "monto": Decimal("0")},
        confirmado=True, espera="error", codigo_error="validacion",
        tags=("negativo", "validacion"),
    ),
    CasoContrato(
        descripcion="herramienta inexistente → error_interno",
        frase_origen="(alucinación del modelo)",
        tool="borrar_todo",
        args={},
        espera="error", codigo_error="error_interno",
        tags=("negativo", "alucinacion"),
    ),
)
