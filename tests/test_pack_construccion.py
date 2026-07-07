"""Loader del pack Construcción (plan §8.1): siembra idempotente de parámetros legales + catálogos.

Unitario SIN base de datos: una conexión FALSA captura el SQL emitido y responde los SELECT de
existencia. Verifica el contrato clave del loader: (1) siempre siembra `parametros_legales` 2026 con
los valores ciertos y las columnas que espeja la migración 0043; (2) máquinas/herramientas se insertan
por `codigo` (clave UNIQUE) solo si no existen (idempotencia por clave natural); (3) sección None no
rompe (nace solo con parámetros legales).
"""
from decimal import Decimal

from tools.manifest.packs.construccion import cargar_construccion


class _FakeResult:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _FakeConn:
    """Captura (sql, params) y responde `SELECT 1 FROM <tabla> WHERE codigo` según `existentes`."""

    def __init__(self, existentes: frozenset[str] = frozenset()):
        self.ejecutados: list[tuple[str, object]] = []
        self._existentes = existentes

    def execute(self, sql, params=None):
        self.ejecutados.append((sql, params))
        normalizado = " ".join(sql.split())
        if normalizado.startswith("SELECT 1 FROM"):
            codigo = params[0]
            return _FakeResult({"?column?": 1} if codigo in self._existentes else None)
        return _FakeResult(None)

    def sql_de(self, tabla_insert: str) -> list[tuple[str, object]]:
        return [(s, p) for s, p in self.ejecutados if f"INSERT INTO {tabla_insert}" in s]


_SECCION = {
    "maquinas": [
        {"nombre": "Retroexcavadora", "tipo": "excavación"},
        {"nombre": "Volqueta", "tipo": "transporte"},
    ],
    "herramientas": [
        {"nombre": "Pulidora", "categoria": "eléctrica"},
    ],
}


def test_siembra_parametros_legales_2026_siempre():
    conn = _FakeConn()
    conteos = cargar_construccion(None, conn)  # sección None: solo parámetros legales
    assert conteos == {"parametros_legales": 1, "maquinas": 0, "herramientas": 0}
    inserts = conn.sql_de("parametros_legales")
    assert len(inserts) == 1
    sql, params = inserts[0]
    assert "ON CONFLICT (vigente_desde)" in sql            # UPSERT idempotente por vigencia (UNIQUE en 0043)
    assert params["smmlv"] == Decimal("1750905")
    assert params["auxilio_transporte"] == Decimal("249095")
    assert params["iva_general"] == Decimal("0.19")
    assert params["salud_empleado_pct"] == Decimal("0.04")


def test_maquinas_y_herramientas_se_insertan_cuando_no_existen():
    conn = _FakeConn()  # nada existe aún
    conteos = cargar_construccion(_SECCION, conn)
    assert conteos == {"parametros_legales": 1, "maquinas": 2, "herramientas": 1}
    assert len(conn.sql_de("maquinas")) == 2
    assert len(conn.sql_de("herramientas")) == 1


def test_idempotente_no_reinserta_lo_existente():
    # Segunda corrida: todas las máquinas/herramientas ya existen (por su `codigo` derivado del nombre) →
    # no se reinsertan (0), pero los parámetros legales se re-UPSERTAN (converge, no duplica).
    conn = _FakeConn(existentes=frozenset({"RETROEXCAVADORA", "VOLQUETA", "PULIDORA"}))
    conteos = cargar_construccion(_SECCION, conn)
    assert conteos == {"parametros_legales": 1, "maquinas": 0, "herramientas": 0}
    assert conn.sql_de("maquinas") == []
    assert conn.sql_de("herramientas") == []
    assert len(conn.sql_de("parametros_legales")) == 1
