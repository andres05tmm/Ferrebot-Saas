"""Golden tests de las 3 plantillas de tickets × 2 anchos (R3, goal §A.3) — carta Siriuss.

El render es DETERMINISTA: mismo payload → mismo buffer ESC/POS, byte a byte. Los goldens viven
en `tests/golden/impresion/` (buffer .bin + render texto .txt). Si un cambio de plantilla es
INTENCIONAL, regenerar con:  UPDATE_GOLDEN=1 pytest tests/test_plantillas_golden.py

Condicionales R3: la precuenta JAMÁS suma la propina al total; leyenda INC solo con
`con_inc`; caracteres es-CO correctos (tildes, ñ, $ con puntos de miles).
"""
import os
import re
from pathlib import Path

from escpos.printer import Dummy

from modules.impresion.render import render_trabajo

GOLDEN_DIR = Path("tests/golden/impresion")

# Payloads fijos armados con la carta Siriuss REAL (docs/fixtures/carta-siriuss/carta.yaml):
# Plato fuerte del día $19.000 (Proteína: Salpicón de jurel; Acompañantes: Tajadas + Lentejas),
# Sopa de hueso $14.000, Menú especial $21.000. Precios finales con INC incluido (ADR 0032 D2).

COMANDA = {
    "tipo": "comanda", "pedido_id": 87, "comanda_id": 121, "zona": "cocina",
    "origen": "whatsapp", "cliente": "Doña Marta", "notas": "sin ají", "hora": "12:35",
    "items": [
        {"nombre": "Plato fuerte del día", "cantidad": "2", "modificadores": [
            {"grupo": "Proteína", "opcion": "Salpicón de jurel", "delta_precio": "0.00"},
            {"grupo": "Acompañantes", "opcion": "Tajadas", "delta_precio": "0.00"},
            {"grupo": "Acompañantes", "opcion": "Lentejas", "delta_precio": "0.00"},
        ]},
        {"nombre": "Sopa de hueso", "cantidad": "1", "modificadores": []},
    ],
}

PRECUENTA = {
    "tipo": "precuenta", "pedido_id": 87, "cliente": "Mesa 4", "origen": "mesa",
    "subtotal": "73000", "total": "73000", "costo_domicilio": "0", "con_inc": True,
    "items": [
        {"nombre": "Plato fuerte del día", "cantidad": "2", "precio_unitario": "19000",
         "subtotal": "38000", "modificadores": [
             {"grupo": "Proteína", "opcion": "Salpicón de jurel", "delta_precio": "0.00"}]},
        {"nombre": "Sopa de hueso", "cantidad": "1", "precio_unitario": "14000",
         "subtotal": "14000", "modificadores": []},
        {"nombre": "Menú especial", "cantidad": "1", "precio_unitario": "21000",
         "subtotal": "21000", "modificadores": []},
    ],
}

COMPROBANTE = {
    "tipo": "comprobante", "venta_id": 55, "consecutivo": 1042, "fecha": "2026-07-24",
    "metodo_pago": "efectivo", "subtotal": "76000", "impuestos": "0", "total": "76000",
    "items": [
        {"nombre": "Plato fuerte del día — Salpicón de jurel", "cantidad": "2",
         "precio_unitario": "19000", "subtotal": "38000"},
        {"nombre": "Sopa de hueso", "cantidad": "1", "precio_unitario": "14000",
         "subtotal": "14000"},
        {"nombre": "Menú especial", "cantidad": "1", "precio_unitario": "21000",
         "subtotal": "21000"},
        {"nombre": "Propina", "cantidad": "1", "precio_unitario": "3000", "subtotal": "3000"},
    ],
}

CASOS = [("comanda", COMANDA), ("precuenta", PRECUENTA), ("comprobante", COMPROBANTE)]

# El render texto es CONSCIENTE del codepage: magic-encode de escpos cambia de página al vuelo
# (ESC t n) para cubrir tildes/mayúsculas acentuadas (Ó vive en cp850, no en cp437).
_CODEPAGES = {0: "cp437", 13: "cp850", 16: "cp1252"}
_ESCPOS = re.compile(rb"\x1b[!EadaM].|\x1b@|\x1dV.")


def _texto(buffer: bytes) -> str:
    partes: list[str] = []
    pagina = "cp437"
    for segmento in re.split(rb"(\x1bt.)", buffer):
        if segmento.startswith(b"\x1bt"):
            pagina = _CODEPAGES.get(segmento[2], "cp437")
            continue
        partes.append(_ESCPOS.sub(b"", segmento).decode(pagina, errors="replace"))
    return "".join(partes)


def _render(payload: dict, ancho: int) -> bytes:
    d = Dummy()
    render_trabajo(d, payload, ancho=ancho, negocio="SIRIUSS — Comida Ejecutiva")
    return d.output


def test_goldens_por_plantilla_y_ancho():
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    actualizar = os.environ.get("UPDATE_GOLDEN") == "1"
    for nombre, payload in CASOS:
        for ancho in (80, 58):
            buffer = _render(payload, ancho)
            bin_path = GOLDEN_DIR / f"{nombre}_{ancho}.bin"
            txt_path = GOLDEN_DIR / f"{nombre}_{ancho}.txt"
            if actualizar or not bin_path.exists():
                bin_path.write_bytes(buffer)
                txt_path.write_text(_texto(buffer), encoding="utf-8", newline="\n")
            assert buffer == bin_path.read_bytes(), (
                f"golden desviado: {bin_path} — si el cambio de plantilla es intencional, "
                "regenerar con UPDATE_GOLDEN=1"
            )
            assert _texto(buffer) == txt_path.read_text(encoding="utf-8"), f"texto: {txt_path}"


def test_precuenta_jamas_suma_la_propina_al_total():
    texto = _texto(_render(PRECUENTA, 80))
    assert "$73.000" in texto                       # el total ES el total
    assert "Propina sugerida (10%): $7.300" in texto
    assert "VOLUNTARIA" in texto
    assert "$80.300" not in texto                   # total+propina NO existe en el ticket


def test_leyenda_inc_condicional():
    con = _texto(_render(PRECUENTA, 80))
    sin = _texto(_render({**PRECUENTA, "con_inc": False}, 80))
    assert "Precios incluyen INC 8%" in con
    assert "INC 8%" not in sin                      # ferretería (IVA): sin leyenda de impoconsumo


def test_caracteres_es_co():
    """Tildes, ñ y pesos con puntos de miles llegan bien a la térmica (encoding del perfil)."""
    buffer = _render(COMANDA, 80)
    texto = _texto(buffer)
    assert "Salpicón de jurel".upper() in texto      # modificador destacado, con tilde
    assert "Doña Marta" in texto and "ají" in texto
    texto_pre = _texto(_render(PRECUENTA, 80))
    assert "Menú especial" in texto_pre and "$21.000" in texto_pre


def test_comanda_agrupa_y_muestra_numero_y_hora():
    texto = _texto(_render(COMANDA, 80))
    assert "Pedido #87" in texto and "12:35" in texto
    assert "2 x Plato fuerte del día" in texto
    assert ">> SALPICÓN DE JUREL" in texto
