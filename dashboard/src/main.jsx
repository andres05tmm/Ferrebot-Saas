import React from 'react'
import ReactDOM from 'react-dom/client'
// Inter via @fontsource — sin <link> externo (evitar FOUT)
import '@fontsource/inter/400.css'
import '@fontsource/inter/500.css'
import '@fontsource/inter/600.css'
import '@fontsource/inter/700.css'
// Fuentes display de los temas con nombre (self-host, sin CDN). El navegador solo descarga el woff2
// del tema activo (los @font-face quedan declarados, las glifos cargan on-demand). El tema base no usa
// ninguna de estas: Nunito → aurora · Figtree → brasa · Cormorant Garamond → brisa · Sora → lienzo ·
// Archivo → navaja.
import '@fontsource/nunito/600.css'
import '@fontsource/nunito/700.css'
import '@fontsource/nunito/800.css'
import '@fontsource/figtree/600.css'
import '@fontsource/figtree/700.css'
import '@fontsource/figtree/800.css'
import '@fontsource/cormorant-garamond/600.css'
import '@fontsource/cormorant-garamond/700.css'
import '@fontsource/sora/600.css'
import '@fontsource/sora/700.css'
import '@fontsource/sora/800.css'
import '@fontsource/archivo/600.css'
import '@fontsource/archivo/700.css'
import '@fontsource/archivo/800.css'
import { QueryClientProvider } from '@tanstack/react-query'
import App from './App.jsx'
import './index.css'
import { consumeTokenFromHash } from './lib/handoff.js'
import { queryClient } from './lib/queryClient'
import { registerServiceWorker } from './lib/registerSw.js'

// Handoff de la landing (plan §3): ANTES de montar el router o disparar cualquier fetch, si la URL trae
// `#token=...` lo guardamos como sesión y borramos el fragmento del historial (que jamás quede en el
// historial ni en logs). Si ya había sesión, el token del fragmento la reemplaza.
consumeTokenFromHash()

// El boot de /config (theming + features) corre ya autenticado, dentro de ProtectedRoute (App.jsx).
// QueryClientProvider en la raíz (ADR 0029): habilita useQuery/useMutation en toda la app. Convive
// con useFetch (sin big-bang) y con el SSE (useRealtime), que no cambian.
ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  </React.StrictMode>,
)

// PWA: registra el service worker (no-op en dev). Fuera del árbol de React: no afecta el render.
registerServiceWorker()
