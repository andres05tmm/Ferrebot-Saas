/*
 * Login — entrada por email + contraseña (login real, ADR 0009 A1.5).
 *
 * El login es ÚNICO y vive en la landing (melquiadez.com/login, plan §3): en PROD esta pantalla no
 * se muestra — si alguien navega directo a /login en un subdominio de tenant, rebota a la landing
 * (`landingLoginUrlForHost`, mismo contrato que ProtectedRoute y el intercept de 401). En DEV (sin
 * landing configurada) se queda el formulario local + el escape hatch del dev_token.
 *
 * POST /auth/login/password (useAuth.loginConPassword) → guarda el token → navega al shell, que trae
 * GET /config (branding + packs). Mensajes SIN enumeración: el mismo texto para email/clave (401) y
 * un aviso claro al bloqueo por intentos (429).
 */
import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Loader2 } from 'lucide-react'
import { Card } from '@/components/ui/card.jsx'
import { useAuth } from '@/hooks/useAuth.js'
import { TOKEN_KEY } from '@/lib/api.js'
import { landingLoginUrlForHost, handoffNav } from '@/lib/handoff.js'

export default function Login() {
  const navigate = useNavigate()
  const { loginConPassword } = useAuth()
  // En prod el login vive en la landing: si hay landing para este host, rebota allí (cubre la
  // navegación directa a /login). En dev (sin landing) → null → se queda el /login local.
  const landingUrl = landingLoginUrlForHost()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [devToken, setDevToken] = useState('')

  useEffect(() => {
    if (landingUrl) handoffNav.toLanding(landingUrl)
  }, [landingUrl])

  async function enviar(e) {
    e.preventDefault()
    if (!email.trim() || !password) return
    setLoading(true)
    setError('')
    try {
      const res = await loginConPassword(email.trim(), password)
      // El super-admin (identidad de plataforma, sin tenant) va a su panel; el resto, al shell del tenant.
      if (res.ok) navigate(res.usuario?.rol === 'super_admin' ? '/admin' : '/')
      else setError(res.error)
    } catch {
      setError('Error de conexión. Intenta de nuevo.')
    } finally {
      setLoading(false)
    }
  }

  function entrarConToken() {
    const t = devToken.trim()
    if (!t) return
    localStorage.setItem(TOKEN_KEY, t)
    navigate('/')
  }

  // En prod el efecto ya disparó el rebote a la landing: no pintamos el form local (evita el flash).
  if (landingUrl) return null

  return (
    <main className="min-h-[100dvh] bg-background flex flex-col items-center justify-center p-5 text-foreground" aria-labelledby="login-title">
      <Card className="w-full max-w-sm px-10 py-12 flex flex-col items-center gap-7">
        <div className="flex flex-col items-center gap-2.5 w-full">
          <div className="size-10 rounded-md bg-color-primary mb-1" aria-hidden="true" />
          <h1 id="login-title" className="m-0 text-[22px] font-extrabold text-foreground tracking-tight leading-tight text-center">
            Melquiadez
          </h1>
          <p className="m-0 mt-1 text-[11px] text-muted-foreground tracking-wider uppercase">
            Dashboard
          </p>
        </div>

        <div className="w-full h-px bg-border" />

        {/* Formulario primario: email + contraseña */}
        <form onSubmit={enviar} className="w-full flex flex-col gap-3" aria-label="Iniciar sesión">
          <div className="flex flex-col gap-1">
            <label htmlFor="email" className="text-[11px] font-medium text-muted-foreground">Email</label>
            <input
              id="email" type="email" autoComplete="email" value={email}
              onChange={(e) => setEmail(e.target.value)} required
              className="w-full text-sm px-3 py-2 rounded-md border border-border bg-surface text-foreground"
            />
          </div>
          <div className="flex flex-col gap-1">
            <label htmlFor="password" className="text-[11px] font-medium text-muted-foreground">Contraseña</label>
            <input
              id="password" type="password" autoComplete="current-password" value={password}
              onChange={(e) => setPassword(e.target.value)} required
              className="w-full text-sm px-3 py-2 rounded-md border border-border bg-surface text-foreground"
            />
          </div>
          <button
            type="submit" disabled={loading}
            className="mt-1 inline-flex items-center justify-center gap-2 text-sm font-semibold px-3 py-2 rounded-md bg-primary text-primary-foreground hover:bg-primary-hover disabled:opacity-60"
          >
            {loading && <Loader2 className="size-3.5 animate-spin" />}
            {loading ? 'Entrando…' : 'Entrar'}
          </button>
        </form>

        {error && (
          <div role="alert" className="w-full text-center bg-destructive/10 border border-destructive/40 rounded-md px-3.5 py-2.5 text-xs text-destructive font-medium">
            {error}
          </div>
        )}

        <Link to="/recuperar" className="text-[11px] text-muted-foreground hover:text-foreground underline-offset-2 hover:underline">
          ¿Olvidaste tu contraseña?
        </Link>

        {/* Escape hatch SOLO en desarrollo: pegar un JWT y entrar sin login. */}
        {import.meta.env.DEV && (
          <div className="w-full border-t border-border pt-4 flex flex-col gap-2">
            <label className="text-[10px] uppercase tracking-wider text-muted-foreground">Dev: entrar con token</label>
            <input
              value={devToken}
              onChange={(e) => setDevToken(e.target.value)}
              placeholder="Pega un JWT…"
              className="w-full text-xs px-2.5 py-1.5 rounded-md border border-border bg-surface text-foreground"
            />
            <button onClick={entrarConToken} className="text-xs px-3 py-1.5 rounded-md bg-primary text-primary-foreground hover:bg-primary-hover">
              Entrar con token
            </button>
          </div>
        )}
      </Card>
    </main>
  )
}
