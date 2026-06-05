"""Guardarraíl de montaje: el manifiesto de rutas de la app REAL (`create_app`).

Los tests por router usan apps mínimas, así que no atrapan si un router se cae del wiring de
`create_app()`. Aquí se introspeccionan `app.routes` (sin DB, sin lifespan, sin ASGITransport) y se
exige que las rutas clave de cada router núcleo/facturación + el bootstrap + salud estén montadas.
"""
from apps.api.main import create_app

# Una ruta representativa por router montado, más salud (infra).
_RUTAS_CLAVE = frozenset({
    "/api/v1/auth/login",      # login del dashboard (Telegram → JWT)
    "/api/v1/config",          # bootstrap del dashboard
    "/api/v1/ventas",          # ventas
    "/api/v1/productos",       # inventario
    "/api/v1/caja/actual",     # caja
    "/api/v1/gastos",          # gastos
    "/api/v1/fiados/deudas",   # fiados
    "/api/v1/facturas",        # facturación
    "/health",                 # liveness
    "/ready",                  # readiness
})


def test_rutas_clave_montadas():
    rutas = {r.path for r in create_app().routes}
    faltantes = _RUTAS_CLAVE - rutas
    assert not faltantes, f"rutas no montadas en create_app(): {sorted(faltantes)}"
