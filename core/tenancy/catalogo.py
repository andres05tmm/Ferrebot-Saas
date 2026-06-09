"""Catálogo canónico de capacidades (feature-flags.md): fuente única + dependencias.

Módulo PURO (sin IO ni DB): define qué features existen, cuáles son núcleo (siempre activas) y las
dependencias entre opcionales. Lo consumen el cálculo de capacidades efectivas
(`core.tenancy.capacidades`), el gate del API y la administración de flags. La validación es la regla
de negocio "no se puede activar X sin su requisito" (feature-flags.md §Catálogo).
"""
from __future__ import annotations

# Núcleo: siempre activo, transversal a CUALQUIER vertical (ADR 0008 §D2). El punto de venta dejó de
# ser núcleo: vive tras el pack `pos`. Solo queda lo que sirve a todo negocio: contactos y resultados.
NUCLEO: frozenset[str] = frozenset({
    "clientes", "reportes",
})

# Opcionales: se activan por plan/override. `pos` (ADR 0008 §D1) agrupa el retail —ventas, inventario,
# caja, gastos, compras, proveedores— en un solo pack grueso (se podrá partir luego sin reescribir).
OPCIONALES: frozenset[str] = frozenset({
    "pos",
    "facturacion_electronica", "documento_soporte", "notas_electronicas", "libro_iva",
    "compras_fiscal", "honorarios", "fiados", "mayorista", "ventas_voz", "bot_telegram",
    "multi_vendedor", "pack_agenda", "pack_faq", "canal_whatsapp",
})

# feature → conjunto-requisito en modo OR: basta UNA del conjunto para satisfacer la dependencia.
# `fiados` (vender a crédito) y `mayorista` (precio mayorista) no existen sin el pack de ventas → `pos`.
DEPENDENCIAS: dict[str, frozenset[str]] = {
    "notas_electronicas": frozenset({"facturacion_electronica"}),
    "libro_iva": frozenset({"facturacion_electronica", "compras_fiscal"}),
    "ventas_voz": frozenset({"bot_telegram"}),
    "fiados": frozenset({"pos"}),
    "mayorista": frozenset({"pos"}),
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
