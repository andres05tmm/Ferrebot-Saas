"""Obtener un `refresh_token` de Gmail para el buzón de ingesta de un tenant.

La pieza que faltaba del runbook (`docs/DEPLOY-RAILWAY-PILOT.md` §10.5): `tools.set_gmail_token`
asume un `<REFRESH>` ya existente, pero nadie lo emitía. Abre el consentimiento en el navegador,
captura el `code` en un loopback y canjea el refresh_token. **No toca la BD**: imprime el token para
pasárselo a `tools.set_gmail_token`, que es quien lo cifra.

El cliente OAuth debe ser de tipo *Desktop app*: Google acepta `http://127.0.0.1:<puerto>` sin
registrar la URI, así que no hay nada que configurar en GCP por cada corrida.

Uso (en la máquina del operador, necesita navegador):
    python -m tools.gmail_oauth --client-id <ID> --client-secret <SECRET>
"""
from __future__ import annotations

import argparse
import http.server
import secrets
import urllib.parse
import webbrowser

import httpx

SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_URL = "https://oauth2.googleapis.com/token"


class _Captura(http.server.BaseHTTPRequestHandler):
    """Recibe el redirect de Google y guarda `code`/`state` en la clase. Una sola petición."""

    code: str | None = None
    state: str | None = None

    def do_GET(self) -> None:  # noqa: N802 (nombre impuesto por BaseHTTPRequestHandler)
        params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        _Captura.code = (params.get("code") or [None])[0]
        _Captura.state = (params.get("state") or [None])[0]
        cuerpo = "Listo, ya podés cerrar esta pestaña." if _Captura.code else "Google no devolvió código."
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(f"<p>{cuerpo}</p>".encode())

    def log_message(self, *_args) -> None:
        """Silencia el log a stderr del servidor: la salida útil es el token."""


def obtener_refresh_token(client_id: str, client_secret: str, *, usuario: str | None = None) -> str:
    """Corre el consentimiento y devuelve el refresh_token. Lanza RuntimeError si Google no lo da."""
    estado = secrets.token_urlsafe(16)
    servidor = http.server.HTTPServer(("127.0.0.1", 0), _Captura)
    redirect_uri = f"http://127.0.0.1:{servidor.server_port}"
    consulta = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",
        # Sin `prompt=consent` Google NO reemite refresh_token si la cuenta ya autorizó la app,
        # que es exactamente el caso al rotar un token vencido.
        "prompt": "consent",
        "state": estado,
    }
    if usuario:
        consulta["login_hint"] = usuario
    url = f"{_AUTH_URL}?{urllib.parse.urlencode(consulta)}"
    print(f"Abriendo el consentimiento en el navegador. Si no abre, pegá esta URL:\n\n{url}\n")
    webbrowser.open(url)
    servidor.handle_request()
    servidor.server_close()

    if not _Captura.code:
        raise RuntimeError("Google no devolvió un código de autorización")
    if _Captura.state != estado:
        raise RuntimeError("El `state` no coincide: se descarta la respuesta")

    resp = httpx.post(_TOKEN_URL, data={
        "client_id": client_id, "client_secret": client_secret, "code": _Captura.code,
        "redirect_uri": redirect_uri, "grant_type": "authorization_code",
    })
    if resp.status_code != 200:
        raise RuntimeError(f"canje del código falló ({resp.status_code}): {resp.text}")
    refresh = resp.json().get("refresh_token")
    if not refresh:
        raise RuntimeError("Google no devolvió refresh_token (¿faltó access_type=offline?)")
    return refresh


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Obtener el refresh_token de Gmail de un buzón")
    parser.add_argument("--client-id", required=True)
    parser.add_argument("--client-secret", required=True)
    parser.add_argument("--usuario", help="email del buzón, para preseleccionar la cuenta")
    args = parser.parse_args(argv)
    try:
        token = obtener_refresh_token(args.client_id, args.client_secret, usuario=args.usuario)
    except (RuntimeError, httpx.HTTPError) as exc:
        print(f"ERROR: {exc}")
        return 1
    print(f"\nrefresh_token = {token}\n")
    print("Cargalo cifrado con:\n"
          f"  python -m tools.set_gmail_token <slug> --refresh-token {token} --email <buzon>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
