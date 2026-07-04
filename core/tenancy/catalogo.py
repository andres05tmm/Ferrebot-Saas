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

# Opcionales: se activan por plan/override. El retail se partió en features finas (ventas/caja/
# inventario); `pos` sobrevive como META-PACK que expande a las tres (compat: los tenants con `pos`
# en su plan siguen viendo todo el retail sin migración de flags).
OPCIONALES: frozenset[str] = frozenset({
    "pos",
    "ventas", "caja", "inventario",
    "facturacion_electronica", "documento_soporte", "notas_electronicas", "libro_iva",
    "pos_electronico",
    "compras_fiscal", "honorarios", "fiados", "mayorista", "ventas_voz", "bot_telegram",
    "multi_vendedor", "pack_agenda", "pack_faq", "pack_cobranza", "pack_pedidos", "pack_ventas",
    "pack_reservas", "pack_postventa", "pack_pagar", "canal_whatsapp", "pagos_online",
    "conciliacion_bancaria",
    # Contable C (ADR 0027): retenciones/INC editables por tenant + libros auxiliar/mayor. Opt-in,
    # sin dependencias duras (un negocio puede retener sin FE; los libros derivan de datos existentes).
    "retenciones", "libros_contables",
})

# Meta-packs: un flag grueso que EXPANDE a features finas. La expansión conserva el flag meta en el
# set (el gating de familia del dashboard —ADR 0018— y los checks legados siguen leyendo `pos`).
# Semántica: el meta-pack SIEMPRE implica sus finas; para activar un subconjunto se usan las finas
# directamente (un override que apague una fina bajo `pos` activo no surte efecto).
#   - ventas: registrar/consultar ventas + catálogo de productos (una peluquería vende shampoo
#     sin llevar stock).
#   - caja: caja + gastos (arqueo híbrido: degrada a 0 ventas_efectivo si no hay `ventas`).
#   - inventario: stock/kárdex/ajustes + compras + proveedores (mutan stock juntos).
META_PACKS: dict[str, frozenset[str]] = {
    "pos": frozenset({"ventas", "caja", "inventario"}),
}


def expandir_metapacks(features: frozenset[str]) -> frozenset[str]:
    """Set con los meta-packs expandidos a sus finas (conserva el flag meta). PURO e idempotente."""
    expandido = set(features)
    for meta, finas in META_PACKS.items():
        if meta in features:
            expandido |= finas
    return frozenset(expandido)


# feature → conjunto-requisito en modo OR: basta UNA del conjunto para satisfacer la dependencia.
# Las dependencias apuntan a las features FINAS; `pos` las satisface porque la validación corre
# sobre el set expandido. `fiados` (vender a crédito) y `mayorista` no existen sin `ventas`.
DEPENDENCIAS: dict[str, frozenset[str]] = {
    "notas_electronicas": frozenset({"facturacion_electronica"}),
    "libro_iva": frozenset({"facturacion_electronica", "compras_fiscal"}),
    # POS electrónico (ADR 0012 D10): cierre fiscal de la venta de mostrador; reusa toda la capa FE.
    # Requiere `facturacion_electronica` (el cliente MATIAS, secretos y la máquina de estados) y `pos`
    # (la venta de mostrador que cierra). La capa fiscal sigue transversal (ADR 0008).
    "pos_electronico": frozenset({"facturacion_electronica"}),
    "ventas_voz": frozenset({"bot_telegram"}),
    "fiados": frozenset({"ventas"}),
    "mayorista": frozenset({"ventas"}),
    # El stock es DE productos del catálogo, que vive tras `ventas`.
    "inventario": frozenset({"ventas"}),
    # Cobranza (ADR 0015): la cartera v1 ES el saldo de fiados (el motor lee `clientes.saldo_fiado`).
    "pack_cobranza": frozenset({"fiados"}),
    # Pedidos (ADR 0016): el menú ES el catálogo del POS (productos, solo lectura) → `ventas`.
    "pack_pedidos": frozenset({"ventas"}),
    # Cotizaciones hacia afuera (ADR 0017): cotiza el catálogo y los precios → `ventas`.
    "pack_ventas": frozenset({"ventas"}),
    # Reservas (plan §2.7): la variante noches DEL motor de agenda (citas/recursos/config).
    "pack_reservas": frozenset({"pack_agenda"}),
    # Pagar (ADR 0019): aviso interno al dueño de cuentas por pagar. Su fuente es `facturas_proveedores`,
    # que escribe el módulo proveedores (alta de factura + abonos); ese módulo vive tras `inventario`.
    "pack_pagar": frozenset({"inventario"}),
    # Conciliación bancaria (ADR 0028): cruza el extracto con gastos/ventas/abonos. Su superficie de
    # contabilidad de caja (gastos) vive tras `caja`; basta esa para habilitarla (dep en OR).
    "conciliacion_bancaria": frozenset({"caja"}),
}


def es_feature_valida(nombre: str) -> bool:
    """True si `nombre` es una capacidad conocida (núcleo u opcional)."""
    return nombre in NUCLEO or nombre in OPCIONALES


def capacidades_completas(efectivas: frozenset[str]) -> frozenset[str]:
    """NUCLEO ∪ efectivas con meta-packs expandidos: el núcleo siempre está activo."""
    return NUCLEO | expandir_metapacks(efectivas)


def validar_dependencias(features: frozenset[str]) -> list[str]:
    """Errores de dependencia: features activas cuyo requisito (OR) no se cumple. Vacía = ok.

    Expande los meta-packs ANTES de validar (fail-safe: `pos` satisface las dependencias sobre
    sus finas aunque el llamador pase el set sin expandir). Cada error describe la feature y el
    conjunto-requisito del que falta al menos uno.
    """
    expandidas = expandir_metapacks(features)
    errores: list[str] = []
    for feature, requisitos in DEPENDENCIAS.items():
        if feature in expandidas and requisitos.isdisjoint(expandidas):
            opciones = " o ".join(sorted(requisitos))
            errores.append(f"'{feature}' requiere {opciones}")
    return errores
