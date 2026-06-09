"""Loader idempotente del pack FAQ (ADR 0007 fase 2): manifiesto → tabla `conocimiento`.

Driver SYNC; la `conn` debe traer `row_factory=dict_row`. Idempotente por `titulo`: si la entrada ya
existe se ACTUALIZA (contenido/orden/activo) en vez de duplicar, de modo que re-correr con el mismo
manifiesto deja la fila igual y editar el YAML propaga el cambio. El commit lo hace el llamador.
"""
from __future__ import annotations

from core.logging import get_logger
from tools.manifest.schema import PackFaq

log = get_logger("manifest.packs.faq")


def cargar_faq(faq: PackFaq, conn) -> dict[str, int]:
    """Upserta las entradas de conocimiento (idempotente por titulo). Devuelve conteos para el resumen."""
    for e in faq.entradas:
        row = conn.execute("SELECT id FROM conocimiento WHERE titulo = %s", (e.titulo,)).fetchone()
        if row is not None:
            conn.execute(
                "UPDATE conocimiento SET contenido=%s, orden=%s, activo=true, actualizado_en=now() "
                "WHERE id=%s",
                (e.contenido, e.orden, row["id"]),
            )
        else:
            conn.execute(
                "INSERT INTO conocimiento (titulo, contenido, orden, activo) VALUES (%s,%s,%s,true)",
                (e.titulo, e.contenido, e.orden),
            )
    conteos = {"conocimiento": len(faq.entradas)}
    log.info("pack_faq_cargado", **conteos)
    return conteos
