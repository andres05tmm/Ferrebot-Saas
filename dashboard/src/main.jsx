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
import App from './App.jsx'
import './index.css'

// El boot de /config (theming + features) corre ya autenticado, dentro de ProtectedRoute (App.jsx).
ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
