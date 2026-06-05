"""Verificación del Telegram Login Widget (spec oficial de Telegram).

El widget entrega al frontend un payload firmado (id, first_name, last_name, username, photo_url,
auth_date, hash). La firma se valida con HMAC-SHA256 cuya clave es SHA256(bot_token) — el token del
bot de ESA empresa (secreto cifrado en `secretos_empresa`, control DB). Función PURA y testeable:
sin red, sin BD; el bot-token entra como argumento.

Spec:
  - data_check_string = pares "clave=valor" (EXCEPTO `hash`), ordenados por clave, unidos con "\n".
  - secret_key = SHA256(bot_token)  (bytes crudos, no hexdigest).
  - esperado = HMAC_SHA256(secret_key, data_check_string).hexdigest(); comparar en tiempo constante.
  - Frescura: rechazar si (ahora - auth_date) supera `max_age` segundos (anti-replay).
"""
from __future__ import annotations

import hashlib
import hmac

from core.config.timezone import now_co

# Ventana anti-replay por defecto: 24 h (recomendación de Telegram para el Login Widget).
MAX_AGE_DEFAULT = 86_400


def construir_data_check_string(datos: dict[str, object]) -> str:
    """Pares `clave=valor` (excepto `hash`), ordenados por clave y unidos con '\\n' (spec Telegram)."""
    return "\n".join(f"{clave}={valor}" for clave, valor in sorted(datos.items()) if clave != "hash")


def _hash_esperado(data_check_string: str, bot_token: str) -> str:
    """HMAC-SHA256 (hexdigest) del data_check_string con clave SHA256(bot_token)."""
    secret_key = hashlib.sha256(bot_token.encode("utf-8")).digest()
    return hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()


def _es_fresco(auth_date: object, max_age: int) -> bool:
    """True si `auth_date` (epoch UTC) no supera `max_age` segundos respecto a ahora (anti-replay)."""
    try:
        emitido = int(auth_date)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
    return int(now_co().timestamp()) - emitido <= max_age


def verificar_widget(
    datos: dict[str, object], bot_token: str, *, max_age: int = MAX_AGE_DEFAULT
) -> bool:
    """True si la firma del widget es válida y el payload es fresco; False en cualquier otro caso.

    (1) HMAC-SHA256 con clave SHA256(bot_token) sobre el data_check_string coincide con `hash`
    (comparación en tiempo constante); (2) `auth_date` dentro de `max_age` segundos.
    """
    recibido = datos.get("hash")
    if not isinstance(recibido, str) or not recibido:
        return False
    esperado = _hash_esperado(construir_data_check_string(datos), bot_token)
    if not hmac.compare_digest(esperado, recibido):
        return False
    return _es_fresco(datos.get("auth_date"), max_age)
