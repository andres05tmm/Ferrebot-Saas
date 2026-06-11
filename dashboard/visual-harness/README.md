# Harness de verificación visual — tema "aurora"

Prueba estática (sin backend) de que el tema `aurora` aplica solo cuando el tenant lo declara y que el
tema base (Punto Rojo) no cambia. Reusa la MISMA capa de tokens (`src/index.css`) y las MISMAS clases
semánticas que los componentes reales (Sidebar, KpiCard, Card, Button, Login).

`proof.html` lee el estado del query string y lo aplica en `<html>` antes del paint:
- `?s=base-light | base-dark | aurora-light | aurora-dark` → `data-theme` + (para aurora) `data-tema="aurora"` y `--color-primary:#0EA5A4`.
- `?v=dash | login` → vista.

## Regenerar el CSS y las capturas

```bash
cd dashboard
# 1) CSS con Tailwind escaneando el harness (así nada se purga)
npx tailwindcss -c tailwind.config.js -i src/index.css -o visual-harness/proof.css \
  --content "./visual-harness/proof.html" --minify
# 2) 8 capturas (login + dash) × (base/aurora) × (light/dark) con Chrome headless
#    (ver el comando en el historial; --screenshot exige ruta ABSOLUTA en Windows)
```

`proof.css` y `shots/` son artefactos generados (gitignored). Solo `proof.html` y este README se versionan.
