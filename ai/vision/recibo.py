"""Extracción con Visión de un comprobante de pago de Bancolombia (Fase 6 — Bot PIM).

Esta capa hace UNA sola cosa: recibe la foto de un recibo Bancolombia (transferencia, pago QR,
PSE, consignación, envío a Nequi…), se la pasa al modelo de visión con un prompt estricto y
devuelve un `ReciboExtraido` validado. **No persiste nada.** La escritura del `Gasto` (con
`origen_registro = TELEGRAM_BOT`, la URL de la imagen y `requiere_revision`) es de la Fase 6
completa, que necesita la extensión de la tabla `gastos` (Fase 3). Aquí SOLO se extrae y valida.

Contrato con el caller (Fase 6):
  - `extraer_recibo(image, provider, *, modelo=...) -> ReciboExtraido`.
  - El caller decide la revisión humana con `ReciboExtraido.requiere_revision`
    (equivale a `confianza < UMBRAL_REVISION`, 0.7). Aquí NO se decide persistencia.

Robustez (nunca crashea por culpa del modelo): si el modelo devuelve texto con el JSON embebido,
se extrae; si el JSON es inválido, está roto o no viene, se degrada a un `ReciboExtraido` con
`confianza = 0` y un `motivo`, en vez de lanzar excepción — así ese recibo cae solo en la bandeja
de revisión. Los fallos del PROVEEDOR (red, 429, 5xx, auth) NO se atrapan aquí: son de la capa de
resiliencia de `core/llm` y del reintento de la Fase 6; se dejan propagar a propósito.

Vocabulario: `core/llm` habla inglés; esta capa de dominio habla español (como el resto de `ai/`).
"""
from __future__ import annotations

import json
import re
from datetime import date
from decimal import Decimal, InvalidOperation

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from core.llm.base import ImageBlock, LLMProvider, Message

# Umbral de confianza por debajo del cual la Fase 6 manda el recibo a revisión humana
# (spec 14 §"Register expense": `confianza < 0.7` → `requiereRevision = true`).
UMBRAL_REVISION: Decimal = Decimal("0.7")

# Modelo de visión por defecto. Es un FALLBACK: en producción la Fase 6 resuelve (proveedor+modelo)
# por el factory de `core/llm` (`LLMResuelto.model`) y lo pasa como `modelo=`. Spec 14 fija Claude
# con visión; el default espeja el orquestador del .env (claude-sonnet-4-6) sin acoplar credenciales.
MODELO_VISION_POR_DEFECTO = "claude-sonnet-4-6"


# --- prompts ----------------------------------------------------------------
_SYSTEM = (
    "Eres un extractor de datos de comprobantes de pago del banco colombiano Bancolombia "
    "(transferencias, pagos QR, PSE, consignaciones y envíos a Nequi). Devuelves EXCLUSIVAMENTE "
    "un objeto JSON válido, sin texto adicional, sin explicaciones y sin bloques de código markdown."
)

# Contrato del prompt (documentado para la Fase 6): `valor` es un NÚMERO PLANO en pesos, sin
# separador de miles ni símbolo de moneda (p. ej. 1150000 o 1150000.50). `fecha` en ISO YYYY-MM-DD.
# `confianza` es la autoestimación del modelo en [0, 1]. El parser de abajo es, además, tolerante a
# que el modelo desobedezca y mande formato colombiano ("1.150.000,00", "$150.000").
_PROMPT = """La imagen es un comprobante de una transacción de Bancolombia. Extrae los datos y \
responde ÚNICAMENTE con un objeto JSON (sin ``` y sin texto alrededor) con EXACTAMENTE estas claves:

{
  "fecha": "YYYY-MM-DD" | null,
  "valor": number | null,
  "referencia": string | null,
  "tipo_transaccion": string | null,
  "entidad_o_producto_origen": string | null,
  "destino": string | null,
  "descripcion": string | null,
  "confianza": number
}

Significado de cada campo:
- "fecha": fecha de la transacción (ISO YYYY-MM-DD).
- "valor": monto en pesos colombianos como número PLANO, SIN separador de miles y SIN "$" \
(ejemplos válidos: 1150000 o 1150000.50).
- "referencia": número de aprobación / referencia / comprobante.
- "tipo_transaccion": transferencia | pago | consignacion | qr | pse | nequi | ...
- "entidad_o_producto_origen": de dónde salió el dinero (cuenta de ahorros, Nequi, tarjeta…).
- "destino": a quién o a qué cuenta/entidad llegó (nombre o entidad destino).
- "descripcion": concepto o nota, si aparece.
- "confianza": TU certeza global, un número entre 0 y 1 (1 = imagen totalmente legible y seguro).

Reglas:
- Usa null en cualquier campo que no puedas leer con seguridad (no inventes).
- "confianza" es tu autoestimación honesta entre 0 y 1.
- Responde solo el JSON, nada más."""


# --- parsers tolerantes (nunca lanzan) --------------------------------------
def _a_decimal(valor: object) -> Decimal | None:
    """Monto → `Decimal` exacto, o None. Tolera número JSON y formato colombiano.

    - int/Decimal → exacto; float → vía `str()` para no arrastrar el ruido binario del float.
    - "1.150.000,00" (colombiano) → 1150000.00; "1150000,50" → 1150000.50.
    - "$150.000" → 150000 (punto de miles): un único punto seguido de 3 dígitos, o varios puntos,
      se tratan como separador de miles (así son los montos reales de Bancolombia); un único punto
      con 1-2 decimales se respeta como coma decimal.
    """
    if valor is None or isinstance(valor, bool):
        return None
    if isinstance(valor, int):
        return Decimal(valor)
    if isinstance(valor, Decimal):
        return valor
    if isinstance(valor, float):
        try:
            return Decimal(str(valor))
        except (InvalidOperation, ValueError):
            return None
    if not isinstance(valor, str):
        return None

    s = valor.strip().replace(" ", " ")
    for basura in ("$", "COP", "cop", "Cop", " "):
        s = s.replace(basura, "")
    if not s:
        return None
    negativo = s.startswith("-")
    s = s.lstrip("+-")

    tiene_punto = "." in s
    tiene_coma = "," in s
    if tiene_punto and tiene_coma:
        # Colombiano: punto = miles, coma = decimales.
        s = s.replace(".", "").replace(",", ".")
    elif tiene_coma:
        s = s.replace(",", ".")
    elif tiene_punto:
        _, _, frac = s.rpartition(".")
        if s.count(".") > 1 or len(frac) == 3:
            s = s.replace(".", "")  # separador(es) de miles → fuera
        # else: un único punto con 1-2 decimales → coma decimal, se deja tal cual.

    try:
        d = Decimal(s)
    except (InvalidOperation, ValueError):
        return None
    return -d if negativo else d


def _a_confianza(valor: object) -> Decimal:
    """Confianza → `Decimal` en [0, 1]. Cualquier cosa ilegible degrada a 0 (default seguro)."""
    if isinstance(valor, bool):
        return Decimal("0")
    if isinstance(valor, (int, float, Decimal)):
        try:
            d = Decimal(str(valor))
        except (InvalidOperation, ValueError):
            return Decimal("0")
    elif isinstance(valor, str):
        s = valor.strip().replace("%", "")
        if "," in s and "." not in s:
            s = s.replace(",", ".")
        try:
            d = Decimal(s)
        except (InvalidOperation, ValueError):
            return Decimal("0")
    else:
        return Decimal("0")
    if d < 0:
        return Decimal("0")
    if d > 1:
        return Decimal("1")
    return d


def _a_fecha(valor: object) -> date | None:
    """Fecha → `date`, o None. Acepta `date`, ISO YYYY-MM-DD y dd/mm/aaaa."""
    if valor is None or isinstance(valor, bool):
        return None
    if isinstance(valor, date):
        return valor
    if not isinstance(valor, str):
        return None
    s = valor.strip()
    if not s or s.lower() in {"null", "none", "n/a"}:
        return None
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        pass
    m = re.match(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{4})$", s)
    if m:
        dia, mes, anio = (int(g) for g in m.groups())
        try:
            return date(anio, mes, dia)
        except ValueError:
            return None
    return None


def _a_str(valor: object) -> str | None:
    """Campo de texto → str no vacío, o None (colapsa 'null'/'none'/vacío y estructuras)."""
    if valor is None or isinstance(valor, (dict, list, bool)):
        return None
    s = str(valor).strip()
    if not s or s.lower() in {"null", "none", "n/a"}:
        return None
    return s


# --- modelo de salida -------------------------------------------------------
class ReciboExtraido(BaseModel):
    """Datos estructurados de un comprobante Bancolombia. Dinero en `Decimal` (nunca float).

    Todos los campos de negocio son opcionales: el modelo pone null lo que no puede leer con
    seguridad. `confianza` es la autoestimación del modelo en [0, 1]; `motivo` explica una
    degradación (JSON roto/ausente) y es None cuando la extracción fue limpia.
    """

    model_config = ConfigDict(frozen=True)

    fecha: date | None = None
    valor: Decimal | None = None
    referencia: str | None = None            # número de aprobación / referencia / comprobante
    tipo_transaccion: str | None = None      # transferencia | pago | consignacion | qr | pse | nequi
    entidad_o_producto_origen: str | None = None
    destino: str | None = None
    descripcion: str | None = None
    confianza: Decimal = Decimal("0")        # autoestimación del modelo, 0..1
    motivo: str | None = None                # por qué se degradó (None si la lectura fue limpia)

    @field_validator("valor", mode="before")
    @classmethod
    def _val_valor(cls, v: object) -> Decimal | None:
        return _a_decimal(v)

    @field_validator("confianza", mode="before")
    @classmethod
    def _val_confianza(cls, v: object) -> Decimal:
        return _a_confianza(v)

    @field_validator("fecha", mode="before")
    @classmethod
    def _val_fecha(cls, v: object) -> date | None:
        return _a_fecha(v)

    @field_validator(
        "referencia",
        "tipo_transaccion",
        "entidad_o_producto_origen",
        "destino",
        "descripcion",
        "motivo",
        mode="before",
    )
    @classmethod
    def _val_texto(cls, v: object) -> str | None:
        return _a_str(v)

    @property
    def requiere_revision(self) -> bool:
        """True si el caller (Fase 6) debe mandar el recibo a la bandeja de revisión humana."""
        return self.confianza < UMBRAL_REVISION

    @classmethod
    def desde_crudo(cls, datos: dict[str, object]) -> "ReciboExtraido":
        """Construye desde el dict del modelo, tolerando alias comunes; nunca lanza.

        Acepta tanto las claves canónicas del prompt como sinónimos frecuentes (p. ej. `monto`,
        `destinatario`, `numeroReferencia`, `concepto`) por si el modelo se desvía del contrato.
        """

        def elegir(*claves: str) -> object:
            for k in claves:
                if k in datos and datos[k] is not None:
                    return datos[k]
            for k in claves:
                if k in datos:
                    return datos[k]
            return None

        try:
            return cls(
                fecha=elegir("fecha", "date"),
                valor=elegir("valor", "monto", "amount", "valor_total"),
                referencia=elegir(
                    "referencia", "numero_aprobacion", "numeroReferencia",
                    "numero_referencia", "referenceNumber", "comprobante",
                ),
                tipo_transaccion=elegir("tipo_transaccion", "tipo", "type"),
                entidad_o_producto_origen=elegir(
                    "entidad_o_producto_origen", "origen", "producto_origen", "source",
                ),
                destino=elegir("destino", "destinatario", "destination"),
                descripcion=elegir("descripcion", "descripción", "concepto", "nota", "description"),
                confianza=elegir("confianza", "confidence"),
                motivo=None,
            )
        except ValidationError:
            # Los validadores no lanzan, pero por si acaso: degradar en vez de propagar.
            return cls(confianza=Decimal("0"), motivo="estructura JSON inesperada del modelo")


# --- extracción de JSON de la respuesta -------------------------------------
def _sin_fences(texto: str) -> str:
    """Quita un cerco markdown ```...``` (con o sin etiqueta de lenguaje) si envuelve todo el texto."""
    t = texto.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z0-9]*\s*", "", t)
        t = re.sub(r"\s*```$", "", t).strip()
    return t


def _bloque_json(texto: str) -> str | None:
    """Devuelve el primer objeto `{...}` balanceado del texto (respeta cadenas y escapes), o None."""
    inicio = texto.find("{")
    if inicio == -1:
        return None
    profundidad = 0
    en_cadena = False
    escape = False
    for i in range(inicio, len(texto)):
        c = texto[i]
        if en_cadena:
            if escape:
                escape = False
            elif c == "\\":
                escape = True
            elif c == '"':
                en_cadena = False
            continue
        if c == '"':
            en_cadena = True
        elif c == "{":
            profundidad += 1
        elif c == "}":
            profundidad -= 1
            if profundidad == 0:
                return texto[inicio : i + 1]
    return None


def _extraer_json(texto: str) -> dict[str, object] | None:
    """Extrae el dict JSON de la respuesta del modelo (puro, en fences o embebido). None si no hay."""
    if not texto or not texto.strip():
        return None
    limpio = _sin_fences(texto)
    for candidato in (limpio, _bloque_json(limpio), _bloque_json(texto)):
        if not candidato:
            continue
        try:
            datos = json.loads(candidato)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(datos, dict):
            return datos
    return None


# --- API pública ------------------------------------------------------------
async def extraer_recibo(
    image: ImageBlock,
    provider: LLMProvider,
    *,
    modelo: str | None = None,
) -> ReciboExtraido:
    """Extrae los datos de un recibo Bancolombia desde su foto usando visión del `provider`.

    Arma el mensaje de usuario con el prompt en español y la imagen (`images=[image]`), llama a
    `provider.generate(...)` (async), y parsea/valida el JSON de la respuesta contra
    `ReciboExtraido`. Si el modelo no devuelve un JSON válido, degrada a `confianza = 0` con
    `motivo` (nunca lanza por output malformado). Los errores del proveedor (red/429/5xx/auth) se
    dejan propagar: los maneja la resiliencia de `core/llm` y el reintento de la Fase 6.

    El caller (Fase 6) usa `ReciboExtraido.requiere_revision` (`confianza < UMBRAL_REVISION`) para
    decidir si el `Gasto` entra a la bandeja de revisión. Esta función NO persiste nada.
    """
    mensaje = Message(role="user", content=_PROMPT, images=[image])
    respuesta = await provider.generate(
        messages=[mensaje],
        tools=[],
        model=modelo or MODELO_VISION_POR_DEFECTO,
        system=_SYSTEM,
    )
    datos = _extraer_json(respuesta.text or "")
    if datos is None:
        return ReciboExtraido(
            confianza=Decimal("0"),
            motivo="la respuesta del modelo no contenía un JSON válido",
        )
    return ReciboExtraido.desde_crudo(datos)
