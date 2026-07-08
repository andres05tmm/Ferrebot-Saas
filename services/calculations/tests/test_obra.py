"""Aceptación gasto real de obra (plan PIM §4): suma exacta de componentes + semáforo por umbral."""
from dataclasses import dataclass
from decimal import Decimal

from services.calculations.obra import Semaforo, calcular_gasto_real_obra


@dataclass(frozen=True)
class _Gasto:
    monto: Decimal


@dataclass(frozen=True)
class _Compra:
    costo_total: Decimal


@dataclass(frozen=True)
class _Prorrateo:
    costo_imputado: Decimal


@dataclass(frozen=True)
class _Horas:
    horas: Decimal


@dataclass(frozen=True)
class _Consumo:
    cantidad: Decimal
    costo_unitario: Decimal


def _gasto_real(**overrides):
    """Llama a `calcular_gasto_real_obra` con un escenario base de total conocido = 530.000.

    Componentes: gastos 150.000 + compras 200.000 + prorrateo 80.000 + horas (8 × 10.000 = 80.000)
    + consumos (4×2.500 + 2×5.000 = 20.000). Los `overrides` ajustan el presupuesto para el semáforo.
    """
    kwargs = dict(
        gastos=[_Gasto(Decimal("100000")), _Gasto(Decimal("50000"))],
        compras=[_Compra(Decimal("200000"))],
        prorrateos=[_Prorrateo(Decimal("80000"))],
        horas_maquina=[_Horas(Decimal("5")), _Horas(Decimal("3"))],
        costo_op_hora=Decimal("10000"),
        consumos=[_Consumo(Decimal("4"), Decimal("2500")), _Consumo(Decimal("2"), Decimal("5000"))],
        ingreso_presupuestado=Decimal("1000000"),
        utilidad_presupuestada=Decimal("100000"),
    )
    kwargs.update(overrides)
    return calcular_gasto_real_obra(**kwargs)


def test_suma_exacta_de_componentes():
    """Cada componente y el total son la suma exacta de las partes conocidas (plan §4)."""
    desglose = _gasto_real()
    assert desglose.total_gastos == Decimal("150000.00")
    assert desglose.total_compras == Decimal("200000.00")
    assert desglose.total_prorrateo_nomina == Decimal("80000.00")
    assert desglose.total_horas_maquina == Decimal("80000.00")       # (5+3) × 10.000
    assert desglose.total_consumos_inventario == Decimal("20000.00")  # 10.000 + 10.000
    assert desglose.total == Decimal("530000.00")


def test_semaforo_verde_margen_cubre_utilidad():
    """VERDE: ingreso 1.000.000 − gasto 530.000 = 470.000 ≥ utilidad 100.000."""
    desglose = _gasto_real(ingreso_presupuestado=Decimal("1000000"), utilidad_presupuestada=Decimal("100000"))
    assert desglose.semaforo is Semaforo.VERDE


def test_semaforo_amarillo_margen_positivo_bajo_utilidad():
    """AMARILLO: ingreso 600.000 − gasto 530.000 = 70.000, entre 0 y utilidad 100.000."""
    desglose = _gasto_real(ingreso_presupuestado=Decimal("600000"), utilidad_presupuestada=Decimal("100000"))
    assert desglose.semaforo is Semaforo.AMARILLO


def test_semaforo_rojo_margen_negativo():
    """ROJO: ingreso 500.000 − gasto 530.000 = −30.000 (pérdida)."""
    desglose = _gasto_real(ingreso_presupuestado=Decimal("500000"), utilidad_presupuestada=Decimal("100000"))
    assert desglose.semaforo is Semaforo.ROJO


def test_semaforo_borde_margen_igual_utilidad_es_verde():
    """Borde verde/amarillo: margen == utilidad → VERDE (umbral ≥).

    ingreso 630.000 − gasto 530.000 = 100.000 == utilidad 100.000.
    """
    desglose = _gasto_real(ingreso_presupuestado=Decimal("630000"), utilidad_presupuestada=Decimal("100000"))
    assert desglose.semaforo is Semaforo.VERDE


def test_semaforo_borde_margen_cero_es_amarillo():
    """Borde amarillo/rojo: margen == 0 → AMARILLO (aún no hay pérdida).

    ingreso 530.000 − gasto 530.000 = 0.
    """
    desglose = _gasto_real(ingreso_presupuestado=Decimal("530000"), utilidad_presupuestada=Decimal("100000"))
    assert desglose.semaforo is Semaforo.AMARILLO


def test_iterables_vacios_total_cero():
    """Borde: sin gastos/compras/nada → todos los componentes y el total en cero."""
    desglose = calcular_gasto_real_obra(
        gastos=[],
        compras=[],
        prorrateos=[],
        horas_maquina=[],
        costo_op_hora=Decimal("0"),
        consumos=[],
        ingreso_presupuestado=Decimal("1000000"),
        utilidad_presupuestada=Decimal("100000"),
    )
    assert desglose.total_gastos == Decimal("0.00")
    assert desglose.total_compras == Decimal("0.00")
    assert desglose.total_prorrateo_nomina == Decimal("0.00")
    assert desglose.total_horas_maquina == Decimal("0.00")
    assert desglose.total_consumos_inventario == Decimal("0.00")
    assert desglose.total == Decimal("0.00")
    # Sin gasto, el margen es todo el ingreso presupuestado → VERDE.
    assert desglose.semaforo is Semaforo.VERDE


def test_horas_maquina_multiplica_por_costo_op_hora():
    """Las horas se costean a `costo_op_hora`: 12 horas × 15.000 = 180.000."""
    desglose = calcular_gasto_real_obra(
        gastos=[],
        compras=[],
        prorrateos=[],
        horas_maquina=[_Horas(Decimal("12"))],
        costo_op_hora=Decimal("15000"),
        consumos=[],
        ingreso_presupuestado=Decimal("1000000"),
        utilidad_presupuestada=Decimal("100000"),
    )
    assert desglose.total_horas_maquina == Decimal("180000.00")
    assert desglose.total == Decimal("180000.00")
