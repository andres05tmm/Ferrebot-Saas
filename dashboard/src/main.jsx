import React from 'react'
import ReactDOM from 'react-dom/client'
// Inter via @fontsource — sin <link> externo (evitar FOUT)
import '@fontsource/inter/400.css'
import '@fontsource/inter/500.css'
import '@fontsource/inter/600.css'
import '@fontsource/inter/700.css'
import App from './App.jsx'
import './index.css'

// El boot de /config (theming + features) corre ya autenticado, dentro de ProtectedRoute (App.jsx).
ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
