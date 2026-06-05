/*
 * App.jsx — entry point del dashboard (andamiaje E3).
 * Shell con sidebar + react-router. Cada tab vive en su ruta, hoy como stub "Próximamente".
 * Auth (Login/ProtectedRoute) y realtime se cablean en E4/E5.
 */
import React from 'react'
import { BrowserRouter as Router, Routes, Route, Navigate } from 'react-router-dom'
import { Toaster } from './components/ui/sonner.jsx'
import AppShell from './components/AppShell.jsx'
import TabStub from './tabs/TabStub.jsx'
import { ROUTES } from './routes.jsx'

// ── Error Boundary ───────────────────────────────────────────────────────────
class ErrorBoundary extends React.Component {
  constructor(props) { super(props); this.state = { hasError: false, error: null } }
  static getDerivedStateFromError(error) { return { hasError: true, error } }
  render() {
    if (this.state.hasError) {
      const msg = this.state.error?.message || String(this.state.error)
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
    return this.props.children
  }
}

export default function App() {
  return (
    <ErrorBoundary>
      <Toaster position="bottom-right" />
      <Router>
        <Routes>
          <Route element={<AppShell />}>
            <Route path="/" element={<Navigate to="/hoy" replace />} />
            {ROUTES.map(r => (
              <Route key={r.path} path={r.path} element={<TabStub />} />
            ))}
            <Route path="*" element={<Navigate to="/hoy" replace />} />
          </Route>
        </Routes>
      </Router>
    </ErrorBoundary>
  )
}
