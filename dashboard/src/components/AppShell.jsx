/*
 * AppShell — layout principal: sidebar (desktop) o bottom-nav (móvil) + outlet.
 * Andamiaje E3: tema light/dark (data-theme en <html>, persiste en localStorage), sidebar colapsable
 * y refresh manual. Auth (useAuth/VendorProvider) y realtime (useRealtime/SSE) se cablean en E4/E5.
 */
import { useCallback, useEffect, useState } from 'react'
import { Outlet } from 'react-router-dom'
import { useIsMobile } from './shared.jsx'
import { RealtimeProvider } from './RealtimeProvider.jsx'
import Sidebar from './Sidebar.jsx'
import MobileNav from './MobileNav.jsx'
import CommandPalette from './CommandPalette.jsx'
import HeaderBar from './HeaderBar.jsx'
import PwaInstall from './PwaInstall.jsx'

function loadColorScheme() {
  try {
    const v = localStorage.getItem('ferrebot_color_scheme')
    if (v === 'light' || v === 'dark') return v
  } catch {}
  if (typeof window !== 'undefined' && window.matchMedia) {
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
  }
  return 'light'
}

export default function AppShell() {
  const isMobile = useIsMobile()

  // ── Tema (light/dark via data-theme en <html>) ──────────────────────────────
  const [colorScheme, setColorScheme] = useState(loadColorScheme)
  useEffect(() => {
    document.documentElement.setAttribute('data-theme', colorScheme)
    try { localStorage.setItem('ferrebot_color_scheme', colorScheme) } catch {}
  }, [colorScheme])
  const toggleColorScheme = () => setColorScheme(s => s === 'dark' ? 'light' : 'dark')

  // ── Sidebar colapsado ───────────────────────────────────────────────────────
  const [collapsed, setCollapsed] = useState(() => {
    try { return localStorage.getItem('ferrebot_sidebar_collapsed') === '1' } catch { return false }
  })
  useEffect(() => {
    try { localStorage.setItem('ferrebot_sidebar_collapsed', collapsed ? '1' : '0') } catch {}
  }, [collapsed])

  // ── Refresh global (manual; el realtime SSE llega en E5) ────────────────────
  const [refreshKey, setRefreshKey] = useState(0)
  const [lastRefresh, setLastRefresh] = useState('')
  const doRefresh = useCallback(() => {
    setRefreshKey(k => k + 1)
    setLastRefresh(new Date().toLocaleTimeString('es-CO', { hour: '2-digit', minute: '2-digit', second: '2-digit' }))
  }, [])

  // ── Command Palette ─────────────────────────────────────────────────────────
  const [cmdOpen, setCmdOpen] = useState(false)

  // El stream SSE es ÚNICO y vive en RealtimeProvider; cada tab se suscribe con useRealtimeEvent.
  return (
    <RealtimeProvider>
    <div className="min-h-dvh text-foreground flex">
      {!isMobile && (
        <Sidebar
          collapsed={collapsed}
          setCollapsed={setCollapsed}
          onOpenCommand={() => setCmdOpen(true)}
          colorScheme={colorScheme}
          onToggleColorScheme={toggleColorScheme}
        />
      )}

      <div className="flex-1 min-w-0 flex flex-col">
        <HeaderBar
          isMobile={isMobile}
          onOpenCommand={() => setCmdOpen(true)}
          onRefresh={doRefresh}
          lastRefresh={lastRefresh}
          colorScheme={colorScheme}
          onToggleColorScheme={toggleColorScheme}
        />

        <main
          className="flex-1 px-4 md:px-6 py-5 md:py-6 mx-auto w-full"
          style={{
            maxWidth: 1400,
            paddingBottom: isMobile ? 'calc(80px + env(safe-area-inset-bottom))' : undefined,
          }}
        >
          <Outlet context={{ refreshKey }} />
        </main>
      </div>

      {isMobile && <MobileNav />}

      <CommandPalette open={cmdOpen} setOpen={setCmdOpen} onRefresh={doRefresh} />
      <PwaInstall />
    </div>
    </RealtimeProvider>
  )
}
