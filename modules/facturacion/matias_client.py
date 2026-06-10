"""Cliente MATIAS API v3 por empresa (un cliente por tenant; caché en la instancia).

Aísla el borde HTTP con MATIAS (auth JWT, emisión de factura, caché de ciudades) tras una clase
inyectable, con httpx PEREZOSO: importar este módulo y construir `MatiasClient` NO toca la red
(patrón CR-1, igual que `apps/bot/telegram.py` y `apps/bot/redis_stores.py`). Los parsers son
PUROS (sin red) y se prueban aislados; la orquestación se prueba con `httpx.MockTransport`.

Contrato MATIAS en `docs/facturacion-matias-extract.md` §2, §5, §10, §11 (portado de
`bot-ventas-ferreteria/services/facturacion_service.py`). La arquitectura cambia: credenciales
inyectadas por empresa y caché por instancia (no env global). Alcance E2 = SOLO el cliente; la
persistencia (ventas/`facturas_electronicas`) es E3.

`base_url` es ÚNICO: auth y API cuelgan del mismo host (`{base}/auth/login`, `{base}/invoice`,
`{base}/cities`), como en `facturacion_service.py:307`. El host `auth-v2` del doc §2 es stale.
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import httpx


def _a_json(payload: dict) -> str:
    """Serializa el payload UBL a JSON emitiendo los montos `Decimal` como JSON number (float).

    El payload de E1 lleva montos `Decimal` (no serializables por el codec JSON de httpx). Se emiten
    como número, espejo del original (`round(x, 2)` → number, aceptado por DIAN); el formato exacto
    (number vs string, nº de decimales) se confirma contra el sandbox MATIAS en E4d.
    """
    return json.dumps(payload, default=float)

# CUFE mínimo válido (FAD06, §9): `success` sin CUFE de ≥40 chars se trata como fallo.
CUFE_MIN_LEN = 40
# Expiración por defecto del token si MATIAS no informa `expires_at`/`expires_in` (segundos, §2).
_EXPIRY_DEFAULT = 86_400
# Margen para renovar el token antes de que expire (segundos, §2).
_EXPIRY_MARGEN = 60


@dataclass(frozen=True, slots=True)
class MatiasCredenciales:
    """Credenciales MATIAS de UNA empresa (descifradas en memoria por job; nunca en código/git)."""

    email: str
    password: str
    base_url: str


@dataclass(frozen=True, slots=True)
class EventoResultado:
    """Resultado de un evento RADIAN (acuse/aceptación/reclamo): `ok` + `error_msg` legible en fallo."""

    ok: bool
    error_msg: str | None = None


@dataclass(frozen=True, slots=True)
class EmisionResultado:
    """Resultado de emitir un documento: `cufe` en éxito, `error_msg` legible en fallo.

    `categoria` clasifica el desenlace para la política de reintento (E4): "aceptada" | "rechazada"
    | "error". El default "error" es un placeholder; `_parsear_emision` la fija siempre en GREEN
    (E4b actualizará el servicio para consumirla). `raw` lleva la respuesta MATIAS COMPLETA: la
    persiste el servicio como histórico fiscal (D7.3 del ADR 0012; antes solo se guardaba el cufe).
    """

    ok: bool
    cufe: str | None = None
    error_msg: str | None = None
    categoria: str = "error"
    raw: dict | None = None
    # POS electrónico (ADR 0012 D4): número/prefijo que MATIAS asigna por autoincremento al emitir;
    # el servicio los persiste en la fila `pos` (que nació con consecutivo/prefijo NULL). None en FE.
    numero: int | None = None
    prefijo: str | None = None


@dataclass(frozen=True, slots=True)
class EstadoConsulta:
    """Estado DIAN consultado en MATIAS para la reconciliación (D7.2).

    `categoria` ∈ {aceptada, rechazada, pendiente, desconocido}: la red de respaldo del webhook decide
    qué transición aplicar (o dejar como está). `cufe` cuando MATIAS ya lo devuelve."""

    categoria: str
    cufe: str | None = None
    raw: dict | None = None


# --- parsers PUROS (sin red) -------------------------------------------------

def _extraer_token(data: dict, *, ahora: float) -> tuple[str, float]:
    """Token + timestamp de expiración desde la respuesta de `/auth/login` (§2). PURO.

    Token en `token` | `access_token` | `data.token` | `data.access_token` (ninguno → ValueError).
    Expiry: `expires_at` ISO ("Z"→"+00:00") o `ahora + expires_in` (default `_EXPIRY_DEFAULT`).
    """
    anidado = data.get("data") or {}
    token = data.get("token") or data.get("access_token") or anidado.get("token") or anidado.get("access_token")
    if not token:
        raise ValueError("Respuesta de login sin token MATIAS")
    expires_at = data.get("expires_at")
    if expires_at:
        expiry = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00")).timestamp()
    else:
        expiry = ahora + float(data.get("expires_in") or _EXPIRY_DEFAULT)
    return token, expiry


def _parsear_emision(data: dict) -> EmisionResultado:
    """Parsea la respuesta de `/invoice` (§10) con pre-check FAD06 (§9). PURO.

    Éxito = `success` y CUFE (`XmlDocumentKey`|`document_key`) de ≥`CUFE_MIN_LEN` chars; si falta o es
    corto → fallo. En rechazo concatena `message` con `errors` (dict) en un `error_msg` legible.
    """
    cufe = (data.get("XmlDocumentKey") or data.get("document_key") or "").strip()
    if bool(data.get("success")):
        if not cufe or len(cufe) < CUFE_MIN_LEN:
            return EmisionResultado(False, error_msg="CUFE inválido devuelto por MATIAS API",
                                    categoria="error", raw=data)
        return EmisionResultado(True, cufe=cufe, categoria="aceptada", raw=data)
    msg = data.get("message") or ""
    errors = data.get("errors")
    if isinstance(errors, dict) and errors:
        error_msg = f"{msg} | " + " | ".join(f"{k}: {v}" for k, v in errors.items())
    else:
        error_msg = msg or str(data)
    return EmisionResultado(False, error_msg=error_msg, categoria="rechazada", raw=data)


# Claves donde MATIAS suele exponer las URLs del documento (verbatim del portal + alias defensivos;
# el nombre exacto se confirma contra el sandbox en F2.4 — `urls_documento` tolera las variantes).
_CLAVES_XML_URL = ("urlinvoicexml", "url_xml", "xml_url", "urlxml")
_CLAVES_PDF_URL = ("urlinvoicepdf", "url_pdf", "pdf_url", "urlpdf")


def urls_documento(dian_respuesta: dict | None) -> tuple[str | None, str | None]:
    """(xml_url, pdf_url) extraídas de la respuesta MATIAS guardada (D7.3). PURO; tolera claves ausentes."""
    if not isinstance(dian_respuesta, dict):
        return None, None
    xml = next((dian_respuesta[k] for k in _CLAVES_XML_URL if dian_respuesta.get(k)), None)
    pdf = next((dian_respuesta[k] for k in _CLAVES_PDF_URL if dian_respuesta.get(k)), None)
    return xml, pdf


def _parsear_secret_webhook(data: dict) -> str:
    """Extrae el secret de firma de la respuesta de registro del webhook MATIAS (§webhooks). PURO.

    MATIAS lo muestra UNA sola vez al registrar; se busca en claves comunes (`secret`/`signing_secret`/
    `webhook_secret`) en la raíz o bajo `data`. Sin secret → ValueError (no se puede verificar la firma)."""
    anidado = data.get("data") if isinstance(data.get("data"), dict) else {}
    for clave in ("secret", "signing_secret", "webhook_secret", "key"):
        valor = data.get(clave) or anidado.get(clave)
        if valor:
            return str(valor)
    raise ValueError("Respuesta de registro de webhook MATIAS sin secret")


def _parsear_estado(data: dict) -> EstadoConsulta:
    """Clasifica la consulta de estado DIAN de MATIAS (§11) para la reconciliación. PURO; defensivo.

    aceptada = validada (`is_valid`/`valid`/`success` truthy o `document_status==1`); rechazada = flag de
    validez explícito en False / `document_status==2`; `document_status==0` (sin validar) = pendiente; si
    no hay ninguna señal → desconocido (no se toca el estado). El shape exacto se confirma en sandbox (F2.4)."""
    cufe = (data.get("XmlDocumentKey") or data.get("document_key") or data.get("cufe") or "").strip() or None
    status = data.get("document_status")
    validez = data.get("is_valid")
    if validez is None:
        validez = data.get("valid")
    if validez is None:
        validez = data.get("success")
    if validez is True or status == 1:
        return EstadoConsulta("aceptada", cufe=cufe, raw=data)
    if validez is False or status == 2:
        return EstadoConsulta("rechazada", cufe=cufe, raw=data)
    if status == 0:
        return EstadoConsulta("pendiente", cufe=cufe, raw=data)
    return EstadoConsulta("desconocido", cufe=cufe, raw=data)


def _digitos(valor) -> int | None:
    """Entero desde un número que puede traer prefijo embebido (POS1024 → 1024). None si no hay dígitos."""
    if valor is None:
        return None
    d = "".join(c for c in str(valor) if c.isdigit())
    return int(d) if d else None


def _parsear_emision_pos(data: dict) -> EmisionResultado:
    """Parsea la respuesta del POS por autoincremento (§/auto-increment). PURO.

    Reusa el desenlace de `_parsear_emision` (success + CUDE ≥40 con FAD06) y además extrae el NÚMERO y
    PREFIJO que MATIAS asignó (`number`/`prefix`, o anidados en `document`/`data`) para persistirlos en la
    fila `pos`. Si el shape difiere del de `/invoice`, este es el punto único a ajustar contra el sandbox."""
    base = _parsear_emision(data)
    cuerpo = data.get("document") or data.get("data") or data
    numero = _digitos(cuerpo.get("number") or cuerpo.get("document_number") or cuerpo.get("consecutivo"))
    prefijo = cuerpo.get("prefix") or cuerpo.get("prefijo")
    return EmisionResultado(
        base.ok, cufe=base.cufe, error_msg=base.error_msg, categoria=base.categoria, raw=base.raw,
        numero=numero, prefijo=(str(prefijo) if prefijo else None),
    )


def _parsear_evento(data: dict) -> EventoResultado:
    """Parsea la respuesta de un evento RADIAN (`/events/...`, §14). PURO.

    Éxito = `success` truthy. En fallo arma un `error_msg` legible con `message` + `errors` (dict),
    espejo de `_parsear_emision`. Sin red: se prueba aislado y con `httpx.MockTransport`.
    """
    if bool(data.get("success")):
        return EventoResultado(True)
    msg = data.get("message") or ""
    errors = data.get("errors")
    if isinstance(errors, dict) and errors:
        error_msg = f"{msg} | " + " | ".join(f"{k}: {v}" for k, v in errors.items())
    else:
        error_msg = msg or str(data)
    return EventoResultado(False, error_msg=error_msg)


def _parsear_ciudades(data: dict) -> dict[int, str]:
    """Construye {dane_code:int → matias_id:str} desde `/cities` (§5). PURO.

    Códigos en `dataRecords.data` o `data`; cada `code`|`dane_code`|`municipality_code` mapea a
    `str(id)`. Entradas inválidas (code no numérico, sin `id`, None) se saltan.
    """
    cities = (data.get("dataRecords", {}) or {}).get("data", []) or data.get("data", []) or []
    resultado: dict[int, str] = {}
    for city in cities:
        code = city.get("code") or city.get("dane_code") or city.get("municipality_code")
        try:
            resultado[int(str(code))] = str(city["id"])
        except (ValueError, KeyError, TypeError):
            continue
    return resultado


def _ciudades_raw(data: dict) -> list[dict]:
    """Lista cruda de ciudades desde `/cities` (`dataRecords.data` o `data`). PURO."""
    return (data.get("dataRecords", {}) or {}).get("data", []) or data.get("data", []) or []


def _parsear_ciudades_full(data: dict, pais_id: int) -> list[dict]:
    """Ciudades para los selectores del dashboard (espeja `get_ciudades_list` del original). PURO.

    Cada ítem: `{matias_id, dane_code, nombre, departamento, pais_id}`. Entradas sin `id` se saltan;
    un `code` no numérico cae a `dane_code=0` (no rompe la lista).
    """
    resultado: list[dict] = []
    for city in _ciudades_raw(data):
        try:
            matias_id = str(city["id"])
        except (KeyError, TypeError):
            continue
        code = city.get("code") or city.get("dane_code") or city.get("municipality_code")
        try:
            dane = int(str(code)) if code else 0
        except (ValueError, TypeError):
            dane = 0
        resultado.append({
            "matias_id": matias_id,
            "dane_code": dane,
            "nombre": city.get("name_city") or city.get("name") or "",
            "departamento": (city.get("department") or {}).get("name_department", ""),
            "pais_id": pais_id,
        })
    return resultado


def _parsear_paises(data: dict) -> list[dict]:
    """Países desde `/countries` (espeja `get_paises_list` del original). PURO.

    Cada ítem: `{matias_id, codigo_a2, nombre, telefono_codigo}`. Entradas sin `id` se saltan.
    """
    raw = (
        (data.get("dataRecords", {}) or {}).get("data", [])
        or data.get("data", [])
        or (data if isinstance(data, list) else [])
    )
    resultado: list[dict] = []
    for p in raw:
        if not p.get("id"):
            continue
        resultado.append({
            "matias_id": p.get("id"),
            "codigo_a2": p.get("abbreviation_A2") or p.get("abbreviation_a2") or "",
            "nombre": p.get("country_name") or p.get("name") or "",
            "telefono_codigo": p.get("phone_code") or "",
        })
    return resultado


# --- cliente por empresa (httpx perezoso) ------------------------------------

class MatiasClient:
    """Cliente MATIAS de UNA empresa: auth con caché, emisión y caché de ciudades (todo perezoso)."""

    def __init__(self, cred: MatiasCredenciales, *, client: httpx.AsyncClient | None = None) -> None:
        """Guarda credenciales y cliente inyectado; NO crea httpx ni toca red. Caché vacía (CR-1)."""
        self._cred = cred
        self._client = client
        self._token_val: str | None = None
        self._token_expiry: float = 0.0
        self._ciudades: dict[int, str] | None = None
        self._ciudades_full: dict[int, list[dict]] = {}   # por pais_id (selectores del dashboard)
        self._paises: list[dict] | None = None
        self._token_lock = asyncio.Lock()
        self._ciudades_lock = asyncio.Lock()
        self._ciudades_full_lock = asyncio.Lock()
        self._paises_lock = asyncio.Lock()

    def _get_client(self) -> httpx.AsyncClient:
        """Cliente httpx perezoso y MEMOIZADO (uno por instancia atado al `base_url`); no abre red."""
        import httpx

        if self._client is None:
            self._client = httpx.AsyncClient(base_url=self._cred.base_url)
        return self._client

    async def _token(self) -> str:
        """JWT con caché por instancia, renovado `_EXPIRY_MARGEN` s antes de expirar; login perezoso (§2)."""
        async with self._token_lock:
            ahora = time.time()
            if self._token_val and ahora < self._token_expiry - _EXPIRY_MARGEN:
                return self._token_val
            resp = await self._get_client().post(
                "/auth/login",
                json={"email": self._cred.email, "password": self._cred.password, "remember_me": 0},
                headers={"Accept": "application/json", "Content-Type": "application/json"},
                follow_redirects=True, timeout=15,
            )
            resp.raise_for_status()
            try:
                data = resp.json()
            except ValueError as exc:
                raise ValueError("Respuesta de login MATIAS vacía o no-JSON") from exc
            self._token_val, self._token_expiry = _extraer_token(data, ahora=ahora)
            return self._token_val

    async def emitir_factura(self, payload: dict) -> EmisionResultado:
        """POST `/invoice` con Bearer token; devuelve `EmisionResultado` (NO persiste; eso es E3, §7/§10)."""
        tok = await self._token()
        resp = await self._get_client().post(
            "/invoice", content=_a_json(payload),
            headers={"Authorization": f"Bearer {tok}", "Accept": "application/json",
                     "Content-Type": "application/json"},
            timeout=30,
        )
        return _parsear_emision(resp.json())

    async def consultar_estado(
        self, *, prefijo: str | None, consecutivo: int, resolution: str | None = None
    ) -> EstadoConsulta:
        """GET `/status` por número+prefijo (§11): estado DIAN del documento para la reconciliación (D7.2).

        Red de respaldo del webhook: NO persiste (eso es E3). Lanza en error HTTP (el servicio lo trata
        como 'no se pudo consultar' y deja la factura como está)."""
        tok = await self._token()
        params = {"number": str(consecutivo)}
        if prefijo:
            params["prefix"] = prefijo
        if resolution:
            params["resolution"] = resolution
        resp = await self._get_client().get(
            "/status", params=params,
            headers={"Authorization": f"Bearer {tok}", "Accept": "application/json"},
            timeout=20,
        )
        resp.raise_for_status()
        return _parsear_estado(resp.json())

    async def emitir_pos(self, payload: dict) -> EmisionResultado:
        """POST `/auto-increment/pos-documents` (ADR 0012 D4): emite el POS y MATIAS asigna número/prefijo.

        Bearer token; devuelve `EmisionResultado` con `numero`/`prefijo` asignados (NO persiste; eso es E3).
        El endpoint de autoincremento elimina huecos y colisiones del consecutivo (los gestiona MATIAS)."""
        tok = await self._token()
        resp = await self._get_client().post(
            "/auto-increment/pos-documents", content=_a_json(payload),
            headers={"Authorization": f"Bearer {tok}", "Accept": "application/json",
                     "Content-Type": "application/json"},
            timeout=30,
        )
        return _parsear_emision_pos(resp.json())

    async def registrar_webhook(
        self, callback_url: str, *, events: list[str], registro_url: str | None = None
    ) -> str:
        """Registra el webhook en MATIAS (`POST /webhooks`) y devuelve el SECRET de firma (D7.1).

        `registro_url` permite una URL ABSOLUTA (p. ej. `{base}/ubl2.1/webhooks`) cuando el endpoint de
        registro no cuelga del `base_url` del cliente; si es None usa `/webhooks` relativo. Bearer token.
        Lo usa `tools.registrar_webhook_matias`; NO persiste (el tool guarda el secret cifrado)."""
        tok = await self._token()
        resp = await self._get_client().post(
            registro_url or "/webhooks",
            json={"url": callback_url, "events": events},
            headers={"Authorization": f"Bearer {tok}", "Accept": "application/json",
                     "Content-Type": "application/json"},
            timeout=30,
        )
        resp.raise_for_status()
        return _parsear_secret_webhook(resp.json())

    async def obtener_xml(self, track_id: str) -> str:
        """GET `/documents/xml/{track_id}` (§11): XML técnico de la FE para el histórico fiscal (D7.3).

        `track_id` = CUFE/CUDE del documento. Devuelve el cuerpo XML crudo; lanza en error HTTP (el
        servicio lo traduce a reintento). NO persiste (eso es E3/repositorio)."""
        tok = await self._token()
        resp = await self._get_client().get(
            f"/documents/xml/{track_id}",
            headers={"Authorization": f"Bearer {tok}", "Accept": "application/xml"},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.text

    async def importar_track_id(self, cufe: str) -> EventoResultado:
        """POST `/events/import-track-id` {trackId: cufe} (§14): registra en MATIAS la FE recibida.

        Prerrequisito para enviar eventos RADIAN sobre esa factura. Bearer token; NO persiste.
        """
        tok = await self._token()
        resp = await self._get_client().post(
            "/events/import-track-id", json={"trackId": cufe},
            headers={"Authorization": f"Bearer {tok}", "Accept": "application/json",
                     "Content-Type": "application/json"},
            timeout=30,
        )
        return _parsear_evento(resp.json())

    async def enviar_evento(self, cufe: str, code: str, notes: str = "") -> EventoResultado:
        """POST `/events/send/{cufe}` {code, notes} (§14): envía un evento RADIAN REAL a la DIAN.

        `code`: 030 acuse · 031 reclamo · 032 recibo · 033 aceptación. Bearer token; NO persiste.
        """
        tok = await self._token()
        resp = await self._get_client().post(
            f"/events/send/{cufe}", json={"code": code, "notes": notes},
            headers={"Authorization": f"Bearer {tok}", "Accept": "application/json",
                     "Content-Type": "application/json"},
            timeout=30,
        )
        return _parsear_evento(resp.json())

    async def city_id(self, dane_code: str | int | None) -> str | None:
        """Resuelve código DANE → id MATIAS de ciudad (caché perezosa por instancia, §5)."""
        if dane_code is None:
            return None
        async with self._ciudades_lock:
            if self._ciudades is None:
                resp = await self._get_client().get("/cities", timeout=15)
                self._ciudades = _parsear_ciudades(resp.json())
        try:
            return self._ciudades.get(int(dane_code))
        except (ValueError, TypeError):
            return None

    async def listar_ciudades(self, *, pais_id: int = 45, q: str = "") -> list[dict]:
        """Ciudades del país (caché perezosa por `pais_id`), filtradas por `q` (nombre/depto), máx 50.

        Espeja `get_ciudades_list` del original: códigos DANE + nombre + departamento para los
        selectores del form de cliente (uso fiscal).
        """
        async with self._ciudades_full_lock:
            cache = self._ciudades_full.get(pais_id)
            if cache is None:
                resp = await self._get_client().get(
                    "/cities", params={"country_id": pais_id}, timeout=15
                )
                cache = _parsear_ciudades_full(resp.json(), pais_id)
                self._ciudades_full[pais_id] = cache
        ql = q.strip().lower()
        if ql:
            cache = [c for c in cache if ql in c["nombre"].lower() or ql in c["departamento"].lower()]
        return cache[:50]

    async def listar_paises(self) -> list[dict]:
        """Países de MATIAS (caché perezosa por instancia). Espeja `get_paises_list` del original.

        Nota: el original usaba un host absoluto stale para `/countries`; aquí cuelga del `base_url`
        por empresa (consistente con `/cities` y la decisión 'base_url único' de este cliente).
        """
        async with self._paises_lock:
            if self._paises is None:
                resp = await self._get_client().get("/countries", timeout=15)
                self._paises = _parsear_paises(resp.json())
        return self._paises

    async def aclose(self) -> None:
        """Cierra el cliente httpx si existe (lifecycle lo maneja la capa superior en E3)."""
        if self._client is not None:
            await self._client.aclose()
