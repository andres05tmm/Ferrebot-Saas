# Fase 9 — Feature flags efectivas + `GET /config` · plan + prompts

> Re-scopeada contra el código real. A diferencia de la Fase 8, aquí **sí hay trabajo**: el cálculo de
> capacidades y el gate ya existen, pero falta la pieza que consume el dashboard (`/config`) y su entorno.
> Cowork redacta; Andrés ejecuta en Claude Code. Ciclo RED → revisión → GREEN.

## Estado real

**Ya implementado:**
- `core/tenancy/capacidades.py::ControlCapacidades.efectivas()` = features del plan ∪ habilitados −
  deshabilitados (`empresa_features`). Compartido por API y bot.
- `core/auth/features.py`: `verificar_feature` (404 puro), `get_capacidades` (control DB per-call),
  `require_feature` (factory). Aplicado **solo** a facturación.
- `empresa_features` + `planes.limites.features` (control DB). RBAC con `super_admin` en `core/auth/rbac.py`.

**Falta (núcleo de Fase 9):**
- `GET /api/v1/config` (no existe) — arranque del dashboard: branding + features efectivas + identidad empresa.
- **Branding no se lee en ningún lado** (tabla existe, sin lector).
- **Catálogo de capacidades + validación de dependencias** (sin fuente única).
- **Caché de capacidades** (hoy control DB por request gateado).
- Endpoint admin de toggles → **diferido a Fase 13** (decisión).

## Decisiones (cerradas con Andrés)

- **D1 — Admin de toggles + auth super_admin:** **diferido a Fase 13** (panel super-admin / onboarding). En
  Fase 9 las features se fijan en provisioning/seed. Fase 9 NO construye auth super_admin.
- **D2 — Caché de capacidades:** **sí**, con **TTL corto** (~60s), espejando `core/tenancy/cache.py`
  (`ControlCache`). Sin admin-toggle en Fase 9, el TTL basta; la invalidación explícita se agrega en Fase 13
  cuando llegue el PUT.

## Desglose

| E | Entregable | Qué |
|---|---|---|
| E1 | Catálogo de capacidades | Módulo PURO: `NUCLEO`, `OPCIONALES`, `DEPENDENCIAS`, `validar_dependencias`, `capacidades_completas` (núcleo siempre on). Fuente única de `feature-flags.md`. |
| E2 | Caché de efectivas (TTL) | Cachear `efectivas(empresa_id)` con TTL ~60s (espejo de `ControlCache`); cablear `get_capacidades` para usarla. |
| E3 | `GET /api/v1/config` | Lee branding (control DB), capacidades efectivas (núcleo ∪ efectivas) e identidad de empresa. Smoke HTTP. |
| E4 | Verificación | Smoke de `/config` con empresa de features on/off (paridad con `feature-flags.md`); suite verde. |

**Criterio de cierre:** `/config` devuelve branding + features (núcleo ∪ efectivas) + empresa; el catálogo
valida dependencias; las efectivas se cachean con TTL; suite verde. (El PUT admin y la invalidación viven en
Fase 13.)

---

## E1 — Catálogo de capacidades (prompt RED para Claude Code)

```
Contexto: FerreBot SaaS, feature flags. Falta el CATÁLOGO de capacidades como fuente única (hoy el set de
features sale solo del plan/overrides en core/tenancy/capacidades.py, sin lista canónica ni validación de
dependencias). Spec en docs/feature-flags.md.

Crea un módulo PURO (p.ej. core/tenancy/catalogo.py) con:
- NUCLEO: frozenset siempre activo: ventas, inventario, caja, gastos, clientes, proveedores, reportes.
- OPCIONALES: frozenset: facturacion_electronica, documento_soporte, notas_electronicas, libro_iva,
  compras_fiscal, honorarios, fiados, mayorista, ventas_voz, bot_telegram, multi_vendedor.
- DEPENDENCIAS: mapa feature -> conjunto-requisito en modo OR (basta UNA del conjunto):
    notas_electronicas -> {facturacion_electronica}
    libro_iva          -> {facturacion_electronica, compras_fiscal}
    ventas_voz         -> {bot_telegram}
- validar_dependencias(features: frozenset[str]) -> list[str]: errores (features activas cuya dependencia no
  se cumple); lista vacía = ok.
- capacidades_completas(efectivas: frozenset[str]) -> frozenset[str]: NUCLEO ∪ efectivas (núcleo siempre on).
- es_feature_valida(nombre: str) -> bool contra NUCLEO ∪ OPCIONALES.

TDD: tests en tests/ que cubran: núcleo siempre incluido en capacidades_completas; notas_electronicas sin
facturacion -> error; libro_iva válido con compras_fiscal aunque no haya facturacion; ventas_voz sin
bot_telegram -> error; conjunto sin opcionales -> sin errores.

Reglas: módulo PURO (sin IO ni DB), type hints 3.10+, docstrings español, sin print.
Corre: .venv/Scripts/python.exe -m pytest tests/ -k "catalogo or capacidad" -q
```

**Qué reviso:** que sea puro (sin DB), que las dependencias sean OR donde aplica (libro_iva), y que
`capacidades_completas` meta el núcleo siempre.

> E2-E4 los entrego uno a uno al avanzar (caché TTL → /config → verificación).
