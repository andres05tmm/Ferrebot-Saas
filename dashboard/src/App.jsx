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
import SetPassword from './pages/SetPassword.jsx'
import RecuperarPassword from './pages/RecuperarPassword.jsx'
import AdminPanel from './pages/admin/AdminPanel.jsx'
import PlatformRoute from './components/PlatformRoute.jsx'
import TabStub from './tabs/TabStub.jsx'
import TabHoy from './tabs/TabHoy.jsx'
import TabInicioAgente from './tabs/TabInicioAgente.jsx'
import TabVentasRapidas from './tabs/TabVentasRapidas.jsx'
import TabInventario from './tabs/TabInventario.jsx'
import TabCaja from './tabs/TabCaja.jsx'
import TabGastos from './tabs/TabGastos.jsx'
import TabClientes from './tabs/TabClientes.jsx'
import TabHistorial from './tabs/TabHistorial.jsx'
import TabHistorialServicios from './tabs/TabHistorialServicios.jsx'
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
import TabCartera from './tabs/TabCartera.jsx'
import TabCuentasPorPagar from './tabs/TabCuentasPorPagar.jsx'
import TabPedidos from './tabs/TabPedidos.jsx'
import TabCobros from './tabs/TabCobros.jsx'
import TabCotizaciones from './tabs/TabCotizaciones.jsx'
import TabPostventa from './tabs/TabPostventa.jsx'
import TabKardex from './tabs/TabKardex.jsx'
import TabReservas from './tabs/TabReservas.jsx'
import TabDevoluciones from './tabs/TabDevoluciones.jsx'
import TabLibros from './tabs/TabLibros.jsx'
import TabRetenciones from './tabs/TabRetenciones.jsx'
import TabConciliacion from './tabs/TabConciliacion.jsx'
import { ROUTES } from './routes.jsx'

// Tabs núcleo (E6) + reportes (S2) + facturación (S3) + compras (S4a) + proveedores (S4b) +
// compras fiscal (S6a) + libro IVA (S5); el resto, stub.
const TABS = {
  '/inicio': TabInicioAgente,
  '/hoy': TabHoy,
  '/ventas': TabVentasRapidas,
  '/inventario': TabInventario,
  '/caja': TabCaja,
  '/gastos': TabGastos,
  '/clientes': TabClientes,
  '/historial': HistorialPorFamilia,
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
  '/cartera': TabCartera,
  '/cuentas-por-pagar': TabCuentasPorPagar,
  '/pedidos': TabPedidos,
  '/cobros': TabCobros,
  '/cotizaciones': TabCotizaciones,
  '/postventa': TabPostventa,
  '/kardex': TabKardex,
  '/reservas': TabReservas,
  '/devoluciones': TabDevoluciones,
  '/libros': TabLibros,
  '/retenciones': TabRetenciones,
  '/conciliacion': TabConciliacion,
}
import { bootConfig } from './lib/config.js'
import { FeaturesProvider, useFeatures, resolveHomePath, esAtencionCliente, isRouteEnabled } from './lib/features.jsx'
import { BrandingProvider } from './lib/branding.jsx'

// /historial es transversal (ADR 0018): la familia POS ve el historial de ventas; la de servicios ve
// el suyo por vertical (pedidos/citas/reservas). El wrapper elige el componente por familia. Vive
// dentro de FeaturesProvider (ShellBoot), así que lee las features del shell ya cargado.
export function HistorialPorFamilia() {
  const features = useFeatures()
  return esAtencionCliente(features) ? <TabHistorialServicios /> : <TabHistorial />
}

// Redirige a la portada del tenant resuelta por sus features (Hoy POS / Inicio agente). Vive dentro de
// FeaturesProvider (ShellBoot), así que lee las features del shell ya cargado.
function HomeRedirect() {
  const features = useFeatures()
  return <Navigate to={resolveHomePath(features)} replace />
}

// Gate por feature del tenant: un deep-link a un tab deshabilitado (Sidebar/CommandPalette ya lo
// filtran, pero la URL sigue siendo alcanzable) redirige a la portada en vez de montar un panel
// cuyos fetches solo devolverían 403/404.
function RutaConFeature({ path, children }) {
  const features = useFeatures()
  if (!isRouteEnabled(path, features)) return <Navigate to={resolveHomePath(features)} replace />
  return children
}

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
          <Route path="/set-password" element={<SetPassword />} />
          <Route path="/recuperar" element={<RecuperarPassword />} />
          {/* Panel super-admin (ADR 0010): FUERA del shell de tenant; gateado por rol super_admin. */}
          <Route path="/admin" element={<PlatformRoute><AdminPanel /></PlatformRoute>} />
          {/* La portada y el catch-all resuelven la home por features (HomeRedirect): Hoy POS o Inicio agente. */}
          <Route
            element={
              <ProtectedRoute>
                <ShellBoot />
              </ProtectedRoute>
            }
          >
            <Route path="/" element={<HomeRedirect />} />
            {ROUTES.map(r => {
              const Comp = TABS[r.path] || TabStub
              return (
                <Route
                  key={r.path}
                  path={r.path}
                  element={<RutaConFeature path={r.path}><Comp /></RutaConFeature>}
                />
              )
            })}
            <Route path="*" element={<HomeRedirect />} />
          </Route>
        </Routes>
      </Router>
    </ErrorBoundary>
  )
}
