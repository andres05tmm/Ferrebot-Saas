---
name: Sistema de Diseño Multi-vertical — Melquiadez
principle: un solo sistema, una piel por vertical
source-of-truth: core/tenancy/branding_presets.py  # los tokens viven en código, no en este doc
typography:
  display-lg: {fontFamily: var(--font-display), fontSize: 36px, fontWeight: '700', lineHeight: 44px, letterSpacing: -0.02em}
  headline-md: {fontFamily: var(--font-display), fontSize: 24px, fontWeight: '600', lineHeight: 32px, letterSpacing: -0.01em}
  headline-sm: {fontFamily: var(--font-display), fontSize: 20px, fontWeight: '600', lineHeight: 28px}
  body-lg: {fontFamily: var(--font-ui), fontSize: 16px, fontWeight: '400', lineHeight: 24px}
  body-md: {fontFamily: var(--font-ui), fontSize: 14px, fontWeight: '400', lineHeight: 20px}
  label-md: {fontFamily: var(--font-ui), fontSize: 14px, fontWeight: '500', lineHeight: 20px}
  label-sm: {fontFamily: var(--font-ui), fontSize: 12px, fontWeight: '600', lineHeight: 16px}
spacing: {unit: 4px, container-margin: 24px, gutter: 16px, padding-card: 20px, stack-sm: 8px, stack-md: 16px}
grid: {columns: 12, sidebar: 280px, input-height: 40px, row-compact: 56px, row-standard: 72px}
---

# Sistema de Diseño Multi-vertical

> **Este documento reemplaza al sistema rojo-céntrico anterior.** El rojo `#C8200E` NO es el color
> de la plataforma: es el branding explícito del tenant Punto Rojo (override `color_primario`).
> Cada vertical tiene su propia piel (preset) y todos comparten los mismos fundamentos.

## 1. Principio: un sistema, N pieles

La plataforma sirve ferreterías, restaurantes, hoteles, barberías, clínicas y obra civil. Un
restaurante no debe verse como una ferretería recoloreada: cada vertical nace con la identidad de
su gremio, y cada tenant puede sobreescribir el primario con su propia marca (white-label).

- **Fuente única de tokens:** `core/tenancy/branding_presets.py` → resuelto por
  `resolver_branding` → entregado plano por `GET /api/v1/config` → aplicado por el dashboard como
  **variables CSS**. El front nunca interpreta el nombre del preset; solo aplica tokens.
- **Regla dura (gate de revisión):** PROHIBIDO hardcodear colores de marca en componentes, JSX o
  CSS de superficies. Todo color de marca entra por `var(--color-*)`. Un `#C8200E` (o cualquier
  hex de preset) escrito en un componente es un bug de theming, greppeable en CI.
- **Override de tenant:** `branding.color_primario` gana sobre el primario del preset (así Punto
  Rojo conserva su rojo). El resto de tokens siguen siendo del preset.

## 2. Tokens (contrato estable)

| Token (backend) | Variable CSS | Rol |
|---|---|---|
| `primario` / `primario_up` | `--color-primario` / `--color-primario-up` | Acento de marca; CTA, estados activos, foco. `_up` para hover/gradiente |
| `superficie` | `--color-superficie` | Fondo de la app (nivel 0) |
| `card` | `--color-card` | Tarjetas y contenedores (nivel 1) |
| `linea` | `--color-linea` | Bordes estructurales 1px |
| `tinta` / `tinta_suave` | `--color-tinta` / `--color-tinta-suave` | Texto principal / secundario |
| `ok` / `warn` / `bad` | `--color-ok` / `--color-warn` / `--color-bad` | Estados semánticos |
| `radius` | `--radius-brand` | Radio base del vertical (derivar: ×0.5 inputs, ×1 cards, pill 9999px) |
| `font_display` / `font_ui` | `--font-display` / `--font-ui` | Titulares / cuerpo (Google Fonts) |

## 3. Presets por vertical (espejo del código — no editar aquí, editar el .py)

| Preset | Vertical | Personalidad | Primario | Superficie | Fuentes | Radio |
|---|---|---|---|---|---|---|
| `melquiadez` | **Default de plataforma** | Papel cálido, tinta noche, oro viejo — sobrio y editorial | `#b8924f` | `#f7f4ee` | Fraunces / Bricolage Grotesque | 14px |
| `brasa` | Restaurantes, cafés | Ladrillo/brasa cálido y apetitoso, claro | `#d6452c` | `#faf5ef` | Figtree | 16px |
| `brisa` | Hoteles, hospedajes, pasadías | Mar profundo + arena, costero elegante (display serif) | `#0b3954` | `#f7f1e5` | Cormorant Garamond / Jost | 14px |
| `navaja` | Barberías | Oro sobre carbón — **nace OSCURO** | `#d99a3d` | `#171310` | Archivo | 14px |
| `aurora` | Clínicas, consultorios | Teal clínico, limpio y calmado | `#0e8784` | `#f6f9f9` | Nunito / Inter | 16px |
| `obra` | Construcción/obra civil (PIM) | Ámbar de maquinaria sobre grises industriales, condensada | `#e07a00` | `#f5f4f2` | Oswald / Inter | **8px** |
| `lienzo` | Genérico (sin preset propio) | Violeta neutro | `#6c5ce7` | `#f4f5f9` | Sora / Inter | 14px |
| *(override)* | Punto Rojo (ferretería) | Marca del tenant vía `color_primario` | `#C8200E` | del preset | del preset | del preset |

Personalidad por vertical al diseñar una pantalla nueva: en `brasa` el acento se usa con apetito
(fotos de comida mandan, el ladrillo enmarca); en `brisa` el lujo está en el aire y el serif de
display (mucho blanco-arena, poco acento); en `navaja` el oro es joya sobre oscuro (dosis mínima);
en `aurora` la calma clínica exige acento contenido y mucho aire; en `obra` la información es
densa y sin adornos (radio 8px, condensada, tablas duras).

## 4. Fundamentos compartidos (idénticos en todos los verticales)

- **Jerarquía por peso, no por tamaño**, en tarjetas de dashboard. Escala tipográfica del
  front-matter con las familias del preset.
- **Ritmo de 4px**; paddings internos en incrementos de 4 (8/12/16/20). Grid 12 columnas,
  sidebar fija 280px; móvil reflow a 1 columna con márgenes de 16px, tablas → tarjetas verticales.
- **Elevación por capas tonales**, no sombras pesadas: nivel 0 `--color-superficie`, nivel 1
  `--color-card` + borde 1px `--color-linea`, nivel 2 (modales/popovers) sombra difusa
  `0 10px 15px -3px rgba(0,0,0,0.1)`.
- **Estados semánticos**: badges pill (radio 9999px, `label-sm`) con fondo del color de estado al
  10% de opacidad y texto al 100%. Convención: `ok` confirmado/éxito, `warn` pendiente/aviso,
  `bad` error/cancelado, primario = activo/en curso, `tinta_suave` = neutro/cerrado.
- **Componentes**: botón primario (`--color-primario`, texto blanco o `tinta` según contraste del
  preset), secundario (`--color-card` + borde `--color-linea`); inputs 40px, label arriba
  (`body-md` medium), foco = borde primario + anillo exterior del primario al 10%; filas 56/72px
  con hover `--color-superficie`; touch targets ≥44px en superficies de tablet (POS, mesas, KDS).
- **Accesibilidad (gate)**: todo par texto/fondo cumple WCAG AA **en cada preset**; los presets se
  validan al crearse y los overrides de tenant que rompan contraste se corrigen ajustando
  `primario_up`. axe-core sin violaciones críticas/serias en pantallas nuevas.
- **Localización**: textos probados con expansión del español ("Miércoles"); moneda COP con
  separador de miles (`$19.000`).

## 5. Modo oscuro

`navaja` ES oscuro de fábrica. Para superficies que exigen oscuridad operativa (KDS en cocina),
la variante oscura se **deriva de los tokens del preset activo** (oscurecer superficie/card,
invertir tintas, mantener el primario del vertical) — nunca un "dark genérico" gris que borre la
identidad del gremio.

## 6. Crear un preset nuevo (checklist)

1. Nace de una propuesta HTML en `docs/design/propuestas/` con sus tokens en `:root`.
2. Se registra en `PRESETS` (`branding_presets.py`) con los 13 `TOKEN_KEYS`; el test paramétrico
   de presets lo valida (claves completas + contraste AA de pares críticos).
3. Se referencia en `plantillas-verticales.md` (columna Preset) y en la tabla §3 de este doc.
4. Ningún componente cambia: la piel entra sola por `/config`.
