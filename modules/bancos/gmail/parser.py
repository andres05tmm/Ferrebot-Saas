"""Detección y extracción de campos de emails Bancolombia — funciones PURAS (port del legacy).

Cero IO: entradas = headers/body crudos de Gmail; salidas = dicts. Se prueba con los cuerpos reales
del bot viejo (QR, Nequi, PSE, consignación, salida de dinero descartada, formatos de monto). Los
regex/keywords se preservan tal cual del legacy (`routers/bancolombia_notifier.py`) para no regresar
el acierto de parseo ganado en producción.
"""
from __future__ import annotations

import base64
import html as _html_module
import re

# Fragmentos del header From de remitentes oficiales (cubre todos los dominios Bancolombia).
_SENDER_FRAGMENTS = ("bancolombia",)

# Keywords de Subject que indican movimiento (amplia: cubre subjects genéricos).
_SUBJECT_KEYWORDS = (
    "transferencia", "te transfirieron", "recibiste", "transferido", "consignación", "consignacion",
    "abono", "pse", "nequi", "daviplata", "te han transferido", "recibido", "movimiento",
    "alertas y notificaciones", "alerta", "notificacion", "notificación", "todo salio bien",
    "todo salió bien", "pago", "débito", "debito", "crédito", "credito", "transaccion", "transacción",
)

# Frases que confirman dinero que ENTRÓ (notificar).
_KEYWORDS_ENTRADA = (
    "recibiste un pago de", "recibiste un abono", "te enviaron", "te transfirieron", "consignaron",
    "te consignaron", "recibiste una consignacion", "recibiste una consignación",
)

# Frases que indican dinero que SALIÓ (descartar).
_KEYWORDS_SALIDA = (
    "transferiste", "realizaste una transferencia", "realizaste un pago", "pagaste", "enviaste",
    "debitamos", "desde tu cuenta", "compraste", "realizaste una compra", "hiciste una compra",
    "tu compra", "pago realizado", "tu pago fue", "hemos debitado", "se débito", "se debito",
    "fue debitado", "retiraste", "retiro de", "avance de",
)


def leer_headers(headers: list[dict]) -> tuple[str, str]:
    """(from, subject) en minúsculas desde payload.headers de Gmail."""
    from_val = subject = ""
    for h in headers:
        name = (h.get("name") or "").lower()
        val = (h.get("value") or "").lower()
        if name == "from":
            from_val = val
        elif name == "subject":
            subject = val
    return from_val, subject


def es_transferencia_entrante(headers: list[dict]) -> bool:
    """From de Bancolombia + (subject de movimiento O subject genérico → procesa igual)."""
    from_val, subject = leer_headers(headers)
    if not any(frag in from_val for frag in _SENDER_FRAGMENTS):
        return False
    # Subject reconocido, o genérico → se procesa igual (Bancolombia usa subjects genéricos).
    return True if any(kw in subject for kw in _SUBJECT_KEYWORDS) else True


def es_dinero_entrante(body_text: str) -> bool:
    """SALIDA → False; ENTRADA → True; sin match claro → True (no perder pagos reales)."""
    texto = body_text.lower()
    if any(kw in texto for kw in _KEYWORDS_SALIDA):
        return False
    if any(kw in texto for kw in _KEYWORDS_ENTRADA):
        return True
    return True


def extraer_body(payload: dict) -> str:
    """Body HTML (o text/plain) de un mensaje Gmail, prefiriendo HTML. Walk recursivo de las partes."""
    html_content = plain_content = ""

    def _decode(data_b64: str) -> str:
        try:
            return base64.urlsafe_b64decode(data_b64 + "==").decode("utf-8", errors="replace")
        except Exception:
            return ""

    def _walk(parts: list) -> None:
        nonlocal html_content, plain_content
        for part in parts:
            mime = part.get("mimeType", "")
            data_b64 = part.get("body", {}).get("data", "")
            if mime == "text/html" and data_b64 and not html_content:
                html_content = _decode(data_b64)
            elif mime == "text/plain" and data_b64 and not plain_content:
                plain_content = _decode(data_b64)
            if part.get("parts"):
                _walk(part["parts"])

    parts = payload.get("parts", [])
    if parts:
        _walk(parts)
    else:
        data_b64 = payload.get("body", {}).get("data", "")
        if data_b64:
            return _decode(data_b64)
    return html_content or plain_content


def _limpiar_html(texto: str) -> str:
    # `<style>`/`<script>` primero: quitar solo las etiquetas dejaría el CSS como texto, y los correos
    # de Bancolombia traen ~5 KB de hoja de estilo delante del mensaje. Patrones genéricos como
    # `\$\s*([\d][0-9.,]+)` o `(\d{1,2}:\d{2})` encuentran basura ahí antes de llegar al importe real.
    sin_bloques = re.sub(r"<(style|script)\b[^>]*>.*?</\1>", " ", texto, flags=re.IGNORECASE | re.DOTALL)
    return _html_module.unescape(re.sub(r"<[^>]+>", " ", sin_bloques))


def _extraer_valor(texto: str, patrones: list[str]) -> str:
    for patron in patrones:
        m = re.search(patron, texto, re.IGNORECASE | re.DOTALL)
        if m:
            return m.group(1).strip()
    return ""


def _normalizar_monto(monto_str_raw: str) -> tuple[int, str]:
    """'2,000.00'/'1.500.000' → (int pesos, '$2.000'). Maneja formato colombiano y americano."""
    if not monto_str_raw:
        return 0, ""
    limpio = monto_str_raw.strip()
    if re.search(r",\d{2}$", limpio):            # "2,000.00" → decimales + miles
        limpio = re.sub(r",\d{2}$", "", limpio).replace(",", "")
    elif re.search(r"\.\d{2}$", limpio):         # "2.000,00"/"2000.00"
        limpio = re.sub(r"\.\d{2}$", "", limpio).replace(".", "").replace(",", "")
    else:
        limpio = limpio.replace(".", "").replace(",", "")
    try:
        monto = int(limpio)
    except ValueError:
        monto = 0
    monto_fmt = f"${monto:,}".replace(",", ".") if monto > 0 else f"${monto_str_raw}"
    return monto, monto_fmt


def parsear_email_bancolombia(body_raw: str) -> dict:
    """Extrae monto/remitente/cuenta/llave/canal/descripción/hora/fecha del email (HTML o texto)."""
    texto = re.sub(r"\s+", " ", _limpiar_html(body_raw))

    remitente = _extraer_valor(texto, [
        r"recibiste un pago de\s+([A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑA-Za-záéíóúñ\s]{2,80}?)\s+por\s+\$",
        r"pago de\s+([A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑA-Za-záéíóúñ\s]{2,80}?)\s+por\s+\$",
        # `\s+en\s+tu\b` corta el formato "…por $10,000 de ANDRES MALO en tu cuenta **6485": sin él
        # la captura se comía el "en tu" (frena recién en "cuenta") y el nombre quedaba sucio.
        r"de[:\s]+([A-ZÁÉÍÓÚÑ][A-Za-záéíóúñ\s]{2,60}?)(?:\s+por\s+\$|\s+en\s+tu\b|\s{2,}|\||\n|cuenta|ref)",
        r"remitente[:\s]+([A-ZÁÉÍÓÚÑ][A-Za-záéíóúñ\s]{2,60}?)(?:\s{2,}|\||$)",
        r"transferido por[:\s]+([A-ZÁÉÍÓÚÑ][A-Za-záéíóúñ\s]{2,60}?)(?:\s{2,}|\||$)",
    ])
    monto, monto_fmt = _normalizar_monto(_extraer_valor(texto, [
        r"por\s+\$\s*([\d][0-9.,]+)", r"\$\s*([\d][0-9.,]+)", r"por valor de\s+\$?\s*([\d][0-9.,]+)",
        r"valor[:\s]+\$?\s*([\d][0-9.,]+)", r"monto[:\s]+\$?\s*([\d][0-9.,]+)",
    ]))
    # `\**` (no `\*?`): Bancolombia enmascara con DOS asteriscos en el formato nuevo ("cuenta **6485")
    # y con uno en el viejo ("cuenta *3891"). Con `\*?` el segundo asterisco rompía el match y la
    # cuenta quedaba vacía → todo el desglose por cuenta caía en "sin identificar".
    cuenta = _extraer_valor(texto, [
        r"en tu cuenta\s+\**(\d{3,6})", r"cuenta\s+\*+(\d{3,6})", r"cuenta destino[:\s]+\**(\d{3,6})",
    ])
    if cuenta:
        cuenta = f"*{cuenta}"
    llave = _extraer_valor(texto, [r"a la llave\s+(\d{7,15})", r"llave[:\s]+(\d{7,15})"])

    texto_lower = texto.lower()
    if "codigo qr" in texto_lower or "código qr" in texto_lower or "con qr" in texto_lower:
        tipo = "Código QR"
    elif "nequi" in texto_lower:
        tipo = "Nequi"
    elif "daviplata" in texto_lower:
        tipo = "Daviplata"
    elif "pse" in texto_lower:
        tipo = "PSE"
    elif "consign" in texto_lower:
        tipo = "Consignación"
    else:
        tipo = _extraer_valor(texto, [
            r"canal[:\s]+([^\n|]{3,40}?)(?:\s{2,}|\||$)", r"tipo[:\s]+([^\n|]{3,40}?)(?:\s{2,}|\||$)",
        ]) or "Transferencia"

    descripcion = _extraer_valor(texto, [
        r"descripci[oó]n[:\s]+([^\n|]{3,100}?)(?:\s{2,}|\||$)",
        r"referencia[:\s]+([^\n|]{3,100}?)(?:\s{2,}|\||$)",
        r"concepto[:\s]+([^\n|]{3,100}?)(?:\s{2,}|\||$)",
        r"motivo[:\s]+([^\n|]{3,100}?)(?:\s{2,}|\||$)",
    ])
    hora = _extraer_valor(texto, [
        r"a las\s+(\d{1,2}:\d{2}(?::\d{2})?)", r"(\d{1,2}:\d{2}(?::\d{2})?\s*(?:AM|PM|am|pm)?)",
        r"hora[:\s]+(\d{1,2}:\d{2}(?::\d{2})?)",
    ])
    fecha_str = _extraer_valor(texto, [
        r"el\s+(\d{2}/\d{2}/\d{4})", r"(\d{2}/\d{2}/\d{4})", r"(\d{4}-\d{2}-\d{2})",
        r"fecha[:\s]+(\d{1,2}\s+de\s+\w+\s+de\s+\d{4})",
    ])
    return {
        "monto": monto,
        "monto_str": monto_fmt or "—",
        "remitente": remitente[:100].strip() if remitente else "",
        "cuenta": cuenta[:10] if cuenta else "",
        "llave": llave[:15] if llave else "",
        "descripcion": descripcion[:200] if descripcion else "",
        "tipo": tipo[:60],
        "hora": hora[:20] if hora else "",
        "fecha_str": fecha_str[:20] if fecha_str else "",
    }


def construir_mensaje(datos: dict, subject: str, ahora_hhmm: str) -> str:
    """Mensaje de Telegram con encabezado por canal. `ahora_hhmm` = hora fallback.

    **Texto plano, sin Markdown.** Llevaba `*negritas*` y `` `código` ``, pero se envía sin
    `parse_mode`, así que Telegram los mostraba tal cual: el dueño leía los asteriscos y las comillas
    invertidas como si fueran parte del mensaje. Prender `parse_mode` sería la otra salida, pero
    entonces cualquier nombre de remitente con un `_` o un `*` rompería el formato del aviso.
    """
    tipo_lower = (datos.get("tipo") or "").lower()
    if "nequi" in tipo_lower:
        encabezado = "🟣 Transferencia recibida — Nequi"
    elif "pse" in tipo_lower:
        encabezado = "🔵 Transferencia recibida — PSE"
    elif "daviplata" in tipo_lower:
        encabezado = "🔴 Transferencia recibida — Daviplata"
    elif "consign" in tipo_lower:
        encabezado = "🏧 Consignación recibida — Bancolombia"
    else:
        encabezado = "🏦 Transferencia recibida — Bancolombia"

    lineas = [encabezado]
    if datos.get("monto", 0) > 0:
        lineas.append(f"💰 Monto: {datos['monto_str']}")
    else:
        lineas.append(f"📩 {subject[:80]}")
    if datos.get("remitente"):
        lineas.append(f"👤 De: {datos['remitente']}")
    if datos.get("cuenta"):
        lineas.append(f"🏦 Cuenta: {datos['cuenta']}")
    if datos.get("llave"):
        lineas.append(f"🔑 Llave: {datos['llave']}")
    if datos.get("tipo") and tipo_lower not in ("transferencia", ""):
        lineas.append(f"📲 Canal: {datos['tipo']}")
    if datos.get("descripcion"):
        lineas.append(f"📝 {datos['descripcion'][:120]}")
    hora_display = (datos.get("hora") or ahora_hhmm).strip()
    fecha_display = datos.get("fecha_str", "")
    lineas.append(f"📅 {fecha_display}  🕐 {hora_display}" if fecha_display else f"🕐 {hora_display}")
    return "\n".join(lineas)
