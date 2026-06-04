# Abstracción de proveedor de IA (`core/llm/`)

> Concreta la **decisión (b) del ADR 0005** (híbrido agnóstico de proveedor).
> Objetivo: cambiar la IA que alimenta el agente debe ser **configuración, no reescritura**.
> Estado: **implementado** en Fase 4 (capa IA), con TDD, antes de cablear herramientas y rieles.

> **Convención de idioma (excepción acotada).** Esta capa usa **vocabulario de vendor en inglés**
> a propósito (`Message`, `ToolSpec`, `ToolCall`, `LLMResponse`, `generate`, `role/content/
> parameters/arguments`): espeja los SDKs de Anthropic/OpenAI y minimiza la traducción en los
> adaptadores. Es una excepción **solo** para `core/llm/`; los módulos de dominio (`modules/…`)
> siguen en español.

---

## 1. Principio

El agente **nunca** habla con el SDK de Claude ni con el de OpenAI directamente. Habla con una
**interfaz única** (`LLMProvider`). Cada proveedor (Claude, OpenAI, Gemini, …) es una
implementación intercambiable de esa interfaz.

El despachador (`ai/dispatcher`, fase siguiente) pide *"dame el proveedor de esta empresa"*, recibe
la interfaz, le pasa el catálogo de herramientas en **formato canónico** y recibe una respuesta
**canónica** (`LLMResponse` con `ToolCall`s). Quién esté detrás es invisible para el resto del código.

```
ai/dispatcher ──► LLMProvider (Protocol) ──► ClaudeProvider | OpenAIProvider | …
                       ▲
                       │  factory.get_llm(empresa_id, turno)  ← proveedor + modelo + key
```

Cambiar de IA = girar una variable. Agregar un proveedor = un archivo nuevo + una línea en el
registry. El despachador, las herramientas y los rieles de validación **no se tocan**.

---

## 2. Estructura

```
core/llm/
  base.py          # Protocol LLMProvider + tipos canónicos + errores
  registry.py      # nombre → clase  ({"claude": ClaudeProvider, "openai": OpenAIProvider})
  factory.py       # get_llm(empresa_id, turno) → resuelve proveedor + modelo + key
  stores.py        # ConfigStore/KeyStore respaldados por el control DB
  providers/
    claude.py      # ToolSpec → tools de Anthropic; tool_use → ToolCall
    openai.py      # ToolSpec → functions de OpenAI; tool_calls → ToolCall
```

Cada `providers/*.py` hace **solo dos cosas**: (1) traducir el catálogo canónico
(`list[ToolSpec]`) al formato de function-calling del vendor; (2) normalizar la respuesta del
vendor de vuelta a `LLMResponse`/`ToolCall`. Eso es lo único específico de proveedor en el
proyecto. RBAC, idempotencia y rieles viven en el despachador, una sola vez.

---

## 3. Tipos canónicos y Protocol (`core/llm/base.py`)

```python
@dataclass(frozen=True, slots=True)
class Message:
    role: str                           # system | user | assistant | tool
    content: str
    tool_call_id: str | None = None     # respuesta de una herramienta (role=tool)
    name: str | None = None

@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]          # JSON Schema de los argumentos

@dataclass(frozen=True, slots=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]           # ya normalizado a dict (OpenAI manda string JSON)

@dataclass(frozen=True, slots=True)
class LLMResponse:
    text: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    model: str | None = None
    stop_reason: str | None = None
    usage: dict[str, int] = field(default_factory=dict)   # tokens in/out para costos
    raw: Any = None

class LLMProvider(Protocol):
    nombre: str
    api_key: str
    async def generate(
        self, *, messages: list[Message], tools: list[ToolSpec], model: str,
        system: str | None = ..., **kwargs: Any,
    ) -> LLMResponse: ...
```

Errores: `ProveedorDesconocido` (registry), `LLMSinCredencial` (no hay key ni por empresa ni de
plataforma). El despachador depende **solo** de este módulo.

---

## 4. Registry — agregar un proveedor es una línea

```python
# core/llm/registry.py
_PROVIDERS: dict[str, type[LLMProvider]] = {
    "claude": ClaudeProvider,
    "openai": OpenAIProvider,
    # "gemini": GeminiProvider,   ← proveedor nuevo: 1 import + 1 línea
}

def registrar(nombre, clase) -> None: ...
def obtener_clase(nombre) -> type[LLMProvider]:   # ProveedorDesconocido si no está
def proveedores() -> tuple[str, ...]: ...
```

---

## 5. Resolución de configuración y secretos (`core/llm/factory.py` + `stores.py`)

| Qué | Dónde vive | Precedencia |
|---|---|---|
| **Proveedor** (`claude`/`openai`/…) | `config_empresa` por tenant → default de plataforma (`.env`) | empresa gana |
| **Modelo** (worker / orquestador) | `config_empresa` por tenant → default de plataforma (`.env`) | empresa gana |
| **API key** (secreto) | `secretos_empresa` cifrado por empresa → `.env` de plataforma | empresa gana |

```python
# core/llm/factory.py
class Turno(str, Enum):
    WORKER = "worker"            # frecuente, modelo barato (el ~40% que el bypass no resuelve)
    ORQUESTADOR = "orquestador"  # multi-paso / desambiguación, modelo capaz

async def get_llm(
    empresa_id, *, turno=Turno.WORKER, config_store, key_store, plataforma,
) -> LLMResuelto:                # (provider instanciado con su key, model, provider_nombre)
    ...
```

- `config_store.overrides(empresa_id)` lee `config_empresa` (claves `llm_provider`,
  `llm_model_worker`, `llm_model_orquestador`); si falta, cae al default de plataforma.
- `key_store.api_key(empresa_id, provider)` descifra de `secretos_empresa`
  (`claude→anthropic_api_key`, `openai→openai_api_key`); si falta, usa la del `.env`. Si no hay
  ninguna → `LLMSinCredencial` (jamás se hardcodea).
- El factory depende de los **puertos** `ConfigStore`/`KeyStore` (Protocols), no del control DB →
  testeable con fakes. Las implementaciones reales (`ControlLLMConfigStore`,
  `ControlLLMKeyStore`) viven en `core/llm/stores.py`.

### Variables de plataforma (`.env`)

```bash
LLM_PROVIDER=openai             # claude | openai | …
LLM_MODEL_WORKER=gpt-4o-mini    # turno frecuente
LLM_MODEL_ORQUESTADOR=gpt-4o    # escalado en turnos multi-paso

OPENAI_API_KEY=...
ANTHROPIC_API_KEY=...
```

### Override por empresa (`config_empresa`, control DB — migración control 0002)

Tabla `config_empresa (empresa_id, clave, valor)` con UNIQUE(empresa_id, clave). Texto plano **no
secreto** (proveedor, modelos y, a futuro, umbrales monto/confirmación del bypass). La **key** de
la empresa sale cifrada de `secretos_empresa`; si no la tiene, hereda la de plataforma.

> **Constraint duro (ADR 0005 / `secrets.md`):** las API keys **jamás** en código ni en git.
> Plataforma en `.env`; por empresa **cifradas** en el control DB (`SECRETS_MASTER_KEY`). Los
> umbrales (monto/confirmación) viven en `config_empresa`, no en código.

---

## 6. Cómo se cambia de IA en la práctica

**Un solo tenant (Punto Rojo), en `.env`:**

```bash
LLM_PROVIDER=openai
LLM_MODEL_WORKER=gpt-4o-mini
# ¿probar Claude? una línea:
LLM_PROVIDER=claude
LLM_MODEL_WORKER=claude-haiku-4-5
# reiniciar el bot. Cero código tocado.
```

**Multi-empresa:** la empresa A en OpenAI y la B en Claude a la vez — cada una con su fila en
`config_empresa` y su key cifrada. El factory resuelve por tenant en cada turno.

**Proveedor nuevo (Gemini):** `core/llm/providers/gemini.py` implementando el Protocol + una línea
en `registry.py`. Nada más.

---

## 7. Selección de modelo por turno (costo)

El proveedor recibe el `model` a usar; lo decide el despachador según `.claude/rules/performance.md`:

- **Worker** (barato, default): clasificación/operación frecuente. Default: `gpt-4o-mini`.
- **Orquestador** (escalado): razonar varios pasos o desambiguar. Default: `gpt-4o`.
- **Opus / premium**: nunca en runtime del bot (solo diseño/arquitectura fuera de línea).

El **bypass Python ya resuelve ~60% de ventas sin IA** (costo cero); el modelo solo aplica al
~40% restante. Los **rieles** del despachador (producto desconocido, precio dudoso >1 %/mín. 1
peso, confirmación hablada) son la red que hace seguro usar un modelo económico. Validar fiabilidad
empíricamente antes de fijar el default.

---

## 8. Pruebas (TDD — ya en verde)

1. **Registry** (`tests/test_llm_registry.py`): proveedores base; `ProveedorDesconocido`; registrar uno nuevo.
2. **Factory** (`tests/test_llm_factory.py`): default de plataforma; escala a orquestador; override de empresa gana; key de empresa gana; fallback a key de plataforma; `LLMSinCredencial`.
3. **Providers** (`tests/test_llm_providers.py`): traducción de tools por vendor; normalización de respuesta; `generate` con cliente inyectado.
4. **Swap** (la que cierra el diseño): el mismo turno por `ClaudeProvider` y `OpenAIProvider` → el **mismo `ToolCall` canónico**.
5. **Stores** (`tests/test_llm_stores.py`, integración): override de `config_empresa` + key descifrada de `secretos_empresa` contra un control DB efímero.

> Sin claves reales en tests: los providers se prueban con el cliente del vendor mockeado (los SDKs
> se importan perezosamente; importar el provider no requiere el SDK).

---

## 9. Relación con otros documentos

| Tema | Documento |
|---|---|
| Tool-calling nativo + despachador | `docs/adr/0005-tool-calling-nativo-despachador.md` |
| Híbrido bypass + function calling | `docs/adr/0003-ia-hibrida-bypass-function-calling.md` |
| Envelope y catálogo de herramientas | `docs/ai-tools.md` (§3 envelope, §5 catálogo) |
| Secretos por empresa cifrados | `docs/secrets.md`, `SECURITY.md` |
| Capacidades / feature flags por empresa | `docs/feature-flags.md` |
| Selección de modelo | `.claude/rules/performance.md` |

---

## 10. Deuda viva (no abordada aquí)

- `[MEDIUM]` búsqueda trigram: `similarity() >= 0.3` → operador `%` para usar el índice GIN.
- `[nombrada]` wiring venta-efectivo → `caja_movimientos` ingreso (fase cross-módulo).
- Aplicar la migración control `0002_config_empresa` al control DB en vivo (paso operativo, runbook).
- Umbrales monto/confirmación del bypass → `config_empresa` (cuando se cablee el despachador).
