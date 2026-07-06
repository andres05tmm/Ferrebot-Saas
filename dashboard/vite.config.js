import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import { VitePWA } from 'vite-plugin-pwa'
import path from 'node:path'

export default defineConfig({
  plugins: [
    react(),
    // PWA instalable + caché de lectura. El manifest NO se genera aquí (manifest: false): lo sirve
    // el backend por-tenant en GET /api/v1/manifest.webmanifest (nombre/theme_color de la empresa).
    // El SW solo cachea el código (app-shell), igual para todos los tenants. Registro manual en main.jsx.
    VitePWA({
      registerType: 'autoUpdate',
      injectRegister: null,
      manifest: false,
      includeAssets: ['favicon.ico', 'apple-touch-icon-180x180.png', 'sello.svg'],
      workbox: {
        globPatterns: ['**/*.{js,css,html}'],
        maximumFileSizeToCacheInBytes: 4 * 1024 * 1024,
        // El SPA lo sirve la API en toda ruta no-/api/; offline cae al index precacheado.
        navigateFallback: '/index.html',
        // NUNCA interceptar la API con el fallback de navegación (regla multi-tenant: los datos van a red).
        navigateFallbackDenylist: [/^\/api\//],
        runtimeCaching: [
          {
            // GETs de lectura de la API: red-primero (datos frescos), cae a caché solo si no hay red.
            // La caché es por-origen; cada tenant vive en su subdominio, así que no se cruzan empresas.
            urlPattern: ({ url, request }) => url.pathname.startsWith('/api/') && request.method === 'GET',
            handler: 'NetworkFirst',
            options: {
              cacheName: 'api-read',
              networkTimeoutSeconds: 5,
              expiration: { maxEntries: 200, maxAgeSeconds: 60 * 60 * 24 },
              cacheableResponse: { statuses: [0, 200] },
            },
          },
          {
            urlPattern: ({ request }) => request.destination === 'font',
            handler: 'CacheFirst',
            options: { cacheName: 'fonts', expiration: { maxEntries: 30, maxAgeSeconds: 60 * 60 * 24 * 365 } },
          },
          {
            urlPattern: ({ request }) => request.destination === 'image',
            handler: 'StaleWhileRevalidate',
            options: { cacheName: 'images', expiration: { maxEntries: 60, maxAgeSeconds: 60 * 60 * 24 * 30 } },
          },
        ],
      },
      devOptions: { enabled: false },
    }),
  ],
  resolve: {
    alias: {
      '@': path.resolve(import.meta.dirname, './src'),
    },
  },
  server: {
    port: 5173,
    proxy: {
      // Mantiene el prefijo /api/v1 (SIN rewrite): el backend SaaS expone los endpoints ahí.
      '/api/v1': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: './vitest.setup.js',
    css: false,
  },
})
