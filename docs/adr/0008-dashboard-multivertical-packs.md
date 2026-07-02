# ADR 0008 — Dashboard multi-vertical: POS deja de ser "núcleo", pasa a pack

> Estado: **Propuesto** (8 jun 2026). Decide cómo separar la ferretería (POS) del dashboard de un
> negocio de servicios, para que cada cliente vea **solo sus verticales**. Extiende el modelo de packs
> del ADR 0007. Fase A2 del `docs/roadmap-superficies-web.md`.

## Contexto

El dashboard ya oculta tabs por feature flags (`dashboard/src/lib/features.jsx`: `isRouteEnabled`). Pero
hay un sesgo histórico: las capacidades de **punto de venta** están declaradas como **NÚCLEO = siempre
activo** en `core/tenancy/catalogo.py`:

```python
NUCLEO = frozenset({"ventas", "inventario", "caja", "gastos", "clientes", "proveedores", "reportes"})
```

Y en el frontend, *"las rutas NO listadas son núcleo → siempre visibles"*. Consecuencia: una **clínica
dental** hoy ve en su menú **Ventas Rápidas, Caja, Inventario, Compras, Kárdex, Top productos** — todo
conceptos de retail que no tienen sentido para servicios. El modelo "núcleo = POS" se diseñó cuando
Punto Rojo era el único tenant; dejó de ser cierto con los agentes de servicios.

## Decisión

**Reclasificar las capacidades de retail de NÚCLEO a un pack opt-in**, para que el dashboard (y el API, y
el bot) expongan POS **solo** a los tenants que lo activen. El "núcleo" se encoge a lo de verdad
transversal.

### D1 — Nuevo pack `pos` (retail)

Mueve a un pack `pos` las capacidades específicas de punto de venta:
`ventas`, `inventario`, `caja`, `compras`, `proveedores`, `gastos`.

(Sus dependientes fiscales —`facturacion_electronica`, `compras_fiscal`, `libro_iva`, `fiados`,
`mayorista`, `ventas_voz`, `bot_telegram`— ya son opcionales; se les añade dependencia de `pos` donde
aplique: no tiene sentido facturar ventas sin el pack de ventas.)

### D2 — Núcleo mínimo (transversal de verdad)

Queda como núcleo solo lo que sirve a **cualquier** vertical:
- `clientes` — todo negocio tiene contactos (pacientes/clientes). *(Decisión abierta: si los campos
  fiscales del cliente molestan en servicios, ya son condicionales por flag — no bloquea.)*
- `reportes` — todos quieren ver resultados.
- La home **"Hoy"** y los ajustes del negocio.

> **Granularidad — decisión a confirmar:** ¿un solo pack `pos` (simple, recomendado para arrancar) o
> packs finos (`ventas`, `inventario`, `caja`…) para negocios que solo quieren parte? Recomiendo
> **un `pos` grueso ahora**; partirlo después si un cliente lo pide (el registro de packs lo permite sin
> reescribir).

### D3 — Gating consistente en las tres capas (igual que los demás packs)

- **Frontend** (`features.jsx` / `routes.jsx`): las rutas POS pasan a `RUTA_FEATURE` gateadas por `pos`
  (dejan de ser "núcleo siempre visible"). El menú "Operación/Gestión/Reportes/Fiscal" se arma con
  `routesByGroup(..., features)` que ya filtra.
- **Backend** (API): los routers POS van detrás de `require_feature("pos")` (hoy no lo están porque eran
  núcleo).
- **Registro de packs** (`tools/manifest/packs/registry.py`): `pos` se suma como Pack con su loader de
  datos si aplica (catálogos base de ventas/caja). El manifiesto de una ferretería activa `pos`.

### D4 — Migración: grandfather de Punto Rojo (no romper al tenant vivo)

Al sacar POS del núcleo, **Punto Rojo dejaría de ver sus tabs** si no se activa `pos`. Por eso la
migración del catálogo debe, en el mismo paso, **activar `pos` para Punto Rojo** (y para cualquier tenant
existente con datos de ventas) vía `empresa_features`/plan. Test de no-regresión: el dashboard de PR sigue
mostrando Ventas/Inventario/Caja después del cambio.

## Consecuencias

**A favor:** una clínica/spa ve un dashboard limpio (solo sus packs); el dashboard se vuelve de verdad
multi-vertical; coherente con el modelo runtime+packs+datos del ADR 0007; "separar la ferretería" deja de
ser un fork de código y pasa a ser un flag. Es exactamente el *seam* que el panel super-admin togglea.

**En contra / costo:** hay que añadir `pos` al catálogo + dependencias, gatear los routers POS en el API
(antes núcleo), ajustar `features.jsx`, y **migrar a los tenants existentes** (grandfather PR). Riesgo
principal: olvidar activar `pos` en PR → su dashboard se vacía; lo cubre el test de no-regresión.

**Lo que NO cambia:** el esquema de la BD (las tablas POS siguen existiendo en toda app DB, vacías si no se
usan — principio de `feature-flags.md`); el aislamiento; los packs de servicios (`pack_agenda`,
`canal_whatsapp`, `pack_faq`) siguen igual.

## Alternativas consideradas

- **Dos apps/repos separados (POS vs Agentes).** Rechazado: duplica infra, auth, tenancy y deployment;
  diverge el código; tira a la basura el modelo de packs. La separación correcta es lógica (un flag), no física.
- **Dejar POS como núcleo y solo ocultar tabs en el front.** Rechazado: el API seguiría exponiendo POS a
  todos (núcleo no gateado) y el menú seguiría asumiendo retail; es maquillaje, no separación real.
- **Packs finos desde ya** (`ventas`/`inventario`/…). Aplazado: más superficie sin demanda; un `pos`
  grueso ahora, partir después.

## Decisiones abiertas

1. Granularidad del pack POS (grueso `pos` vs finos) — recomiendo grueso.
2. ¿`clientes` y `reportes` realmente transversales, o también van al pack POS? (Una clínica sí quiere
   "clientes"=pacientes y "reportes"; me inclino a dejarlos en núcleo.)
3. Nombre del pack: `pos` vs `retail` vs `ventas_pos`.

## Enmienda (2026-07-01)

La decisión abierta #1 quedó resuelta por el **ADR 0021**: el pack `pos` se PARTIÓ en las features
finas `ventas` / `caja` / `inventario`, y `pos` sobrevive como **meta-pack que expande** (sin
migración de flags; los tenants con `pos` no cambian). La regla de familias de este ADR la refina el
ADR 0021 §D6: un tenant de servicios con finas EXPLÍCITAS (peluquería con `caja`+`ventas`) sí ve su
contabilidad junto a la agenda; el arrastre histórico del meta-pack sigue suprimido.
