/*
 * Login — autenticación por Telegram Login Widget (adaptado del FerreBot original).
 *
 * Inyecta el script del widget con data-telegram-login = VITE_TELEGRAM_BOT_USERNAME. El callback
 * global onTelegramAuth(user) delega en useAuth().login (POST /auth/login vía api.js). Marca
 * white-label neutra. En desarrollo, escape hatch para pegar un JWT y entrar sin Telegram.
 */
import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Loader2 } from 'lucide-react'
import { Card } from '@/components/ui/card.jsx'
import { useAuth } from '@/hooks/useAuth.js'
import { TOKEN_KEY } from '@/lib/api.js'

const BOT_USERNAME = import.meta.env.VITE_TELEGRAM_BOT_USERNAME

export default function Login() {
  const navigate = useNavigate()
  const { login } = useAuth()
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [devToken, setDevToken] = useState('')
  const widgetRef = useRef(null)

  useEffect(() => {
    window.onTelegramAuth = async (user) => {
      setLoading(true)
      setError('')
      try {
        const res = await login(user)
        if (res.ok) {
          navigate('/')
        } else {
          setError(res.error)
        }
      } catch {
        setError('Error de conexión. Intenta de nuevo.')
      } finally {
        setLoading(false)
      }
    }

    if (BOT_USERNAME && widgetRef.current) {
      const script = document.createElement('script')
      script.src = 'https://telegram.org/js/telegram-widget.js?22'
      script.setAttribute('data-telegram-login', BOT_USERNAME)
      script.setAttribute('data-size', 'large')
      script.setAttribute('data-onauth', 'onTelegramAuth(user)')
      script.setAttribute('data-request-access', 'write')
      script.setAttribute('data-userpic', 'false')
      script.async = true
      widgetRef.current.innerHTML = ''
      widgetRef.current.appendChild(script)
    }

    // Telegram inyecta un <iframe> sin title — el observador lo etiqueta para a11y.
    const obs = widgetRef.current && new MutationObserver(() => {
      const iframe = widgetRef.current?.querySelector('iframe')
      if (iframe && !iframe.title) iframe.title = 'Iniciar sesión con Telegram'
    })
    if (obs && widgetRef.current) obs.observe(widgetRef.current, { childList: true, subtree: true })

    return () => {
      delete window.onTelegramAuth
      obs?.disconnect()
    }
  }, [navigate, login])

  function entrarConToken() {
    const t = devToken.trim()
    if (!t) return
    localStorage.setItem(TOKEN_KEY, t)
    navigate('/')
  }

  return (
    <main className="min-h-[100dvh] bg-background flex flex-col items-center justify-center p-5 text-foreground" aria-labelledby="login-title">
      <Card className="w-full max-w-sm px-10 py-12 flex flex-col items-center gap-7">
        {/* Branding white-label neutro */}
        <div className="flex flex-col items-center gap-2.5 w-full">
          <div className="size-10 rounded-md bg-color-primary mb-1" aria-hidden="true" />
          <h1 id="login-title" className="m-0 text-[22px] font-extrabold text-foreground tracking-tight leading-tight text-center">
            FerreBot
          </h1>
          <p className="m-0 mt-1 text-[11px] text-muted-foreground tracking-wider uppercase">
            Dashboard de ventas
          </p>
        </div>

        <div className="w-full h-px bg-border" />

        {/* Widget container — el script se inyecta aquí */}
        <div ref={widgetRef} className="flex justify-center min-h-[48px] w-full" />

        {loading && (
          <div className="inline-flex items-center gap-2 text-muted-foreground text-xs tracking-wide">
            <Loader2 className="size-3.5 animate-spin text-primary" />
            <span>Autenticando…</span>
          </div>
        )}

        {error && (
          <div className="w-full text-center bg-destructive/10 border border-destructive/40 rounded-md px-3.5 py-2.5 text-xs text-destructive font-medium">
            {error}
          </div>
        )}

        <p className="m-0 text-[11px] text-muted-foreground text-center leading-relaxed">
          Inicia sesión con tu cuenta de Telegram para acceder al dashboard
        </p>

        {/* Escape hatch SOLO en desarrollo: pegar un JWT y entrar sin Telegram. */}
        {import.meta.env.DEV && (
          <div className="w-full border-t border-border pt-4 flex flex-col gap-2">
            <label className="text-[10px] uppercase tracking-wider text-muted-foreground">Dev: entrar con token</label>
            <input
              value={devToken}
              onChange={(e) => setDevToken(e.target.value)}
              placeholder="Pega un JWT…"
              className="w-full text-xs px-2.5 py-1.5 rounded-md border border-border bg-surface text-foreground"
            />
            <button
              onClick={entrarConToken}
              className="text-xs px-3 py-1.5 rounded-md bg-primary text-primary-foreground hover:bg-primary-hover"
            >
              Entrar
            </button>
          </div>
        )}
      </Card>
    </main>
  )
}
