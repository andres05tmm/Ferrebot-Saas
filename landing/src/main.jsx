import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter, Routes, Route } from 'react-router-dom'
// Fuentes autohospedadas (sin ida a Google Fonts en producción).
import '@fontsource/fraunces/latin-400.css'
import '@fontsource/fraunces/latin-600.css'
import '@fontsource/bricolage-grotesque/latin-400.css'
import '@fontsource/bricolage-grotesque/latin-600.css'
import '@fontsource/bricolage-grotesque/latin-800.css'
import './index.css'
import Landing from './pages/Landing.jsx'
import Login from './pages/Login.jsx'
import Demo from './pages/Demo.jsx'
import EnConstruccion from './pages/EnConstruccion.jsx'

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <BrowserRouter>
      <Routes>
        {/* Portada "en obra" mientras se termina la landing definitiva (/preview). */}
        <Route path="/" element={<EnConstruccion />} />
        <Route path="/preview" element={<Landing />} />
        <Route path="/login" element={<Login />} />
        <Route path="/demo" element={<Demo />} />
        <Route path="*" element={<EnConstruccion />} />
      </Routes>
    </BrowserRouter>
  </React.StrictMode>,
)
