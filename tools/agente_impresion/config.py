"""Config local del agente de impresión (ADR 0033 D4): mapeo zona↔impresora vive AQUÍ, en la sede.

`config.json` junto al ejecutable:

{
  "url": "https://miempresa.melquiadez.com",
  "slug": "miempresa",
  "token": "imp_...",                       // token de dispositivo (emitido en el dashboard)
  "intervalo_seg": 3,
  "impresoras": {
    "parrilla": {"tipo": "red",     "destino": "192.168.1.50",  "ancho": 80},
    "bar":      {"tipo": "windows", "destino": "POS-58",        "ancho": 58},
    "*":        {"tipo": "red",     "destino": "192.168.1.50",  "ancho": 80}   // default/fallback
  }
}

`tipo`: "red" (IP puerto 9100 — el estándar de las térmicas de restaurante), "windows"
(impresora instalada en Windows, driver genérico), "archivo" (debug). La zona "*" recibe lo
que no tenga zona mapeada — perfil conservador: mejor imprimir en la impresora equivocada que
no imprimir.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Impresora:
    tipo: str          # red | windows | archivo | dummy
    destino: str       # IP | nombre de impresora Windows | ruta de archivo
    ancho: int = 80


@dataclass(frozen=True)
class ConfigAgente:
    url: str
    slug: str
    token: str
    intervalo_seg: float = 3.0
    impresoras: dict[str, Impresora] = field(default_factory=dict)

    def impresora_para(self, zona: str | None) -> Impresora | None:
        """La impresora de la zona, o la default '*'. None = no hay dónde imprimir (se loguea)."""
        if zona and zona in self.impresoras:
            return self.impresoras[zona]
        return self.impresoras.get("*")


def cargar_config(ruta: str | Path) -> ConfigAgente:
    datos = json.loads(Path(ruta).read_text(encoding="utf-8"))
    impresoras = {
        zona: Impresora(**imp) for zona, imp in datos.get("impresoras", {}).items()
    }
    return ConfigAgente(
        url=datos["url"].rstrip("/"), slug=datos["slug"], token=datos["token"],
        intervalo_seg=float(datos.get("intervalo_seg", 3)), impresoras=impresoras,
    )
