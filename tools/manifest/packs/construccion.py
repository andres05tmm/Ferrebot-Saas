"""Loader idempotente del pack Construcción (plan piped-hatching-sloth §8 — Construcciones PIM).

Siembra los cimientos del vertical de obra civil/alquiler de maquinaria sobre la BD del tenant:
  1) `parametros_legales` — la vigencia 2026 (SMMLV, auxilio de transporte, IVA y los %s de aportes/
     provisiones que alimentan el motor de nómina). Son CONSTANTES legales, no dato del manifiesto: se
     hardcodean aquí como fuente de verdad. Los %s de empleador/provisiones son PROVISIONALES hasta que
     el contador de PIM los confirme (plan §7) — marcados abajo con [DEFINIR contador].
  2) catálogos default DECLARADOS EN EL MANIFIESTO (tipos de máquina / categorías de herramienta): filas
     placeholder de arranque, provisionales hasta recibir el catálogo real del cliente (plan §7 [DEFINIR]).

Driver SYNC; la `conn` debe traer `row_factory=dict_row` (como abre `provision_from_manifest`). El
commit lo hace el llamador (un solo commit por tenant), igual que los demás loaders.

CONTRATO DE ESQUEMA (los nombres de tabla/columna ESPEJAN la migración de tenant 0043 — plan §8.3 —,
que a su vez porta la spec del cliente `01_MODELO_DATOS.md` con los mismos nombres en español). Si 0043
diverge en un nombre, el INSERT de aquí falla ruidoso al provisionar (fail-fast), no en silencio.

  parametros_legales(vigente_desde DATE UNIQUE, vigente_hasta DATE NULL, smmlv MONEY4,
    auxilio_transporte MONEY4, salud_empleado_pct, pension_empleado_pct, salud_empleador_pct,
    pension_empleador_pct, caja_compensacion_pct, sena_pct, icbf_pct, cesantias_pct,
    intereses_cesantias_pct, prima_pct, vacaciones_pct, iva_general)   -- MONEY4 = Numeric(18,4)
  maquinas(codigo UNIQUE, nombre, tipo, precio_hora_default MONEY4, costo_operacion_hora MONEY4 NULL)
  herramientas(codigo UNIQUE, nombre, categoria NULL)
  trabajadores(...)  -- existe pero NO se siembra: son personas reales (las carga el cliente), no
                        dato de arranque; sembrar trabajadores ficticios contaminaría la nómina.

Los recargos de hora extra (diurna/nocturna/dominical) NO viven en `parametros_legales` (la spec del
cliente no los persiste ahí): son constantes del motor de nómina (Fase 4), no cimiento de arranque.

Idempotencia por CLAVE NATURAL (no por id), igual que `pos`/`agenda`:
- parametros_legales: UPSERT por `vigente_desde` (una fila por periodo de vigencia; la migración 0043 lo
  declara UNIQUE). Re-correr con el mismo manifiesto ACTUALIZA la fila 2026 (no duplica); cuando el
  contador confirme los %s, se editan las constantes aquí y re-provisionar propaga el cambio.
- maquinas/herramientas: insert-si-ausente por `codigo` (su clave UNIQUE en 0043), así re-sembrar no
  duplica y agregar una fila al manifiesto la crea sin tocar las existentes. El `codigo` se deriva del
  `nombre` (slug en mayúsculas) cuando el manifiesto no lo trae — placeholder legible que no colisiona
  con la numeración real de activos (M-001…) que cargará el cliente.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date
from decimal import Decimal

from core.logging import get_logger

log = get_logger("manifest.packs.construccion")

# --- Vigencia 2026 (plan §3 grupo 1; columnas EXACTAS a la migración 0043 / spec 01_MODELO_DATOS) -----
# Valores CIERTOS 2026 (publicados): SMMLV y auxilio de transporte en pesos; IVA general 0.19; salud y
# pensión del trabajador 4% c/u (régimen general). El resto (empleador/parafiscales/provisiones) son los
# defaults provisionales de la spec, marcados [DEFINIR contador] (plan §7): se editan aquí cuando el
# contador confirme, sin cambiar la mecánica.
_VIGENTE_DESDE_2026 = date(2026, 1, 1)
_PARAMS_2026: dict[str, Decimal] = {
    "smmlv": Decimal("1750905"),
    "auxilio_transporte": Decimal("249095"),
    # Deducciones del trabajador (ciertas, régimen general).
    "salud_empleado_pct": Decimal("0.04"),
    "pension_empleado_pct": Decimal("0.04"),
    # [DEFINIR contador] — aportes del EMPLEADOR (la ARL por clase de riesgo llega con el contador y queda
    # NULL hasta entonces; los parafiscales caja/SENA/ICBF usan el default de ley).
    "salud_empleador_pct": Decimal("0.085"),
    "pension_empleador_pct": Decimal("0.12"),
    "caja_compensacion_pct": Decimal("0.04"),
    "sena_pct": Decimal("0.02"),
    "icbf_pct": Decimal("0.03"),
    # [DEFINIR contador] — provisiones prestacionales: cesantías 8.33%, intereses de cesantías 1% mensual,
    # prima 8.33%, vacaciones 4.17% (defaults de la spec).
    "cesantias_pct": Decimal("0.0833"),
    "intereses_cesantias_pct": Decimal("0.01"),
    "prima_pct": Decimal("0.0833"),
    "vacaciones_pct": Decimal("0.0417"),
    "iva_general": Decimal("0.19"),
}

_ESPACIOS_RE = re.compile(r"\s+")
_NO_CODIGO_RE = re.compile(r"[^A-Z0-9]+")


def _codigo_desde_nombre(nombre: str) -> str:
    """Deriva un `codigo` legible y determinista del nombre (slug en MAYÚSCULAS, sin tildes). Placeholder
    provisional que no choca con la numeración real de activos del cliente (M-001…). Ej.: "Compactador
    manual" → "COMPACTADOR-MANUAL"."""
    sin_tildes = unicodedata.normalize("NFKD", nombre).encode("ascii", "ignore").decode("ascii")
    return _NO_CODIGO_RE.sub("-", sin_tildes.upper()).strip("-")


def _campo(item: object, nombre: str) -> object | None:
    """Lee un campo de un item del manifiesto, ya sea objeto (pydantic) o dict. Defensivo: la forma
    exacta de la sección `packs.construccion` la fija el esquema del manifiesto (fuera de este loader)."""
    if isinstance(item, dict):
        return item.get(nombre)
    return getattr(item, nombre, None)


def _lista(seccion: object, nombre: str) -> list:
    """Sub-lista de la sección (`maquinas`/`herramientas`), tolerante a sección None o campo ausente."""
    if seccion is None:
        return []
    valor = _campo(seccion, nombre)
    return list(valor) if valor else []


def _sembrar_parametros_legales(conn) -> None:
    """UPSERT de la vigencia 2026 por `vigente_desde` (clave natural UNIQUE en 0043). Constante legal, no
    dato del manifiesto: la nómina lee de aquí y congela un snapshot al abrir cada periodo (plan §3 g.4)."""
    params = {"vigente_desde": _VIGENTE_DESDE_2026, "vigente_hasta": None, **_PARAMS_2026}
    columnas = ["vigente_desde", "vigente_hasta", *_PARAMS_2026.keys()]
    placeholders = ", ".join(f"%({c})s" for c in columnas)
    set_clause = ", ".join(f"{c}=EXCLUDED.{c}" for c in columnas if c != "vigente_desde")
    conn.execute(
        f"INSERT INTO parametros_legales ({', '.join(columnas)}) VALUES ({placeholders}) "
        f"ON CONFLICT (vigente_desde) DO UPDATE SET {set_clause}",
        params,
    )


def _existe_por_codigo(conn, tabla: str, codigo: str) -> bool:
    return conn.execute(f"SELECT 1 FROM {tabla} WHERE codigo = %s", (codigo,)).fetchone() is not None


def _sembrar_maquinas(conn, seccion: object) -> int:
    """Filas placeholder de máquina desde el manifiesto (insert-si-ausente por `codigo`). Provisionales:
    el catálogo real de máquinas lo entrega el cliente (plan §7 [DEFINIR])."""
    n = 0
    for m in _lista(seccion, "maquinas"):
        nombre = _campo(m, "nombre")
        if not nombre:
            continue
        codigo = _campo(m, "codigo") or _codigo_desde_nombre(str(nombre))
        if _existe_por_codigo(conn, "maquinas", codigo):
            continue
        # `precio_hora_default` es NOT NULL en 0043: 0 como placeholder provisional (el precio real lo pone
        # el cliente). `costo_operacion_hora` queda NULL: es el [DEFINIR] de rentabilidad neta (plan §3).
        precio = _campo(m, "precio_hora_default") or Decimal("0")
        conn.execute(
            "INSERT INTO maquinas (codigo, nombre, tipo, precio_hora_default, costo_operacion_hora) "
            "VALUES (%s, %s, %s, %s, NULL)",
            (codigo, nombre, _campo(m, "tipo"), precio),
        )
        n += 1
    return n


def _sembrar_herramientas(conn, seccion: object) -> int:
    """Filas placeholder de herramienta desde el manifiesto (insert-si-ausente por `codigo`)."""
    n = 0
    for h in _lista(seccion, "herramientas"):
        nombre = _campo(h, "nombre")
        if not nombre:
            continue
        codigo = _campo(h, "codigo") or _codigo_desde_nombre(str(nombre))
        if _existe_por_codigo(conn, "herramientas", codigo):
            continue
        conn.execute(
            "INSERT INTO herramientas (codigo, nombre, categoria) VALUES (%s, %s, %s)",
            (codigo, nombre, _campo(h, "categoria")),
        )
        n += 1
    return n


def cargar_construccion(seccion: object, conn) -> dict[str, int]:
    """Siembra los cimientos del vertical construcción (idempotente). Devuelve conteos para el resumen.

    `seccion` es la sub-sección `packs.construccion` del manifiesto (catálogos default declarados);
    puede ser None (el tenant nace solo con `parametros_legales`, que es lo no negociable). `conn` es
    una conexión psycopg SYNC con `row_factory=dict_row`; el commit lo hace el llamador.
    """
    _sembrar_parametros_legales(conn)
    n_maquinas = _sembrar_maquinas(conn, seccion)
    n_herramientas = _sembrar_herramientas(conn, seccion)
    conteos = {"parametros_legales": 1, "maquinas": n_maquinas, "herramientas": n_herramientas}
    log.info("pack_construccion_cargado", **conteos)
    return conteos
