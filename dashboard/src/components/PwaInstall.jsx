/*
 * PwaInstall — banner "Instalar app" para la PWA.
 *
 * Escucha `beforeinstallprompt` (Chrome/Edge/Android): guarda el evento y ofrece instalar con un botón.
 * NO importa el módulo virtual del SW (es testeable en jsdom). Personaliza el nombre con el branding del
 * tenant. La descartada persiste en localStorage; tras instalar (`appinstalled`) no vuelve a aparecer.
 * iOS/Safari no dispara el evento: ahí el banner simplemente no se muestra (sin ruido).
 */
import { useEffect, useState } from 'react'
import { Download, X } from 'lucide-react'
import { useBranding } from '../lib/branding.jsx'

const DISMISS_KEY = 'ferrebot_pwa_install_dismissed'

function yaInstalada() {
  if (typeof window === 'undefined') return false
  return window.matchMedia?.('(display-mode: standalone)').matches || window.navigator.standalone === true
}

export default function PwaInstall() {
  const branding = useBranding()
  const [prompt, setPrompt] = useState(null)
  const [oculto, setOculto] = useState(() => {
    try { return localStorage.getItem(DISMISS_KEY) === '1' } catch { return false }
  })

  useEffect(() => {
    if (yaInstalada()) return
    const onPrompt = (e) => { e.preventDefault(); setPrompt(e) }
    const onInstalled = () => { setPrompt(null); try { localStorage.setItem(DISMISS_KEY, '1') } catch {} }
    window.addEventListener('beforeinstallprompt', onPrompt)
    window.addEventListener('appinstalled', onInstalled)
    return () => {
      window.removeEventListener('beforeinstallprompt', onPrompt)
      window.removeEventListener('appinstalled', onInstalled)
    }
  }, [])

  if (!prompt || oculto) return null

  const nombre = branding?.nombre_comercial || 'la app'

  const descartar = () => {
    setOculto(true)
    try { localStorage.setItem(DISMISS_KEY, '1') } catch {}
  }

  const instalar = async () => {
    try {
      prompt.prompt()
      await prompt.userChoice
    } finally {
      setPrompt(null)
    }
  }

  return (
    <div
      role="dialog"
      aria-label="Instalar aplicación"
      className="fixed z-50 left-4 right-4 bottom-4 md:left-auto md:right-6 md:bottom-6 md:w-80
                 bg-surface border border-border rounded-xl shadow-lg p-4 flex items-start gap-3"
    >
      <div className="shrink-0 size-10 rounded-lg bg-primary/10 grid place-items-center text-primary">
        <Download className="size-5" aria-hidden="true" />
      </div>
      <div className="min-w-0 flex-1">
        <p className="text-sm font-semibold text-foreground">Instalar {nombre}</p>
        <p className="text-xs text-muted-foreground mt-0.5">Acceso directo, pantalla completa y arranque sin conexión.</p>
        <div className="flex gap-2 mt-3">
          <button
            onClick={instalar}
            className="bg-primary text-primary-foreground text-xs font-medium px-3 py-1.5 rounded-md hover:bg-primary-hover"
          >
            Instalar
          </button>
          <button
            onClick={descartar}
            className="text-xs font-medium px-3 py-1.5 rounded-md text-muted-foreground hover:bg-surface-2"
          >
            Ahora no
          </button>
        </div>
      </div>
      <button onClick={descartar} aria-label="Cerrar" className="shrink-0 text-muted-foreground hover:text-foreground">
        <X className="size-4" aria-hidden="true" />
      </button>
    </div>
  )
}
