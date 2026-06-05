# Smoke manual — Dashboard (Fase 11, cierre E7)

Confirmación VISUAL en navegador del flujo crítico. El E2E HTTP automatizado vive en
`tests/test_e2e_dashboard.py` (login → resumen → SSE → venta → resumen); esto cubre lo que el
ASGITransport no puede: el SSE real por HTTP y el render/tematizado en el navegador.

> Requiere Postgres + Redis arriba y un tenant (Punto Rojo) provisionado en el control DB
> (`docs/onboarding-tenant.md` / `python -m tools.migrate_tenants`).

## 1. Build del dashboard
```bash
cd dashboard
npm install          # primera vez
npm run build        # genera dashboard/dist (index.html + assets/)
```
La API sirve `dashboard/dist` automáticamente si existe (catch-all SPA; ver `apps/api/main.py`).

## 2. Variables de entorno del SPA (dev)
Crear `dashboard/.env` a partir de `.env.example`:
```
VITE_TENANT_SLUG=puntorojo          # se manda como X-Tenant-Slug SOLO en dev
VITE_TELEGRAM_BOT_USERNAME=<bot_de_puntorojo>   # sin @
```
> En producción la empresa se resuelve por subdominio; `VITE_TENANT_SLUG` no se usa.

## 3. Arrancar la API (con el tenant provisionado)
```bash
uvicorn apps.api.main:app --reload --port 8000
```
Dev con Vite (HMR): `cd dashboard && npm run dev` (proxy `/api/v1` → `localhost:8000`, sin reescribir
prefijo). Prod-like: abrir directamente `http://localhost:8000/` (sirve el build).

## 4. Entrar (auth)
- **Telegram real:** botón del Login Widget (necesita `VITE_TELEGRAM_BOT_USERNAME` y que el
  `telegram_id` esté en `usuarios` de la empresa, activo).
- **Escape hatch dev (sin Telegram):** en `/login`, pegar un JWT válido del tenant y "Entrar".
  Generarlo, por ejemplo:
  ```bash
  .venv/Scripts/python.exe -c "from core.auth import create_access_token; print(create_access_token(user_id=<id>, tenant='puntorojo', rol='admin'))"
  ```
  (visible solo con `import.meta.env.DEV`; oculto en el build de prod).

## 5. Checklist visual
- [ ] **Theming white-label:** la marca del Sidebar y los acentos usan `--color-primary` de
      `GET /config` (branding `color_primario`). Cambiar el branding del tenant → recargar → cambia.
- [ ] **Gating de tabs:** con `facturacion_electronica` OFF, NO aparecen Facturación / Libro IVA /
      Compras Fiscal; con la feature ON, aparecen. Núcleo siempre visible.
- [ ] **Hoy:** KPIs del día (ventas, pedidos, ticket), métodos de pago, últimas ventas, stock bajo.
- [ ] **Ventas rápidas:** buscar producto → agregar al carrito → Registrar venta → toast de éxito,
      carrito limpio.
- [ ] **En vivo (SSE):** con Hoy o Historial abierto en OTRA pestaña, registrar una venta → ambas
      se actualizan solas (sin recargar). Cortar y restaurar la red → toast "Conexión restablecida".
- [ ] **Historial:** rango de fechas; expandir una venta muestra sus líneas (GET /ventas/{id}).
- [ ] **Caja:** abrir caja → registrar movimiento → cerrar caja (muestra la diferencia).
- [ ] **Gastos:** registrar un gasto (requiere caja abierta) → aparece en la lista del día.
- [ ] **Clientes:** buscar, crear (dedup avisa si el documento ya existe); con la feature fiscal,
      aparecen los selectores de ciudad/país (MATIAS).

## 6. Diferido a Fase 12 (no es defecto del MVP)
CRUD de inventario (crear/editar/eliminar, fracciones, mayorista), tabs fiscales completos
(Facturación, Libro IVA, Compras fiscal, Proveedores, FE recibidas, Kárdex), reportes pesados
(Resultados, Top productos), `VistaMes` rica (heatmap), selector de vendedor para admin.
