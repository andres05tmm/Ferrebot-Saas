"""Catálogo canónico de capacidades (feature-flags.md): fuente única + dependencias.

Módulo PURO (sin IO ni DB): define qué features existen, cuáles son núcleo (siempre activas) y las
dependencias entre opcionales. Lo consumen el cálculo de capacidades efectivas
(`core.tenancy.capacidades`), el gate del API y la administración de flags. La validación es la regla
de negocio "no se puede activar X sin su requisito" (feature-flags.md §Catálogo).
"""
from __future__ import annotations

# Núcleo: siempre activo, no depende del plan (feature-flags.md §Catálogo).
NUCLEO: frozenset[str] = frozenset({
    "ventas", "inventario", "caja", "gastos", "clientes", "proveedores", "reportes",
})

# Opcionales: se activan por plan/override.
OPCIONALES: frozenset[str] = frozenset({
    "facturacion_electronica", "documento_soporte", "notas_electronicas", "libro_iva",
    "compras_fiscal", "honorarios", "fiados", "mayorista", "ventas_voz", "bot_telegram",
    "multi_vendedor",
})

# feature → conjunto-requisito en modo OR: basta UNA del conjunto para satisfacer la dependencia.
DEPENDENCIAS: dict[str, frozenset[str]] = {
    "notas_electronicas": frozenset({"facturacion_electronica"}),
    "libro_iva": frozenset({"facturacion_electronica", "compras_fiscal"}),
    "ventas_voz": frozenset({"bot_telegram"}),
}


def es_feature_valida(nombre: str) -> bool:
    """True si `nombre` es una capacidad conocida (núcleo u opcional)."""
    return nombre in NUCLEO or nombre in OPCIONALES


def capacidades_completas(efectivas: frozenset[str]) -> frozenset[str]:
    """NUCLEO ∪ efectivas: el núcleo siempre está activo, se sumen o no las efectivas del plan."""
    return NUCLEO | efectivas


def validar_dependencias(features: frozenset[str]) -> list[str]:
    """Errores de dependencia: features activas cuyo requisito (OR) no se cumple. Vacía = ok.

    Cada error describe la feature y el conjunto-requisito del que falta al menos uno.
    """
    errores: list[str] = []
    for feature, requisitos in DEPENDENCIAS.items():
        if feature in features and requisitos.isdisjoint(features):
            opciones = " o ".join(sorted(requisitos))
            errores.append(f"'{feature}' requiere {opciones}")
    return errores
