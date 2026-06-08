/*
 * App.jsx — entry point del dashboard.
 * /login público; el shell + tabs detrás de ProtectedRoute. Ya autenticado, ShellBoot trae /config
 * (theming + features) antes de montar el shell. Tabs como stubs (E3); tabs reales en E6.
 */
import React, { useEffect, useState } from 'react'
import { BrowserRouter as Router, Routes, Route, Navigate } from 'react-router-dom'
import { Loader2 } from 'lucide-react'
import { Toaster } from './components/ui/sonner.jsx'
import AppShell from './components/AppShell.jsx'
import ProtectedRoute from './components/ProtectedRoute.jsx'
import Login from './pages/Login.jsx'
import TabStub from './tabs/TabStub.jsx'
import TabHoy from './tabs/TabHoy.jsx'
import TabVentasRapidas from './tabs/TabVentasRapidas.jsx'
import TabInventario from './tabs/TabInventario.jsx'
import TabCaja from './tabs/TabCaja.jsx'
import TabGastos from './tabs/TabGastos.jsx'
import TabClientes from './tabs/TabClientes.jsx'
import TabHistorial from './tabs/TabHistorial.jsx'
import TabResultados from './tabs/TabResultados.jsx'
import TabTopProductos from './tabs/TabTopProductos.jsx'
import TabFacturacion from './tabs/TabFacturacion.jsx'
import TabCompras from './tabs/TabCompras.jsx'
import TabProveedores from './tabs/TabProveedores.jsx'
import TabComprasFiscal from './tabs/TabComprasFiscal.jsx'
import TabLibroIVA from './tabs/TabLibroIVA.jsx'
import TabAgenda from './tabs/TabAgenda.jsx'
import TabConversaciones from './tabs/TabConversaciones.jsx'
import TabConocimiento from './tabs/TabConocimiento.jsx'
import { ROUTES } from './routes.jsx'

// Tabs núcleo (E6) + reportes (S2) + facturación (S3) + compras (S4a) + proveedores (S4b) +
// compras fiscal (S6a) + libro IVA (S5); el resto, stub.
const TABS = {
  '/hoy': TabHoy,
  '/ventas': TabVentasRapidas,
  '/inventario': TabInventario,
  '/caja': TabCaja,
  '/gastos': TabGastos,
  '/clientes': TabClientes,
  '/historial': TabHistorial,
  '/resultados': TabResultados,
  '/top-productos': TabTopProductos,
  '/facturacion': TabFacturacion,
  '/compras': TabCompras,
  '/proveedores': TabProveedores,
  '/compras-fiscal': TabComprasFiscal,
  '/libro-iva': TabLibroIVA,
  '/agenda': TabAgenda,
  '/conversaciones': TabConversaciones,
  '/conocimiento': TabConocimiento,
}
import { bootConfig } from './lib/config.js'
import { FeaturesProvider } from './lib/features.jsx'
import { BrandingProvider } from './lib/branding.jsx'

// ── Error Boundary ───────────────────────────────────────────────────────────
class ErrorBoundary extends React.Component {
  constructor(props) { super(props); this.state = { hasError: false, error: null } }
  static getDerivedStateFromError(error) { return { hasError: true, error } }
  render() {
    if (this.state.hasError) {
      return <BootError msg={this.state.error?.message || String(this.state.error)} />
    }
    return this.props.children
  }
}

function BootError({ msg }) {
  return (
    <div className="min-h-dvh grid place-items-center bg-background p-8">
      <div className="max-w-lg bg-surface border border-border rounded-lg p-8 shadow-md">
        <h2 className="text-lg font-semibold text-primary mb-2">Error al cargar el dashboard</h2>
        <pre className="bg-surface-2 rounded-md p-3 text-xs text-secondary-foreground overflow-x-auto whitespace-pre-wrap mb-4">
          {msg}
        </pre>
        <button
          onClick={() => window.location.reload()}
          className="bg-primary text-primary-foreground px-4 py-2 rounded-md text-sm hover:bg-primary-hover"
        >
          Recargar
        </button>
      </div>
    </div>
  )
}

// ── ShellBoot — ya autenticado: trae /config, tematiza y monta el shell ──────
function ShellBoot() {
  const [estado, setEstado] = useState({ cargando: true, error: null, features: [], branding: {} })

  useEffect(() => {
    let cancelado = false
    bootConfig()
      .then((cfg) => { if (!cancelado) setEstado({ cargando: false, error: null, features: cfg.features, branding: cfg.branding }) })
      .catch((e) => { if (!cancelado) setEstado({ cargando: false, error: e.message, features: [], branding: {} }) })
    return () => { cancelado = true }
  }, [])

  if (estado.cargando) {
    return (
      <div className="min-h-dvh grid place-items-center" role="status" aria-label="Cargando dashboard">
        <Loader2 className="size-6 animate-spin text-muted-foreground" aria-hidden="true" />
      </div>
    )
  }
  if (estado.error) return <BootError msg={estado.error} />

  return (
    <FeaturesProvider features={estado.features}>
      <BrandingProvider branding={estado.branding}>
        <AppShell />
      </BrandingProvider>
    </FeaturesProvider>
  )
}

export default function App() {
  return (
    <ErrorBoundary>
      <Toaster position="bottom-right" />
      <Router>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route
            element={
              <ProtectedRoute>
                <ShellBoot />
              </ProtectedRoute>
            }
          >
            <Route path="/" element={<Navigate to="/hoy" replace />} />
            {ROUTES.map(r => {
              const Comp = TABS[r.path] || TabStub
              return <Route key={r.path} path={r.path} element={<Comp />} />
            })}
            <Route path="*" element={<Navigate to="/hoy" replace />} />
          </Route>
        </Routes>
      </Router>
    </ErrorBoundary>
  )
}
