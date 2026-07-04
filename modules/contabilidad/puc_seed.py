"""Semilla del PUC colombiano a nivel de un comercio (clases 1-6) y códigos que el proyector usa.

Módulo PURO (datos + helpers, sin IO). El árbol trae solo las cuentas que el motor necesita para
proyectar los eventos operativos; cada empresa puede extenderlo después. Las hojas (`imputable=True`)
son las únicas que reciben movimientos; las de agrupación existen para el balance/estado por niveles.

`naturaleza` fija el signo del saldo: una cuenta `debito` (activo/gasto/costo) sube por débitos; una
`credito` (pasivo/patrimonio/ingreso) sube por créditos. `saldo = debitos − creditos` para las de
naturaleza débito, y `creditos − debitos` para las de crédito.
"""
from __future__ import annotations

from dataclasses import dataclass

# --- códigos de cuenta que el proyector referencia por constante (nunca strings mágicos) ---
CAJA = "110505"
BANCOS = "111005"
CLIENTES = "130505"
ANTICIPO_RETEFUENTE = "135515"
ANTICIPO_RETEIVA = "135517"
ANTICIPO_ICA = "135518"
INVENTARIO = "143501"
PROVEEDORES = "220505"
IVA_GENERADO = "240810"
IVA_DESCONTABLE = "240820"
RETEFUENTE_PAGAR = "236505"
RETEIVA_PAGAR = "236705"
ICA_PAGAR = "236805"
PATRIMONIO = "310505"
INGRESOS_VENTAS = "413505"
DEVOLUCIONES_VENTAS = "417505"
GASTO_NOMINA = "510506"
GASTO_TRANSPORTE = "513525"
GASTO_SERVICIOS = "513535"
GASTO_MANTENIMIENTO = "514510"
GASTO_PAPELERIA = "519525"
GASTO_OTROS = "519595"
COSTO_VENTAS = "613505"
# Compras a proveedor por factura "suelta" (sin `compra` asociada, sin inventario mapeado; ADR 0030
# cabo c / ADR 0020). Contrapartida al débito de la CxP que se acredita a Proveedores.
COMPRAS_PROVEEDOR = "620501"

# categoría de `gastos` (enum operativo) → cuenta PUC del gasto.
GASTO_CUENTA_POR_CATEGORIA: dict[str, str] = {
    "transporte": GASTO_TRANSPORTE,
    "papeleria": GASTO_PAPELERIA,
    "servicios": GASTO_SERVICIOS,
    "nomina": GASTO_NOMINA,
    "mantenimiento": GASTO_MANTENIMIENTO,
    "otros": GASTO_OTROS,
}

# tipo de retención (config_retenciones) → (cuenta anticipo del activo cuando NOS retienen en venta,
# cuenta 'por pagar' del pasivo cuando NOSOTROS retenemos en compra).
RETENCION_CUENTAS: dict[str, tuple[str, str]] = {
    "retefuente": (ANTICIPO_RETEFUENTE, RETEFUENTE_PAGAR),
    "reteiva": (ANTICIPO_RETEIVA, RETEIVA_PAGAR),
    "ica": (ANTICIPO_ICA, ICA_PAGAR),
}

D = "debito"
C = "credito"


@dataclass(frozen=True, slots=True)
class CuentaSemilla:
    codigo: str
    nombre: str
    naturaleza: str
    imputable: bool


# Árbol plano; el parent se infiere por prefijo de código (la de prefijo más largo que es prefijo
# estricto de esta). Códigos coherentes con el PUC (Decreto 2650): clase(1) grupo(2) cuenta(4) sub(6).
_SEMILLA: list[CuentaSemilla] = [
    # 1 ACTIVO ---------------------------------------------------------------
    CuentaSemilla("1", "ACTIVO", D, False),
    CuentaSemilla("11", "DISPONIBLE", D, False),
    CuentaSemilla("1105", "CAJA", D, False),
    CuentaSemilla(CAJA, "Caja general", D, True),
    CuentaSemilla("1110", "BANCOS", D, False),
    CuentaSemilla(BANCOS, "Bancos", D, True),
    CuentaSemilla("13", "DEUDORES", D, False),
    CuentaSemilla("1305", "CLIENTES", D, False),
    CuentaSemilla(CLIENTES, "Clientes (fiado)", D, True),
    CuentaSemilla("1355", "ANTICIPO DE IMPUESTOS Y RETENCIONES", D, False),
    CuentaSemilla(ANTICIPO_RETEFUENTE, "Anticipo retención en la fuente", D, True),
    CuentaSemilla(ANTICIPO_RETEIVA, "Anticipo retención de IVA", D, True),
    CuentaSemilla(ANTICIPO_ICA, "Anticipo retención de ICA", D, True),
    CuentaSemilla("14", "INVENTARIOS", D, False),
    CuentaSemilla("1435", "MERCANCÍAS NO FABRICADAS POR LA EMPRESA", D, False),
    CuentaSemilla(INVENTARIO, "Inventario de mercancías", D, True),
    # 2 PASIVO ---------------------------------------------------------------
    CuentaSemilla("2", "PASIVO", C, False),
    CuentaSemilla("22", "PROVEEDORES", C, False),
    CuentaSemilla("2205", "PROVEEDORES NACIONALES", C, False),
    CuentaSemilla(PROVEEDORES, "Proveedores nacionales", C, True),
    CuentaSemilla("23", "CUENTAS POR PAGAR", C, False),
    CuentaSemilla("2365", "RETENCIÓN EN LA FUENTE POR PAGAR", C, False),
    CuentaSemilla(RETEFUENTE_PAGAR, "Retención en la fuente por pagar", C, True),
    CuentaSemilla("2367", "IVA RETENIDO POR PAGAR", C, False),
    CuentaSemilla(RETEIVA_PAGAR, "Retención de IVA por pagar", C, True),
    CuentaSemilla("2368", "RETENCIÓN DE ICA POR PAGAR", C, False),
    CuentaSemilla(ICA_PAGAR, "Retención de ICA por pagar", C, True),
    CuentaSemilla("24", "IMPUESTOS, GRAVÁMENES Y TASAS", C, False),
    CuentaSemilla("2408", "IMPUESTO SOBRE LAS VENTAS (IVA)", C, False),
    CuentaSemilla(IVA_GENERADO, "IVA generado", C, True),
    CuentaSemilla(IVA_DESCONTABLE, "IVA descontable", D, True),   # contra del pasivo IVA
    # 3 PATRIMONIO -----------------------------------------------------------
    CuentaSemilla("3", "PATRIMONIO", C, False),
    CuentaSemilla("31", "CAPITAL SOCIAL", C, False),
    CuentaSemilla("3105", "CAPITAL", C, False),
    CuentaSemilla(PATRIMONIO, "Capital / Patrimonio inicial", C, True),
    # 4 INGRESOS -------------------------------------------------------------
    CuentaSemilla("4", "INGRESOS", C, False),
    CuentaSemilla("41", "OPERACIONALES", C, False),
    CuentaSemilla("4135", "COMERCIO AL POR MAYOR Y AL POR MENOR", C, False),
    CuentaSemilla(INGRESOS_VENTAS, "Venta de mercancías", C, True),
    CuentaSemilla("4175", "DEVOLUCIONES EN VENTAS", D, False),   # contra-ingreso
    CuentaSemilla(DEVOLUCIONES_VENTAS, "Devoluciones en ventas", D, True),
    # 5 GASTOS ---------------------------------------------------------------
    CuentaSemilla("5", "GASTOS", D, False),
    CuentaSemilla("51", "OPERACIONALES DE ADMINISTRACIÓN", D, False),
    CuentaSemilla("5105", "GASTOS DE PERSONAL", D, False),
    CuentaSemilla(GASTO_NOMINA, "Sueldos", D, True),
    CuentaSemilla("5135", "SERVICIOS", D, False),
    CuentaSemilla(GASTO_TRANSPORTE, "Transporte, fletes y acarreos", D, True),
    CuentaSemilla(GASTO_SERVICIOS, "Servicios públicos", D, True),
    CuentaSemilla("5145", "MANTENIMIENTO Y REPARACIONES", D, False),
    CuentaSemilla(GASTO_MANTENIMIENTO, "Mantenimiento y reparaciones", D, True),
    CuentaSemilla("5195", "DIVERSOS", D, False),
    CuentaSemilla(GASTO_PAPELERIA, "Útiles, papelería y fotocopias", D, True),
    CuentaSemilla(GASTO_OTROS, "Otros gastos diversos", D, True),
    # 6 COSTOS ---------------------------------------------------------------
    CuentaSemilla("6", "COSTOS DE VENTAS", D, False),
    CuentaSemilla("61", "COSTO DE VENTAS", D, False),
    CuentaSemilla("6135", "COMERCIO AL POR MAYOR Y AL POR MENOR", D, False),
    CuentaSemilla(COSTO_VENTAS, "Costo de mercancía vendida", D, True),
    CuentaSemilla("62", "COMPRAS", D, False),
    CuentaSemilla("6205", "DE MERCANCÍAS", D, False),
    CuentaSemilla(COMPRAS_PROVEEDOR, "Compras a proveedor (factura sin inventario)", D, True),
]


def semilla_puc() -> list[CuentaSemilla]:
    """Las cuentas de la semilla, ordenadas por código (padres antes que hijas)."""
    return sorted(_SEMILLA, key=lambda c: c.codigo)


def parent_de(codigo: str, codigos: set[str]) -> str | None:
    """Código padre: el prefijo estricto más largo de `codigo` presente en `codigos`. PURO."""
    candidatos = [c for c in codigos if c != codigo and codigo.startswith(c)]
    return max(candidatos, key=len) if candidatos else None
