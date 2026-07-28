"""Parser Bancolombia (modules/bancos/gmail/parser.py) — funciones puras, sin red.

Cuerpos representativos de los formatos reales del bot viejo: QR, Nequi, PSE, consignación, salida de
dinero (descartada), y los dos formatos de monto (americano '2,000.00' y colombiano '1.500.000').
"""
import base64

from modules.bancos.gmail import parser


def _headers(from_val, subject):
    return [{"name": "From", "value": from_val}, {"name": "Subject", "value": subject}]


def _payload_html(html: str) -> dict:
    data = base64.urlsafe_b64encode(html.encode("utf-8")).decode().rstrip("=")
    return {"parts": [{"mimeType": "text/html", "body": {"data": data}}]}


# --------------------------- detección de remitente -----------------------
def test_es_transferencia_entrante_bancolombia():
    assert parser.es_transferencia_entrante(
        _headers("notificaciones@bancolombia.com.co", "Recibiste una transferencia"))


def test_no_bancolombia_se_descarta():
    assert not parser.es_transferencia_entrante(
        _headers("promos@otrobanco.com", "Recibiste una transferencia"))


def test_subject_generico_se_procesa_igual():
    # Bancolombia usa subjects genéricos: si viene de su dominio, se procesa aunque el subject no matchee.
    assert parser.es_transferencia_entrante(
        _headers("alertas@bancolombia.com.co", "Alertas y Notificaciones"))


# --------------------------- dirección del dinero -------------------------
def test_entrada_por_recibiste_un_pago():
    assert parser.es_dinero_entrante("Recibiste un pago de JUAN por $10.000")


def test_salida_se_descarta():
    assert not parser.es_dinero_entrante("Transferiste $50.000 desde tu cuenta *1234")


def test_compra_se_descarta():
    assert not parser.es_dinero_entrante("Realizaste una compra por $30.000 con tu tarjeta")


def test_sin_direccion_clara_asume_entrada():
    assert parser.es_dinero_entrante("Movimiento en tu cuenta por $5.000")


# --------------------------- extracción de campos -------------------------
def test_parseo_qr_formato_americano():
    body = ("Bancolombia: F punto rojo, recibiste un pago de FARID DAVID MALO HERNANDEZ por $2,000.00 "
            "en tu cuenta *3891 conectado a la llave 0046052593 el 24/04/2026 a las 14:02. "
            "Con codigo QR es facil y de una.")
    d = parser.parsear_email_bancolombia(body)
    assert d["monto"] == 2000 and d["monto_str"] == "$2.000"
    assert d["remitente"] == "FARID DAVID MALO HERNANDEZ"
    assert d["cuenta"] == "*3891" and d["llave"] == "0046052593"
    assert d["tipo"] == "Código QR"
    assert d["fecha_str"] == "24/04/2026" and d["hora"] == "14:02"


def test_parseo_monto_colombiano_millones():
    d = parser.parsear_email_bancolombia("recibiste un pago de MARIA por $1.500.000 en tu cuenta *0001")
    assert d["monto"] == 1500000 and d["monto_str"] == "$1.500.000"


def test_parseo_nequi():
    d = parser.parsear_email_bancolombia("Recibiste un pago de PEDRO por $8.000 vía Nequi")
    assert d["tipo"] == "Nequi" and d["monto"] == 8000


def test_extraer_body_prefiere_html():
    d = parser.parsear_email_bancolombia(
        parser.extraer_body(_payload_html("<p>recibiste un pago de <b>ANA</b> por $3.000</p>")))
    assert d["monto"] == 3000 and "ANA" in d["remitente"]


# --------------------------- construcción del mensaje ---------------------
def test_construir_mensaje_incluye_monto_y_remitente():
    datos = parser.parsear_email_bancolombia(
        "recibiste un pago de LUIS por $12.000 en tu cuenta *3891 el 01/07/2026 a las 09:30")
    msg = parser.construir_mensaje(datos, "Transferencia recibida", "10:00")
    assert "$12.000" in msg and "LUIS" in msg and "09:30" in msg
    assert msg.startswith("🏦")


def test_construir_mensaje_nequi_encabezado():
    datos = parser.parsear_email_bancolombia("recibiste un pago de X por $1.000 vía Nequi")
    assert parser.construir_mensaje(datos, "s", "10:00").startswith("🟣")


def test_parseo_formato_nuevo_dos_asteriscos_y_nombre_antes_de_la_cuenta():
    """Correo real de producción (2026-07-27). Bancolombia cambió la plantilla.

    Dos diferencias con el formato viejo que rompían el parseo, con la ingesta funcionando:
    la cuenta va enmascarada con DOS asteriscos, y el nombre viene ANTES de la cuenta
    ("de X en tu cuenta") en vez de antes del monto.
    """
    body = ("¡Listo! Todo salió bien con tus movimientos Bancolombia: Recibiste una transferencia "
            "por $10,000 de ANDRES MALO en tu cuenta **6485, el 27/07/2026 a las 22:46. "
            "Si tienes dudas, hablemos: 018000931987.")
    d = parser.parsear_email_bancolombia(body)
    assert d["monto"] == 10000 and d["monto_str"] == "$10.000"
    assert d["remitente"] == "ANDRES MALO"        # sin el "en tu" pegado atrás
    assert d["cuenta"] == "*6485"                 # el segundo asterisco ya no lo rompe
    assert d["fecha_str"] == "27/07/2026" and d["hora"] == "22:46"


def test_el_css_del_correo_no_contamina_el_parseo():
    """Los correos reales traen ~5 KB de <style> delante. Sin quitar el bloque, los patrones
    genéricos de monto y hora encuentran basura del CSS antes que el dato real."""
    body = ("<style>@media only screen and (max-width: 480px) { td { width: 100%; padding: 0 0.00; } }"
            " .x { content: '$99,999'; line-height: 12:00; }</style>"
            "<p>Recibiste una transferencia por $10,000 de ANDRES MALO en tu cuenta **6485, "
            "el 27/07/2026 a las 22:46.</p>")
    d = parser.parsear_email_bancolombia(parser.extraer_body(_payload_html(body)))
    assert d["monto"] == 10000
    assert d["remitente"] == "ANDRES MALO" and d["cuenta"] == "*6485" and d["hora"] == "22:46"
