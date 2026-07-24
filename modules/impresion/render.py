"""Render ESC/POS de los tickets (ADR 0033 D5) — módulo PURO compartido backend/agente.

Recibe el payload DETERMINISTA del trabajo (R1) y escribe sobre un objeto printer de
`python-escpos` (real en el agente, `Dummy` en tests/golden). Perfil conservador: solo comandos
universales (texto, negrita, tamaño, corte) — las térmicas genéricas chinas difieren en lo demás.

Anchos: 80mm ≈ 48 columnas, 58mm ≈ 32 columnas (fuente A estándar).
"""
from __future__ import annotations

COLUMNAS = {80: 48, 58: 32}


def _linea(printer, char: str = "-", ancho: int = 80) -> None:
    printer.text(char * COLUMNAS[ancho] + "\n")


def _fila(izq: str, der: str, ancho: int) -> str:
    """Dos columnas alineadas al ancho del papel (izquierda recortada si no cabe)."""
    cols = COLUMNAS[ancho]
    espacio = cols - len(der) - 1
    return f"{izq[:espacio]:<{espacio}} {der}\n"


def _cabecera(printer, titulo: str, subtitulo: str | None, ancho: int) -> None:
    printer.set(align="center", bold=True, double_height=True, double_width=True)
    printer.text(titulo + "\n")
    printer.set(align="center", bold=False, double_height=False, double_width=False)
    if subtitulo:
        printer.text(subtitulo + "\n")
    printer.set(align="left")
    _linea(printer, "=", ancho)


def render_comanda(printer, payload: dict, *, ancho: int = 80) -> None:
    """Comanda de cocina: la razón de ser son los MODIFICADORES en grande ("SIN CEBOLLA")."""
    origen = (payload.get("origen") or "").upper()
    cliente = payload.get("cliente") or ""
    _cabecera(printer, payload.get("zona", "COCINA").upper(), None, ancho)
    printer.set(bold=True)
    printer.text(f"{cliente or origen}\n")
    printer.set(bold=False)
    if cliente and origen:
        printer.text(f"({origen})\n")
    _linea(printer, "-", ancho)
    for item in payload.get("items", []):
        printer.set(bold=True, double_height=True)
        printer.text(f"{item['cantidad']} x {item['nombre']}\n")
        printer.set(bold=False, double_height=False)
        for mod in item.get("modificadores") or []:
            # El modificador va DESTACADO: es lo que la cocina no puede pasar por alto.
            printer.set(bold=True, double_height=True, double_width=True)
            printer.text(f"  >> {mod['opcion'].upper()}\n")
            printer.set(bold=False, double_height=False, double_width=False)
    if payload.get("notas"):
        _linea(printer, "-", ancho)
        printer.set(bold=True)
        printer.text(f"NOTA: {payload['notas']}\n")
        printer.set(bold=False)
    printer.cut()


def _pesos(v: str) -> str:
    """'52000' → '$52.000' (separador de miles es-CO)."""
    entero = int(float(v))
    return "$" + f"{entero:,}".replace(",", ".")


def render_precuenta(printer, payload: dict, *, ancho: int = 80, negocio: str | None = None) -> None:
    """Precuenta (no fiscal). Propina Ley 1935/2018: SUGERIDA, jamás sumada al total."""
    _cabecera(printer, negocio or "PRECUENTA", payload.get("cliente"), ancho)
    for item in payload.get("items", []):
        printer.text(_fila(
            f"{item['cantidad']} x {item['nombre']}", _pesos(item["subtotal"]), ancho
        ))
        for mod in item.get("modificadores") or []:
            printer.text(f"   - {mod['opcion']}\n")
    _linea(printer, "-", ancho)
    printer.set(bold=True)
    printer.text(_fila("TOTAL", _pesos(payload["total"]), ancho))
    printer.set(bold=False)
    printer.text("Precios incluyen INC 8%\n")
    _linea(printer, "-", ancho)
    # Ley 1935/2018: voluntaria, sugerida max 10%, el cliente decide. NUNCA sumada por defecto.
    sugerida = _pesos(str(int(float(payload["total"]) * 0.10)))
    printer.text(f"Propina sugerida (10%): {sugerida}\n")
    printer.text("Es VOLUNTARIA: usted decide si la paga,\nla aumenta o la elimina.\n")
    printer.text("* Documento no fiscal *\n")
    printer.cut()


def render_comprobante(printer, payload: dict, *, ancho: int = 80, negocio: str | None = None) -> None:
    """Comprobante de venta (no fiscal mientras `pos_electronico` este off)."""
    _cabecera(printer, negocio or "COMPROBANTE", f"Venta #{payload.get('consecutivo', '')}", ancho)
    printer.text(f"Fecha: {payload.get('fecha', '')}\n")
    _linea(printer, "-", ancho)
    for item in payload.get("items", []):
        printer.text(_fila(
            f"{item['cantidad']} x {item['nombre']}", _pesos(item["subtotal"]), ancho
        ))
    _linea(printer, "-", ancho)
    printer.set(bold=True)
    printer.text(_fila("TOTAL", _pesos(payload["total"]), ancho))
    printer.set(bold=False)
    if payload.get("metodo_pago"):
        printer.text(f"Pago: {payload['metodo_pago']}\n")
    printer.text("* Documento no fiscal *\n")
    printer.cut()


RENDERS = {
    "comanda": render_comanda,
    "precuenta": render_precuenta,
    "comprobante": render_comprobante,
}


def render_trabajo(printer, trabajo_payload: dict, *, ancho: int = 80, negocio: str | None = None) -> None:
    """Rutea al render del tipo. `tipo` viene DENTRO del payload (R1)."""
    tipo = trabajo_payload.get("tipo", "comanda")
    render = RENDERS.get(tipo, render_comanda)
    if tipo == "comanda":
        render(printer, trabajo_payload, ancho=ancho)
    else:
        render(printer, trabajo_payload, ancho=ancho, negocio=negocio)
