"""Saneamiento de entrada: etapa LIGERA y barata ANTES del despachador (SECURITY.md, ai-tools.md §3).

NO es el guardrail completo en instancia separada (un clasificador dedicado): eso se difiere al
lanzamiento de WhatsApp. Aquí solo se ataja lo obviamente peligroso/absurdo en los args de una
herramienta, antes de resolverla: texto desmesurado o con caracteres de control, intentos básicos de
inyección de instrucciones, y números fuera de todo rango razonable (negativos o gigantes). La
validación de tipos y de cada campo la hace Pydantic (`args_model`); esto es la malla previa,
AGNÓSTICA de la herramienta.

Función PURA `revisar`: recibe los `arguments` crudos del ToolCall y devuelve un `Motivo` (mensaje
claro y genérico, sin internals ni IDs) o None. El despachador lo traduce a un `ErrorTool` del
contrato (§3, código `validacion`) y lo loguea con `tenant_id` —sin volcar el contenido ofensivo—.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

# Un mensaje de mostrador no necesita más; cap contra prompt-stuffing y abuso de memoria.
MAX_TEXTO = 2000
# 1e12: cualquier monto/cantidad mayor es absurdo en este dominio (defensa además de los topes Pydantic).
MAX_MAGNITUD = Decimal("1000000000000")

# Controles no imprimibles tolerados (salto de línea / tab / retorno); el resto se rechaza.
_CONTROL_OK = frozenset({"\n", "\t", "\r"})

# Patrones de inyección de instrucciones de alta señal (heurística barata, ES/EN). El guardrail real
# (clasificador en instancia separada) se difiere; esto ataja lo evidente sin falsos positivos típicos.
_INYECCION = re.compile(
    r"ignor[ae].{0,30}(las |all |previous |prior )?instruc"
    r"|olvida.{0,30}(todo|lo anterior|las instrucciones)"
    r"|(system|developer)\s*prompt|prompt\s+del\s+sistema|instrucciones\s+del\s+sistema"
    r"|you are now|pretend to be|act[uú]a\s+como\s+(si|un|una|el|la)\b"
    r"|jailbreak|developer\s+mode|modo\s+desarrollador"
    r"|(revela|mu[eé]stra|reveal|show)\b.{0,20}(tu |el |your )?(prompt|system\b|sistema|instruc)"
    r"|<\|.*?\|>|<<sys>>|\[/?inst\]|```\s*system",
    re.IGNORECASE | re.DOTALL,
)


@dataclass(frozen=True, slots=True)
class Motivo:
    """Razón de rechazo: mensaje genérico para el usuario + si el modelo puede repreguntar."""

    detalle: str
    recuperable: bool


def revisar(arguments: dict) -> Motivo | None:
    """Primer problema de seguridad/cordura en los args crudos (recorrido recursivo), o None."""
    return _revisar_valor(arguments)


def _revisar_valor(valor) -> Motivo | None:
    if isinstance(valor, bool):                  # bool ES subclase de int: tratarlo aparte
        return None
    if isinstance(valor, str):
        return _revisar_texto(valor)
    if isinstance(valor, (int, float, Decimal)):
        return _revisar_numero(valor)
    if isinstance(valor, dict):
        for v in valor.values():
            if (m := _revisar_valor(v)) is not None:
                return m
        return None
    if isinstance(valor, (list, tuple)):
        for v in valor:
            if (m := _revisar_valor(v)) is not None:
                return m
        return None
    return None


def _revisar_texto(texto: str) -> Motivo | None:
    if len(texto) > MAX_TEXTO:
        return Motivo("El texto enviado es demasiado largo.", recuperable=True)
    if any((ord(c) < 32 and c not in _CONTROL_OK) or ord(c) == 127 for c in texto):
        return Motivo("La entrada contiene caracteres no permitidos.", recuperable=True)
    if _INYECCION.search(texto):
        # No recuperable: no invitamos al modelo a "reescribir" un intento de inyección.
        return Motivo("La entrada contiene instrucciones no permitidas.", recuperable=False)
    return None


def _revisar_numero(numero) -> Motivo | None:
    try:
        d = numero if isinstance(numero, Decimal) else Decimal(str(numero))
    except (InvalidOperation, ValueError):
        return Motivo("Un valor numérico es inválido.", recuperable=True)
    if not d.is_finite():                         # NaN / Infinity
        return Motivo("Un valor numérico es inválido.", recuperable=True)
    if d < 0 or abs(d) > MAX_MAGNITUD:            # negativos o magnitudes absurdas
        return Motivo("Un valor numérico está fuera de rango.", recuperable=True)
    return None
