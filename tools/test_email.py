"""Verificador de la config de email (Brevo) — envía un correo de PRUEBA y reporta el resultado.

    python -m tools.test_email destino@ejemplo.com

Usa los MISMOS settings y el MISMO cuerpo que el flujo de reset de producción (core.email), pero hace
la llamada de forma RUIDOSA: imprime el status HTTP y, si falla, el cuerpo del error de Brevo (a
diferencia del sender de prod, que es best-effort y traga los fallos). Sirve para validar, tras
configurar la cuenta: API key correcta, remitente en un dominio AUTENTICADO (SPF/DKIM), y que el correo
llega. El enlace del correo NO es un token real (lleva un marcador): es solo para ver que llega.

NUNCA imprime la API key. Código de salida 0 si Brevo aceptó el envío (2xx), 1 si no.
"""
from __future__ import annotations

import argparse
import sys

import httpx

from core.config import get_settings
from core.email.sender import _BREVO_URL, construir_payload


def main(argv: list[str] | None = None) -> int:
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    parser = argparse.ArgumentParser(description="Envía un email de prueba con la config de Brevo.")
    parser.add_argument("destino", help="email al que enviar la prueba (una bandeja que puedas revisar)")
    args = parser.parse_args(argv)

    s = get_settings()
    if not s.brevo_api_key:
        print("BREVO_API_KEY está vacío: el flujo caería al LogSender (no envía).", file=sys.stderr)
        print("Setea BREVO_API_KEY (y EMAIL_FROM) en el entorno y reintenta.", file=sys.stderr)
        return 1

    enlace = f"{s.reset_password_url}?token=PRUEBA-NO-VALIDA"
    payload = construir_payload(s.email_from, s.email_from_nombre, args.destino, enlace)
    headers = {"api-key": s.brevo_api_key, "Content-Type": "application/json", "accept": "application/json"}

    print(f"Enviando prueba: from={s.email_from!r} → to={args.destino!r} …")
    try:
        resp = httpx.post(_BREVO_URL, json=payload, headers=headers, timeout=15)
    except httpx.HTTPError as exc:
        print(f"ERROR de red al hablar con Brevo: {exc}", file=sys.stderr)
        return 1

    if 200 <= resp.status_code < 300:
        print(f"OK (HTTP {resp.status_code}). Revisa la bandeja de {args.destino} (y spam).")
        return 0

    # Brevo devuelve el detalle del error en el cuerpo (p. ej. remitente no autorizado, key inválida).
    print(f"FALLÓ (HTTP {resp.status_code}). Respuesta de Brevo:", file=sys.stderr)
    print(resp.text[:1000], file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
