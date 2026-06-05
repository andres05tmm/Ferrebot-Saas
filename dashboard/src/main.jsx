import React from 'react'
import ReactDOM from 'react-dom/client'
// Inter via @fontsource — sin <link> externo (evitar FOUT)
import '@fontsource/inter/400.css'
import '@fontsource/inter/500.css'
import '@fontsource/inter/600.css'
import '@fontsource/inter/700.css'
import App from './App.jsx'
import './index.css'
import { bootConfig } from './lib/config.js'
import { FeaturesProvider } from './lib/features.jsx'

// Boot: trae /config (theming + features) y monta el shell ya tematizado y gateado.
bootConfig().then((config) => {
  ReactDOM.createRoot(document.getElementById('root')).render(
    <React.StrictMode>
      <FeaturesProvider features={config.features}>
        <App />
      </FeaturesProvider>
    </React.StrictMode>,
  )
})
