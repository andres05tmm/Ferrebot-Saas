"""Motor PURO de retenciones/INC (ADR 0027): dado el catálogo del tenant y un documento, calcula los
renglones tributarios. Sin IO, sin SQL, sin estado — la unidad testeable del cálculo.

Reglas (todas con tarifa y base explícitas, nada hardcodeado):

- **retefuente**: base = base gravable (subtotal sin IVA); se retiene solo si la base ≥ `base_minima_uvt
  × uvt_valor` (umbral en UVT); valor = base × tarifa%.
- **ica**: base = base gravable; valor = base × tarifa‰ (tarifa POR MIL).
- **reteiva**: base = el IVA del documento; valor = IVA × tarifa%.
- **inc**: base = base gravable; valor = base × tarifa%. (Impuesto al consumo: se registra como tributo
  del documento; incorporarlo al total cobrado es opt-in futuro — ver ADR 0027.)

Dinero con `core.money.cuantizar` (NUMERIC(12,2), ROUND_HALF_UP). El motor NUNCA muta el total del
documento: solo devuelve renglones. Un tenant sin reglas → lista vacía → ningún total cambia.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from core.money import cuantizar

RETEFUENTE = "retefuente"
ICA = "ica"
RETEIVA = "reteiva"
INC = "inc"
UVT = "uvt"
# Fila de CONFIG del tenant (NO es una regla de cálculo, no genera renglón): su `activo` prende/apaga
# que el INC se SUME al total del documento (opt-in, ADR 0027 D5). Clave natural: (inc_al_total, global).
INC_AL_TOTAL = "inc_al_total"

# Retenciones "verdaderas" (reducen el pago recibido); INC es un impuesto (no retención) → aparte.
TIPOS_RETENCION: frozenset[str] = frozenset({RETEFUENTE, ICA, RETEIVA})
# Filas de CONFIG (valores/interruptores del tenant), NO reglas de cálculo: se excluyen del motor.
TIPOS_CONFIG: frozenset[str] = frozenset({UVT, INC_AL_TOTAL})


@dataclass(frozen=True, slots=True)
class ReglaRetencion:
    """Una regla activa del catálogo del tenant (proyección de `config_retenciones`)."""

    tipo: str
    concepto: str
    base_minima_uvt: Decimal
    tarifa: Decimal
    activo: bool = True


@dataclass(frozen=True, slots=True)
class RetencionCalculada:
    """Renglón tributario calculado para un documento (listo para persistir en `retenciones_documento`)."""

    tipo: str
    concepto: str
    base: Decimal
    tarifa: Decimal
    valor: Decimal


def _valor(tipo: str, base: Decimal, tarifa: Decimal) -> Decimal:
    """Valor del renglón: ICA por mil (÷1000); el resto porcentaje (÷100). Cuantizado a centavos."""
    divisor = Decimal(1000) if tipo == ICA else Decimal(100)
    return cuantizar(base * tarifa / divisor)


def calcular_retenciones(
    reglas: list[ReglaRetencion],
    *,
    base_gravable: Decimal,
    iva: Decimal,
    uvt_valor: Decimal,
) -> list[RetencionCalculada]:
    """Aplica las reglas activas a un documento y devuelve los renglones con valor > 0. PURO.

    `base_gravable` = subtotal SIN IVA; `iva` = impuesto del documento. `uvt_valor` = valor del UVT en
    pesos (0 si el tenant no lo configuró → el umbral de retefuente se ignora y la retención aplica).
    Nunca muta el total del documento.
    """
    renglones: list[RetencionCalculada] = []
    for r in reglas:
        if not r.activo or r.tipo in TIPOS_CONFIG or r.tarifa <= 0:
            continue
        base = iva if r.tipo == RETEIVA else base_gravable
        if base <= 0:
            continue
        if r.tipo == RETEFUENTE and r.base_minima_uvt > 0 and uvt_valor > 0:
            if base_gravable < cuantizar(r.base_minima_uvt * uvt_valor):
                continue   # bajo la base mínima: no se retiene
        valor = _valor(r.tipo, base, r.tarifa)
        if valor <= 0:
            continue
        renglones.append(
            RetencionCalculada(
                tipo=r.tipo, concepto=r.concepto,
                base=cuantizar(base), tarifa=r.tarifa, valor=valor,
            )
        )
    return renglones


def total_retenido(renglones: list[RetencionCalculada]) -> Decimal:
    """Σ de las retenciones VERDADERAS (retefuente/ica/reteiva): lo que reduce el pago recibido. PURO."""
    return cuantizar(
        sum((r.valor for r in renglones if r.tipo in TIPOS_RETENCION), Decimal("0"))
    )


def total_inc(renglones: list[RetencionCalculada]) -> Decimal:
    """Σ del INC calculado (impuesto al consumo, registrado aparte del neto). PURO."""
    return cuantizar(sum((r.valor for r in renglones if r.tipo == INC), Decimal("0")))
