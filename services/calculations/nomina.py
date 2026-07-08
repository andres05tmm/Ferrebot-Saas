"""Nómina — liquidación de trabajadores y prorrateo a obra (plan PIM §4).

Motor de nómina como funciones puras (skill money-safe): `Decimal` end-to-end, los valores
legales NUNCA se hardcodean sino que llegan en `ParametrosNomina` (snapshot inmutable de la fila
vigente de `parametros_legales`; el `PeriodoNomina` lo congela al crearse — spec 08), y el redondeo
va SOLO al final con `core.money.cuantizar`. UI, Excel, PDF, bot y nómina electrónica llaman aquí:
una fórmula, una verdad.

Inputs por Protocol/dataclass (duck typing), NO por el ORM: `liquidar_directo` consume cualquier
objeto con los atributos del contrato (`TrabajadorDirecto`, `AsistenciaPeriodo`) y un `ParametrosNomina`
plano. En Fase 4 el caller arma el snapshot desde `modules.nomina.ParametrosLegales`; la función pura
no depende de esa tabla.

Alcance [DEFINIR contador] (plan §7): los porcentajes y recargos son provisionales hasta que el
contador de PIM confirme los reales (errores acá tienen implicación legal). La MECÁNICA está fija y
probada; al recibir los valores solo se actualiza el snapshot y los casos de aceptación reales, sin
tocar este motor.

Invariantes con test-primero (plan §5): idempotencia de la liquidación (misma entrada → misma salida,
garantizada por ser función pura) y **conciliación exacta del prorrateo** (Σ prorrateado ≡ costo total,
sin pérdida ni duplicación de centavos — ver `prorratear_nomina_obra`).
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from typing import Protocol

from core.money import CENTAVO, cuantizar

# El salario mensual se prorratea sobre 30 días (convención de nómina colombiana, spec 08).
TREINTA = Decimal("30")


class TrabajadorDirecto(Protocol):
    """Contrato mínimo de un trabajador DIRECTO para liquidar (duck typing).

    El caller real pasa `modules.trabajadores.Trabajador` (ORM, Fase 1); los tests pasan cualquier
    objeto con `salario_base`. La elegibilidad al auxilio de transporte se deriva del salario contra
    el tope en `ParametrosNomina` (regla legal), no de un flag del trabajador.
    """

    salario_base: Decimal


class AsistenciaPeriodo(Protocol):
    """Asistencia agregada de un trabajador en el periodo (duck typing).

    `dias_trabajados` = días con `RegistroAsistencia` sin ausencia no remunerada (spec 08). Las horas
    extra se separan por tipo porque cada una lleva su propio recargo (diurna/nocturna/dominical).
    """

    dias_trabajados: Decimal
    horas_extra_diurnas: Decimal
    horas_extra_nocturnas: Decimal
    horas_dominicales: Decimal


@dataclass(frozen=True, slots=True)
class ParametrosNomina:
    """Snapshot inmutable de parámetros legales que consume el motor (NO es el ORM).

    Espeja `modules.nomina.ParametrosLegales` (mismos nombres de campo → mapeo trivial en Fase 4) y
    agrega los recargos de horas extra y la convención de horas/mes, que aún no viven en esa tabla
    (se agregan en Fase 4 junto con los valores reales — [DEFINIR contador]). Todos los `*_pct` son
    fracciones (0.04 = 4%). El motor jamás inventa un valor: lo que no esté aquí no se calcula.
    """

    # Base salarial (dinero).
    smmlv: Decimal
    auxilio_transporte: Decimal
    auxilio_transporte_tope_smmlv: int  # el auxilio aplica hasta N SMMLV de salario (hoy 2)
    # Horas extra: convención de horas/mes y recargos (multiplicadores) — [DEFINIR contador].
    horas_mes: Decimal        # 240 h/mes (spec 08)
    recargo_he_diurna: Decimal        # ~1.25
    recargo_he_nocturna: Decimal      # ~1.75
    recargo_dominical: Decimal        # ~2.0
    # Deducciones del empleado (a su cargo): salud y pensión 4% cada una.
    salud_empleado_pct: Decimal
    pension_empleado_pct: Decimal
    # Aportes del empleador (para el costeo real de obra, no salen del sueldo) — [DEFINIR contador].
    salud_empleador_pct: Decimal
    pension_empleador_pct: Decimal
    arl_pct: Decimal          # varía por clase de riesgo (construcción, clase V) [DEFINIR]
    caja_compensacion_pct: Decimal
    sena_pct: Decimal
    icbf_pct: Decimal
    # Provisiones prestacionales.
    cesantias_pct: Decimal
    intereses_cesantias_pct: Decimal  # sobre las cesantías (task: 12% anual) [DEFINIR contador]
    prima_pct: Decimal
    vacaciones_pct: Decimal


@dataclass(frozen=True, slots=True)
class Liquidacion:
    """Resultado de liquidar a un trabajador en un periodo. Todos los campos ya cuantizados (salida).

    Espeja `DetalleLiquidacion` (spec 01/08): devengados, deducciones y neto del trabajador, más los
    aportes del empleador y las provisiones — estos dos NO bajan al neto, pero SÍ alimentan el costeo
    real de la obra (por eso el prorrateo suma devengado + aportes + provisiones).
    """

    # Devengados
    salario_devengado: Decimal   # salario proporcional a los días trabajados (no el salario base pleno)
    auxilio_transporte: Decimal
    valor_horas_extra: Decimal
    total_devengado: Decimal
    # Deducciones (empleado)
    salud_empleado: Decimal
    pension_empleado: Decimal
    total_deducciones: Decimal
    # Neto a pagar
    neto_pagar: Decimal
    # Aportes empleador + provisiones (costeo real, no van al neto)
    aportes_empleador: Decimal
    provisiones: Decimal


@dataclass(frozen=True, slots=True)
class ProrrateoObra:
    """Una porción del costo total de un trabajador imputada a una obra (o a admin). Salida cuantizada.

    `obra_id = None` significa nómina administrativa (días no imputables a una obra concreta). DTO de
    la capa de cálculo (espeja `ProrrateoNominaObra`), no el modelo ORM. `costo_imputado` incluye las
    prestaciones prorrateadas, no solo el salario.
    """

    obra_id: str | None
    dias_imputados: Decimal
    costo_imputado: Decimal


def liquidar_directo(
    trabajador: TrabajadorDirecto,
    asistencia: AsistenciaPeriodo,
    params: ParametrosNomina,
) -> Liquidacion:
    """Liquida a un trabajador DIRECTO en un periodo con el motor de nómina colombiano (spec 08).

    Regla (plan §4, valores en `params` — nunca hardcodeados):
      - salario proporcional = salario_base × días/30;
      - auxilio de transporte proporcional a los días, SOLO si salario_base ≤ tope×SMMLV;
      - horas extra = (salario_base/horas_mes) × recargo × horas, por tipo (diurna/nocturna/dominical);
      - base de cotización = salario proporcional + horas extra (SIN auxilio de transporte);
      - salud/pensión del empleado = base × pct;
      - aportes del empleador = base × Σ(salud, pensión, ARL, caja, SENA, ICBF);
      - provisiones = cesantías + intereses (sobre cesantías) + prima + vacaciones.

    Redondeo SOLO al final (skill money-safe): cada componente se calcula con precisión plena y solo
    se cuantiza al construir la `Liquidacion`, para no arrastrar error de centavo entre pasos.

    Base de las provisiones [DEFINIR contador]: cesantías/prima se calculan sobre el devengado
    prestacional (salario proporcional + horas extra + auxilio, que sí es base de estas); vacaciones
    sobre el salario sin auxilio. La elección de base por prestación la afina el contador en Fase 4.
    """
    dias = asistencia.dias_trabajados
    salario_base = trabajador.salario_base

    # --- Devengados ---
    salario_proporcional = salario_base * dias / TREINTA

    tope_auxilio = params.smmlv * Decimal(params.auxilio_transporte_tope_smmlv)
    elegible_auxilio = salario_base <= tope_auxilio
    auxilio = (
        params.auxilio_transporte * dias / TREINTA if elegible_auxilio else Decimal("0")
    )

    valor_hora = salario_base / params.horas_mes
    valor_horas_extra = (
        valor_hora * params.recargo_he_diurna * asistencia.horas_extra_diurnas
        + valor_hora * params.recargo_he_nocturna * asistencia.horas_extra_nocturnas
        + valor_hora * params.recargo_dominical * asistencia.horas_dominicales
    )

    total_devengado = salario_proporcional + auxilio + valor_horas_extra

    # --- Deducciones del empleado (base de cotización SIN auxilio de transporte) ---
    base_cotizacion = salario_proporcional + valor_horas_extra
    salud_empleado = base_cotizacion * params.salud_empleado_pct
    pension_empleado = base_cotizacion * params.pension_empleado_pct
    total_deducciones = salud_empleado + pension_empleado

    neto_pagar = total_devengado - total_deducciones

    # --- Aportes del empleador (costeo real, sobre la base de cotización) ---
    aportes_empleador = base_cotizacion * (
        params.salud_empleador_pct
        + params.pension_empleador_pct
        + params.arl_pct
        + params.caja_compensacion_pct
        + params.sena_pct
        + params.icbf_pct
    )

    # --- Provisiones prestacionales ---
    base_prestacional = salario_proporcional + valor_horas_extra + auxilio
    base_vacaciones = salario_proporcional + valor_horas_extra
    cesantias = base_prestacional * params.cesantias_pct
    intereses_cesantias = cesantias * params.intereses_cesantias_pct
    prima = base_prestacional * params.prima_pct
    vacaciones = base_vacaciones * params.vacaciones_pct
    provisiones = cesantias + intereses_cesantias + prima + vacaciones

    return Liquidacion(
        salario_devengado=cuantizar(salario_proporcional),
        auxilio_transporte=cuantizar(auxilio),
        valor_horas_extra=cuantizar(valor_horas_extra),
        total_devengado=cuantizar(total_devengado),
        salud_empleado=cuantizar(salud_empleado),
        pension_empleado=cuantizar(pension_empleado),
        total_deducciones=cuantizar(total_deducciones),
        neto_pagar=cuantizar(neto_pagar),
        aportes_empleador=cuantizar(aportes_empleador),
        provisiones=cuantizar(provisiones),
    )


def liquidar_patacaliente(horas: Decimal, tarifa_hora: Decimal) -> Liquidacion:
    """Liquida a un trabajador PATACALIENTE (por hora): `neto = horas × tarifa_hora`.

    Sin deducciones, sin aportes, sin provisiones y sin CUNE (no es nómina electrónica: no son
    empleados formales — spec 08, trato tributario [DEFINIR contador]). Ej.: 48 h × 12.000 → 576.000.
    """
    total = horas * tarifa_hora
    cero = Decimal("0.00")
    return Liquidacion(
        salario_devengado=cuantizar(total),
        auxilio_transporte=cero,
        valor_horas_extra=cero,
        total_devengado=cuantizar(total),
        salud_empleado=cero,
        pension_empleado=cero,
        total_deducciones=cero,
        neto_pagar=cuantizar(total),
        aportes_empleador=cero,
        provisiones=cero,
    )


def prorratear_nomina_obra(
    liquidacion: Liquidacion,
    dias_por_obra: dict[str | None, Decimal],
) -> list[ProrrateoObra]:
    """Reparte el costo TOTAL de una liquidación entre obras según los días trabajados en cada una.

    Costo total = total_devengado + aportes_empleador + provisiones (lo que la obra realmente cuesta;
    spec 08 §3). Se prorratea por días: `costo_dia = costo_total / días_totales`. La clave `None`
    agrupa los días administrativos (no imputables a una obra).

    INVARIANTE CRÍTICO (test-primero, plan §5): **Σ costo_imputado ≡ costo_total EXACTO**, sin pérdida
    ni duplicación de centavos. Cuantizar cada fila por separado dejaría un residuo (p. ej. 100.00 en
    3 partes → 33.33×3 = 99.99). Se resuelve con reparto por mayor resto (largest-remainder): cada fila
    recibe su piso a centavo y los centavos sobrantes van, uno a uno, a las filas con mayor fracción
    truncada. Es determinista y reparte el ajuste de forma justa (no siempre al último renglón).

    Casos borde: días totales 0 (o `dias_por_obra` vacío) → `[]` (no hay costo que imputar).
    """
    costo_total = (
        liquidacion.total_devengado
        + liquidacion.aportes_empleador
        + liquidacion.provisiones
    )
    dias_totales = sum(dias_por_obra.values(), start=Decimal("0"))
    if dias_totales <= 0:
        return []

    # `costo_total` ya viene cuantizado (suma de campos cuantizados), pero lo normalizamos por claridad
    # y para que el cuadre de centavos sea exacto contra un múltiplo de 0.01.
    costo_total_q = cuantizar(costo_total)

    # 1) Piso a centavo por fila; guardamos la fracción truncada para el reparto por mayor resto.
    filas: list[list] = []
    for obra_id, dias in dias_por_obra.items():
        exacto = costo_total_q * dias / dias_totales
        piso = exacto.quantize(CENTAVO, rounding=ROUND_DOWN)
        resto = exacto - piso
        filas.append([obra_id, dias, piso, resto])

    # 2) Centavos sin asignar = residuo; se reparten a las filas con mayor resto (largest-remainder).
    asignado = sum((fila[2] for fila in filas), start=Decimal("0"))
    residuo = costo_total_q - asignado
    centavos_por_repartir = int((residuo / CENTAVO).to_integral_value(rounding=ROUND_HALF_UP))

    orden = sorted(range(len(filas)), key=lambda i: filas[i][3], reverse=True)
    for k in range(centavos_por_repartir):
        filas[orden[k % len(filas)]][2] += CENTAVO

    return [
        ProrrateoObra(obra_id=obra_id, dias_imputados=dias, costo_imputado=costo_imputado)
        for obra_id, dias, costo_imputado, _resto in filas
    ]
