"""Aceptación nómina (plan PIM §4): liquidación directa/patacaliente y prorrateo a obra.

Los valores legales son PROVISIONALES [DEFINIR contador] (plan §7): acá se prueba la MECÁNICA del
motor, no cifras reales del contador. El factory `_params_default` documenta el snapshot usado; al
recibir los reales solo se actualizan estos valores y los casos de aceptación del contador.

Invariante crítico (test-primero, plan §5): la suma del prorrateo por obra es EXACTAMENTE el costo
total de la liquidación, incluso cuando el reparto fuerza un residuo de redondeo.
"""
from dataclasses import dataclass
from decimal import Decimal

from services.calculations.nomina import (
    Liquidacion,
    ParametrosNomina,
    liquidar_directo,
    liquidar_patacaliente,
    prorratear_nomina_obra,
)


@dataclass(frozen=True)
class _Trabajador:
    """Contrato mínimo (`TrabajadorDirecto`): solo el salario base."""

    salario_base: Decimal


@dataclass(frozen=True)
class _Asistencia:
    """Contrato mínimo (`AsistenciaPeriodo`): días y horas extra por tipo."""

    dias_trabajados: Decimal
    horas_extra_diurnas: Decimal = Decimal("0")
    horas_extra_nocturnas: Decimal = Decimal("0")
    horas_dominicales: Decimal = Decimal("0")


def _params_default() -> ParametrosNomina:
    """Snapshot PROVISIONAL de parámetros legales 2026 [DEFINIR contador] (plan §7).

    SMMLV y auxilio son los valores 2026 confirmados (spec 08). Los porcentajes de aportes del
    empleador suman 0.30 a propósito (elegidos para verificar la mecánica con aritmética limpia); los
    reales (salud 8.5%, pensión 12%, ARL por clase de riesgo de construcción, parafiscales) los
    confirma el contador. `intereses_cesantias_pct` = 0.12 sigue la regla del task ("12% anual sobre
    cesantías"); la tabla `parametros_legales` la siembra como 0.01 mensual — reconciliación [DEFINIR].
    """
    return ParametrosNomina(
        smmlv=Decimal("1750905"),
        auxilio_transporte=Decimal("249095"),
        auxilio_transporte_tope_smmlv=2,
        horas_mes=Decimal("240"),
        recargo_he_diurna=Decimal("1.25"),
        recargo_he_nocturna=Decimal("1.75"),
        recargo_dominical=Decimal("2.0"),
        salud_empleado_pct=Decimal("0.04"),
        pension_empleado_pct=Decimal("0.04"),
        salud_empleador_pct=Decimal("0.085"),
        pension_empleador_pct=Decimal("0.12"),
        arl_pct=Decimal("0.005"),
        caja_compensacion_pct=Decimal("0.04"),
        sena_pct=Decimal("0.02"),
        icbf_pct=Decimal("0.03"),
        cesantias_pct=Decimal("0.0833"),
        intereses_cesantias_pct=Decimal("0.12"),
        prima_pct=Decimal("0.0833"),
        vacaciones_pct=Decimal("0.0417"),
    )


# --------------------------------------------------------------------------------------------------
# liquidar_directo
# --------------------------------------------------------------------------------------------------
def test_directo_mes_completo_salario_alto_sin_auxilio():
    """Caso sintético limpio: mes completo, salario > 2 SMMLV (sin auxilio), sin horas extra.

    Todas las cifras salen enteras con el snapshot provisional, así que se pinta la liquidación entera.
    """
    liq = liquidar_directo(
        _Trabajador(salario_base=Decimal("4000000")),
        _Asistencia(dias_trabajados=Decimal("30")),
        _params_default(),
    )
    # Devengados: mes completo, sin auxilio (4.000.000 > 2×1.750.905 = 3.501.810), sin HE.
    assert liq.salario_devengado == Decimal("4000000.00")
    assert liq.auxilio_transporte == Decimal("0.00")
    assert liq.valor_horas_extra == Decimal("0.00")
    assert liq.total_devengado == Decimal("4000000.00")
    # Deducciones sobre base de cotización (= 4.000.000, sin auxilio): 4% + 4%.
    assert liq.salud_empleado == Decimal("160000.00")
    assert liq.pension_empleado == Decimal("160000.00")
    assert liq.total_deducciones == Decimal("320000.00")
    assert liq.neto_pagar == Decimal("3680000.00")
    # Aportes del empleador: base × 0.30 (0.085+0.12+0.005+0.04+0.02+0.03).
    assert liq.aportes_empleador == Decimal("1200000.00")
    # Provisiones: cesantías 333.200 + intereses 39.984 + prima 333.200 + vacaciones 166.800.
    assert liq.provisiones == Decimal("873184.00")


def test_directo_medio_mes_con_auxilio_y_horas_extra():
    """Cubre las ramas de auxilio elegible (≤ 2 SMMLV), días parciales y horas extra diurnas."""
    liq = liquidar_directo(
        _Trabajador(salario_base=Decimal("1200000")),
        _Asistencia(dias_trabajados=Decimal("15"), horas_extra_diurnas=Decimal("10")),
        _params_default(),
    )
    # Salario proporcional 1.200.000 × 15/30 = 600.000.
    assert liq.salario_devengado == Decimal("600000.00")
    # Auxilio proporcional: 249.095 × 15/30 = 124.547,50 (elegible, salario ≤ 2 SMMLV).
    assert liq.auxilio_transporte == Decimal("124547.50")
    # HE diurnas: (1.200.000/240) × 1.25 × 10 = 5.000 × 1.25 × 10 = 62.500.
    assert liq.valor_horas_extra == Decimal("62500.00")
    assert liq.total_devengado == Decimal("787047.50")
    # Base de cotización = 600.000 + 62.500 (SIN auxilio) = 662.500; 4% cada aporte.
    assert liq.salud_empleado == Decimal("26500.00")
    assert liq.pension_empleado == Decimal("26500.00")
    assert liq.total_deducciones == Decimal("53000.00")
    assert liq.neto_pagar == Decimal("734047.50")
    assert liq.aportes_empleador == Decimal("198750.00")  # 662.500 × 0.30
    assert liq.provisiones > Decimal("0")  # exacto no se fija: bases con .50 dan decimales largos


def test_directo_sin_dias_todo_en_cero():
    """Borde: 0 días trabajados → todo devengado/deducción/aporte en cero."""
    liq = liquidar_directo(
        _Trabajador(salario_base=Decimal("2000000")),
        _Asistencia(dias_trabajados=Decimal("0")),
        _params_default(),
    )
    assert liq.total_devengado == Decimal("0.00")
    assert liq.total_deducciones == Decimal("0.00")
    assert liq.neto_pagar == Decimal("0.00")
    assert liq.aportes_empleador == Decimal("0.00")
    assert liq.provisiones == Decimal("0.00")


def test_directo_es_puro_misma_entrada_misma_salida():
    """Idempotencia (plan §5): función pura → dos corridas idénticas dan el mismo resultado."""
    trabajador = _Trabajador(salario_base=Decimal("3000000"))
    asistencia = _Asistencia(dias_trabajados=Decimal("30"), horas_extra_nocturnas=Decimal("4"))
    params = _params_default()
    assert liquidar_directo(trabajador, asistencia, params) == liquidar_directo(
        trabajador, asistencia, params
    )


# --------------------------------------------------------------------------------------------------
# liquidar_patacaliente
# --------------------------------------------------------------------------------------------------
def test_patacaliente_48h_por_12000():
    """Caso del brief (plan §4): 48 h × 12.000 = 576.000, sin deducciones ni aportes ni CUNE."""
    liq = liquidar_patacaliente(Decimal("48"), Decimal("12000"))
    assert liq.total_devengado == Decimal("576000.00")
    assert liq.neto_pagar == Decimal("576000.00")
    assert liq.total_deducciones == Decimal("0.00")
    assert liq.aportes_empleador == Decimal("0.00")
    assert liq.provisiones == Decimal("0.00")


# --------------------------------------------------------------------------------------------------
# prorratear_nomina_obra — INVARIANTE de conciliación exacta (test-primero, plan §5)
# --------------------------------------------------------------------------------------------------
def _liquidacion(total_devengado: str, aportes: str = "0.00", provisiones: str = "0.00") -> Liquidacion:
    """Arma una `Liquidacion` mínima para prorratear (solo importan devengado + aportes + provisiones)."""
    return Liquidacion(
        salario_devengado=Decimal(total_devengado),
        auxilio_transporte=Decimal("0.00"),
        valor_horas_extra=Decimal("0.00"),
        total_devengado=Decimal(total_devengado),
        salud_empleado=Decimal("0.00"),
        pension_empleado=Decimal("0.00"),
        total_deducciones=Decimal("0.00"),
        neto_pagar=Decimal(total_devengado),
        aportes_empleador=Decimal(aportes),
        provisiones=Decimal(provisiones),
    )


def test_prorrateo_caso_brief_15_dias_tres_obras_suma_exacta():
    """Caso del brief: 15 días (10 obra A, 3 obra B, 2 admin) → 3 filas que suman EXACTO el total."""
    liq = _liquidacion("1500000.00", aportes="300000.00", provisiones="200000.00")  # total 2.000.000
    filas = prorratear_nomina_obra(
        liq, {"obra-A": Decimal("10"), "obra-B": Decimal("3"), None: Decimal("2")}
    )
    assert len(filas) == 3
    costo_total = liq.total_devengado + liq.aportes_empleador + liq.provisiones
    assert sum(f.costo_imputado for f in filas) == costo_total == Decimal("2000000.00")
    # El día administrativo se agrupa bajo obra_id None.
    assert {f.obra_id for f in filas} == {"obra-A", "obra-B", None}


def test_prorrateo_invariante_con_residuo_de_redondeo():
    """INVARIANTE (test-primero): montos que fuerzan residuo → Σ sigue siendo EXACTA.

    100.00 en 3 partes iguales daría 33.33×3 = 99.99 con cuantización ingenua (pierde 1 centavo).
    El reparto por mayor resto lo recupera: la suma debe ser 100.00 clavado.
    """
    liq = _liquidacion("100.00")
    filas = prorratear_nomina_obra(
        liq, {"a": Decimal("1"), "b": Decimal("1"), "c": Decimal("1")}
    )
    assert sum(f.costo_imputado for f in filas) == Decimal("100.00")
    # Ninguna fila se aleja más de un centavo de su parte "justa" (33.33 o 33.34).
    for fila in filas:
        assert fila.costo_imputado in {Decimal("33.33"), Decimal("33.34")}


def test_prorrateo_residuo_varios_centavos_suma_exacta():
    """Residuo de varios centavos (7 obras de 1 día sobre 100.00) → Σ exacta, sin duplicar."""
    liq = _liquidacion("100.00")
    dias = {f"obra-{i}": Decimal("1") for i in range(7)}
    filas = prorratear_nomina_obra(liq, dias)
    assert len(filas) == 7
    assert sum(f.costo_imputado for f in filas) == Decimal("100.00")


def test_prorrateo_una_sola_obra_recibe_todo():
    """Una sola obra → recibe el costo total completo."""
    liq = _liquidacion("1234567.89")
    filas = prorratear_nomina_obra(liq, {"obra-unica": Decimal("20")})
    assert len(filas) == 1
    assert filas[0].costo_imputado == Decimal("1234567.89")


def test_prorrateo_sin_dias_lista_vacia():
    """Borde: días totales 0 o dict vacío → no hay costo que imputar → []."""
    liq = _liquidacion("500000.00")
    assert prorratear_nomina_obra(liq, {}) == []
    assert prorratear_nomina_obra(liq, {"obra-A": Decimal("0"), None: Decimal("0")}) == []
