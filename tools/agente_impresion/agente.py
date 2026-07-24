"""Agente local de impresión (ADR 0033 D5): consume SU cola y ejecuta ESC/POS.

Diseño anti-papel-doble de punta a punta (condicional R2):
- El backend re-entrega un trabajo `entregado_agente` sin ack (corte de conexión a mitad).
- El agente lleva un REGISTRO LOCAL de ids ya impresos (archivo, sobrevive reinicios): si un
  trabajo re-entregado ya está en el registro, NO se vuelve a imprimir — solo se ackea.
- El ack va DESPUÉS de imprimir: si el corte llega entre imprimir y ackear, el registro local
  evita el duplicado al reconectar.

Todo puerto (HTTP, impresoras) es inyectable: los tests corren el ciclo completo con la
impresora FALSA (`escpos.printer.Dummy`) y un cliente contra la app ASGI real.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from pathlib import Path

from tools.agente_impresion.config import ConfigAgente, Impresora

log = logging.getLogger("agente_impresion")


class RegistroImpresos:
    """Ids ya impresos, persistidos uno por línea. Chico y honesto: append-only, se poda al cargar."""

    _MAX = 5000   # ponytail: poda simple al releer; si una sede imprime más por ciclo de vida, LRU real

    def __init__(self, ruta: str | Path) -> None:
        self._ruta = Path(ruta)
        self._ids: set[int] = set()
        if self._ruta.exists():
            lineas = self._ruta.read_text(encoding="utf-8").split()
            self._ids = {int(x) for x in lineas[-self._MAX:] if x.isdigit()}

    def __contains__(self, trabajo_id: int) -> bool:
        return trabajo_id in self._ids

    def marcar(self, trabajo_id: int) -> None:
        if trabajo_id in self._ids:
            return
        self._ids.add(trabajo_id)
        with self._ruta.open("a", encoding="utf-8") as f:
            f.write(f"{trabajo_id}\n")


def _impresora_real(imp: Impresora):
    """Instancia el printer de python-escpos según el tipo (import perezoso: dummy no exige USB)."""
    if imp.tipo == "red":
        from escpos.printer import Network

        return Network(imp.destino, timeout=10)
    if imp.tipo == "windows":
        from escpos.printer import Win32Raw

        return Win32Raw(imp.destino)
    if imp.tipo == "archivo":
        from escpos.printer import File

        return File(imp.destino)
    from escpos.printer import Dummy

    return Dummy()


class AgenteImpresion:
    def __init__(
        self,
        config: ConfigAgente,
        registro: RegistroImpresos,
        *,
        http,                                        # httpx.Client o compatible (inyectable)
        fabrica_impresora: Callable | None = None,   # Impresora → printer escpos (Dummy en tests)
    ) -> None:
        self._cfg = config
        self._registro = registro
        self._http = http
        self._fabrica = fabrica_impresora or _impresora_real

    # --- HTTP ------------------------------------------------------------------------
    def _headers(self) -> dict:
        return {"X-Device-Token": self._cfg.token, "X-Tenant-Slug": self._cfg.slug}

    def _cola(self) -> list[dict]:
        r = self._http.get(f"{self._cfg.url}/api/v1/impresion/cola", headers=self._headers())
        r.raise_for_status()
        return r.json()

    def _ack(self, trabajo_id: int, ok: bool, detalle: str | None = None) -> None:
        self._http.post(
            f"{self._cfg.url}/api/v1/impresion/trabajos/{trabajo_id}/ack",
            headers=self._headers(), json={"ok": ok, "detalle": detalle},
        ).raise_for_status()

    # --- ciclo -----------------------------------------------------------------------
    def ciclo(self) -> int:
        """Un pase completo: reclama la cola, imprime lo nuevo, ackea. Devuelve # procesados."""
        from modules.impresion.render import render_trabajo

        procesados = 0
        for trabajo in self._cola():
            tid = trabajo["id"]
            if tid in self._registro:
                # Re-entrega tras corte de conexión: ya salió el papel — solo confirmar.
                log.info("trabajo %s ya impreso (re-entrega): solo ack", tid)
                self._ack(tid, ok=True)
                procesados += 1
                continue
            zona = trabajo.get("payload", {}).get("zona")
            imp = self._cfg.impresora_para(zona)
            if imp is None:
                self._ack(tid, ok=False, detalle=f"sin impresora para la zona '{zona}'")
                procesados += 1
                continue
            try:
                printer = self._fabrica(imp)
                render_trabajo(printer, trabajo["payload"], ancho=imp.ancho)
            except Exception as e:  # noqa: BLE001 — un trabajo malo no tumba el agente
                log.exception("error imprimiendo trabajo %s", tid)
                self._ack(tid, ok=False, detalle=str(e)[:400])
                procesados += 1
                continue
            # Imprimió: registrar ANTES del ack — si el ack falla (corte), el registro evita el doble.
            self._registro.marcar(tid)
            self._ack(tid, ok=True)
            procesados += 1
        return procesados

    def correr(self) -> None:
        """Loop del servicio: poll con intervalo; errores de red → backoff y seguir."""
        backoff = self._cfg.intervalo_seg
        while True:
            try:
                self.ciclo()
                backoff = self._cfg.intervalo_seg
            except Exception:  # noqa: BLE001 — red caída: reintentar, jamás morir
                log.exception("ciclo fallido; reintento en %.0fs", backoff)
                backoff = min(backoff * 2, 60)
            time.sleep(backoff)
