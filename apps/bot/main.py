"""Servicio bot (Telegram, webhook por empresa). Composition root en `apps.bot.wiring`.

La ruta `/tg/{slug}` identifica la empresa (tenancy.md §1). El ensamblaje de `BotDeps` (puertos
reales: control DB per-call, Redis, RecursosBot, dispatcher) vive en `construir_deps`.
"""
import os

from fastapi import FastAPI

from apps.bot.webhook import crear_app_bot
from apps.bot.wiring import construir_deps


def crear_app() -> FastAPI:
    """App ASGI del servicio bot (como `apps/api/main.py`)."""
    return crear_app_bot(construir_deps())


app = crear_app()


def main() -> None:  # pragma: no cover - arranque del proceso
    import uvicorn

    # PORT lo inyecta Railway (docs/infra-railway.md); HOST 0.0.0.0 para escuchar en el contenedor.
    uvicorn.run(
        "apps.bot.main:app",
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8080")),
    )


if __name__ == "__main__":  # pragma: no cover
    main()
