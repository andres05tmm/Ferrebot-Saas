import { defineConfig, minimal2023Preset } from '@vite-pwa/assets-generator/config'

// Genera el set de íconos PWA (pwa-64/192/512, maskable-512, apple-touch-icon-180)
// desde public/pwa-source.svg. Correr: `npm run pwa:icons`.
export default defineConfig({
  preset: minimal2023Preset,
  images: ['public/pwa-source.svg'],
})
