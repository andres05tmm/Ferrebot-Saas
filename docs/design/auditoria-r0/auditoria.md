# Auditoría UI R0 — 4 superficies del restaurante (goal Ronda 2)

Capturas del estado actual (2026-07-24, `restaurante-demo` local re-provisionado, preset `brasa`
activo, datos de la carta demo + 2 mesas abiertas y 3 comandas). Contra `docs/design/DESIGN.md`
(multi-vertical) y el alcance §A.4 del goal. Prioridad: **P0** bug/violación de regla dura ·
**P1** falta del goal · **P2** pulido.

## Estado del theming (gate greppeable, corrido hoy)

- `dashboard/src/tabs/Tab{Pedidos,Mesas,Kds,MenuQr}.jsx`: **cero hex hardcodeados** ✅.
  Solo 6 usos de paleta Tailwind fija (`emerald`/`amber`) que deben migrar a tokens semánticos.
- `apps/api/menu_publico.py`: **`#C8200E` hardcodeado ×2** (header y subrayados del menú público
  de TODOS los tenants — el rojo es branding exclusivo de Punto Rojo, DESIGN.md §1) + paleta
  propia (`#faf7f2`, `#555`, `#222`) sin relación con el preset. El menú público NO lee branding.

## 1. Menú QR público (`menu-publico-390.png`) — la cara al cliente

| Prio | Hallazgo | Norte |
|---|---|---|
| **P0** | Muestra la sección **"Insumos"** (Arroz insumo (kg) $1, Carne insumo…): el BOM interno (ADR 0032 D9) se filtra al comensal porque `_leer_menu` lista todo producto activo | Excluir del menú público los productos que son insumo de recetas (o sin categoría de carta); test de no-fuga |
| **P0** | Header y acentos `#C8200E` (rojo Punto Rojo) hardcodeados; tipografía `system-ui`; ignora `branding` del tenant (preset `brasa` → ladrillo + Figtree) | Render con tokens del branding resuelto del tenant |
| P1 | Sin secciones sticky, sin botón "Pedir por WhatsApp" persistente (queda al fondo del scroll), sin fotos | §A.4.4: secciones sticky + CTA WhatsApp fijo + fotos si existen |
| P2 | Orden de secciones alfabético (Acompañamientos primero, platos fuertes después) | Orden editorial de carta (entradas → fuertes → acompañamientos → bebidas) |

## 2. Tab Menú QR admin (`menu-qr.png`)

| Prio | Hallazgo | Norte |
|---|---|---|
| **P0** | **Roto**: `GET /api/v1/menu-qr` → 422. Causa raíz: `menu_publico.py` tiene `from __future__ import annotations` y `Request` se importa DENTRO de `crear_router_menu_qr` → FastAPI no resuelve la anotación y exige `request` como query param. Fix: import a nivel de módulo | Hotfix de 1 línea + test del endpoint |
| P1 | La UI queda en "Cargando…" eterno ante el error (sin estado de error ni retry) | Estado de error con CTA |

## 3. KDS / Cocina (`kds.png`)

| Prio | Hallazgo | Norte (§A.4.3) |
|---|---|---|
| P1 | Tema claro: falta el **modo oscuro derivado de los tokens de `brasa`** (DESIGN.md §5 — no un dark genérico) | Variante oscura por tokens |
| P1 | Sin cronómetro por comanda ni umbral de alerta (solo hora absoluta "12:17 a.m.") | Tiempo transcurrido + cambio de tono >X min |
| P1 | Sin aviso sonoro+flash en comanda nueva | Audio + flash al insertar |
| P1 | Tipografía de ítems 14px: ilegible a distancia de cocina; botón "Iniciar" bajito | Escala grande (headline) + botón "listo" gigante ≥44px |
| P2 | `ring-amber-400` fijo (línea 63) | `var(--color-warn)` |

## 4. TabMesas (`mesas.png`)

| Prio | Hallazgo | Norte (§A.4.2) |
|---|---|---|
| P1 | Tarjetas chicas, mucha pantalla vacía: no es la grilla táctil tablet-first (targets ≥44px, estado por color tonal de TODA la tarjeta) | Grilla táctil con estado tonal (libre/ocupada/precuenta) |
| P1 | Ocupada solo se distingue por el total en rojo; "Libre" en verde texto plano | Tarjeta tonal + tiempo abierta + # ronda |
| P1 | Flujo abrir→ronda→precuenta→cobrar y modal de cobro con propina (5%/10%/otro/**default SIN propina**, Ley 1935) a verificar/pulir en R4 con capturas del flujo completo | ≤3 toques por paso |
| P2 | `text-emerald-600` fijo (línea 128) | `var(--color-ok)` |
| P2 | Sin atajos de teclado (el POS ya los tiene; extender a mesas — condicional R4) | Atajos + ayuda en UI |

## 5. TabPedidos kanban (`pedidos.png`)

| Prio | Hallazgo | Norte (§A.4.1) |
|---|---|---|
| P1 | Botón "Registrar venta" desborda la tarjeta (se recorta contra el borde en EN PREPARACIÓN / EN CAMINO) | Layout de acciones en columna o wrap |
| P1 | Sin tiempo transcurrido visible ni tono de alerta para pedidos viejos | Badge de minutos + umbral |
| P1 | Sin skeletons de carga ni estado vacío con CTA (columnas vacías muestran "—") | Skeleton + empty state |
| P2 | Badges de estado: títulos de columna en gris uniforme, no semánticos | `ok/warn/primario` según estado |
| P2 | `emerald-*` fijos (línea 55) | tokens semánticos |

## Priorización propuesta (aprobar en el checkpoint)

1. **Hotfix inmediato (no espera a R4):** endpoint `/menu-qr` roto (422) — 1 línea + test.
2. **R4.a Menú público** (es la cara al cliente y tiene los 2 P0 de fuga/branding).
3. **R4.b KDS** (dark por tokens + cronómetro + sonido — lo operativo de cocina).
4. **R4.c TabMesas** (grilla táctil + flujo cobro/propina).
5. **R4.d TabPedidos** (fix overflow + tiempos + estados).

Los 6 usos de Tailwind fijo se migran a tokens en la pasada de cada superficie (gate greppeable
del condicional R4 queda en cero).
