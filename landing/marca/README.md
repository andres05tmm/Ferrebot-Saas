# Marca Melquiadez

Identidad de la plataforma (realismo mágico moderno): misterio + oficio, Caribe + tecnología.
Melquíades le trajo a Macondo los inventos del mundo; Melquiadez le trae al negocio de barrio
un empleado que no duerme.

## Archivos

| Archivo | Qué es | Uso |
|---|---|---|
| `sello.svg` | Monograma **M** como sello/firma alquímica: anillo abierto arriba a la derecha, M caligráfica (contraste de pluma) cuya última pata se enciende en cometa dorado y sale del sello hacia la chispa (el "ánima"). Cuadrado. | Favicon, avatar de WhatsApp, marca compacta |
| `wordmark.svg` | "Melquiadez" en **Fraunces 600** (opsz 72) convertido a paths (no depende de la fuente instalada). | Titulares de marca, footer |
| `lockup.svg` | Sello + wordmark, alineados ópticamente. | Nav, OG image, papelería |
| `tokens.css` | Tokens de marca: papel / tinta noche / oro viejo + temas claro/oscuro + acentos por vertical. | Lo importa la landing (y cualquier superficie pública) |
| `harness.html` | Harness visual: muestra las 3 variantes en todos los tamaños sobre papel y sobre tinta. | Iterar diseño con screenshots |

## Color

- Tinta de los trazos: `currentColor` — cada SVG trae `:root { color: #120c08 }` y cambia a
  `#f9f7f3` con `prefers-color-scheme: dark`, así el logo standalone (favicon) se adapta solo.
- Oro viejo literal `#c5953b` (oklch 70% .12 80) en cometa, chispa y punto del sello.

## Regenerar wordmark/lockup

El sello se dibuja a mano (editar `sello.svg`). El wordmark y el lockup se ensamblan con el
script de `.scratch-logo/` (no versionado): baja el TTF estático de Fraunces 600, corre
`gen-wordmark.mjs` (opentype.js → paths) y `build-svgs.mjs`. Para iterar:
servir esta carpeta (`python -m http.server`) y screenshotear `harness.html` con Chrome headless.
