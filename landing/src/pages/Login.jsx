import { lazy, Suspense, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { Loader2 } from 'lucide-react'
import Sello from '@/components/Sello.jsx'
import { Input } from '@/components/ui/input.jsx'
import { Label } from '@/components/ui/label.jsx'
import { APP_URL, iniciarSesion, urlDashboardConToken } from '@/lib/auth.js'

const AuroraOro = lazy(() => import('@/components/AuroraOro.jsx'))

/*
 * /login — base `split-login-card` (21st.dev, ruixenui) rehecha para Melquiadez:
 * form a la izquierda, panel de marca con shader + sello a la derecha.
 * POST /api/v1/auth/login/password → redirige al dashboard con #token=... (fragmento:
 * no viaja al servidor). 401 → mensaje genérico; 429 → bloqueo temporal.
 */
export default function Login() {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [cargando, setCargando] = useState(false)
  const [error, setError] = useState('')

  // La página de login es de marca: acento neutro (oro), sin vertical activo.
  useEffect(() => {
    document.documentElement.dataset.vertical = 'neutro'
  }, [])

  async function enviar(e) {
    e.preventDefault()
    if (!email.trim() || !password || cargando) return
    setCargando(true)
    setError('')
    const res = await iniciarSesion(email.trim(), password)
    if (res.ok) {
      window.location.assign(urlDashboardConToken(res.token))
      return // seguimos "cargando" mientras el navegador navega
    }
    setError(res.error)
    setCargando(false)
  }

  // Campos con afordancia de marca: borde neutro visible (≥3:1 contra el card) + fondo papel-2
  // (distinto del panel), foco oro (borde --oro-oscuro + halo --oro-suave, un solo color, sin azul).
  const clasesInput =
    'h-11 rounded-xl border-texto-3 bg-fondo-2 px-4 text-[15px] placeholder:text-texto-3 ' +
    'transition-[box-shadow,border-color] focus-visible:border-oro-oscuro focus-visible:ring-[var(--oro-suave)]'

  return (
    <main className="grid min-h-[100dvh] place-items-center bg-fondo p-5">
      <div className="flex w-full max-w-4xl flex-col-reverse overflow-hidden rounded-3xl border border-linea bg-panel shadow-marca md:flex-row">
        {/* form */}
        <div className="flex flex-col justify-center p-8 md:w-1/2 md:p-12">
          <Link to="/" className="mb-8 inline-flex items-center gap-2 text-texto-2 transition-colors hover:text-texto">
            <span className="text-sm">← melquiadez.com</span>
          </Link>
          <h1 className="font-display text-3xl font-semibold tracking-tight">Bienvenido de vuelta</h1>
          <p className="mt-1.5 text-sm text-texto-2">Tu negocio te espera adentro.</p>

          <form onSubmit={enviar} className="mt-8 flex flex-col gap-4" aria-label="Iniciar sesión">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="email">Email</Label>
              <Input
                id="email"
                type="email"
                autoComplete="email"
                placeholder="tu@negocio.com"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
                className={clasesInput}
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="password">Contraseña</Label>
              <Input
                id="password"
                type="password"
                autoComplete="current-password"
                placeholder="••••••••"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                className={clasesInput}
              />
            </div>
            <button
              type="submit"
              disabled={cargando}
              className="mt-2 inline-flex items-center justify-center gap-2 rounded-xl bg-tinta px-4 py-3 text-[15px] font-semibold text-papel shadow-sm transition-all duration-300 ease-marca hover:-translate-y-px hover:bg-[var(--tinta-2)] hover:shadow-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-oro-oscuro focus-visible:ring-offset-2 focus-visible:ring-offset-panel disabled:opacity-60 disabled:hover:translate-y-0 disabled:hover:shadow-sm"
            >
              {cargando && <Loader2 className="size-4 animate-spin" />}
              {cargando ? 'Entrando…' : 'Entrar'}
            </button>
          </form>

          {error && (
            <div
              role="alert"
              className="mt-4 rounded-xl border border-destructive/40 bg-destructive/10 px-4 py-2.5 text-center text-[13px] font-medium text-destructive"
            >
              {error}
            </div>
          )}

          <a
            href={`${APP_URL}/recuperar`}
            className="mt-6 text-center text-[13px] text-texto-2 underline-offset-2 transition-colors hover:text-texto hover:underline"
          >
            ¿Olvidaste tu clave?
          </a>
        </div>

        {/* panel de marca */}
        <div className="relative min-h-[180px] overflow-hidden bg-tinta md:min-h-[540px] md:w-1/2">
          <Suspense fallback={null}>
            <AuroraOro tema="oscuro" intensidad={0.65} />
          </Suspense>
          <div className="absolute inset-0 bg-gradient-to-t from-tinta/60 via-transparent to-transparent" />
          <div className="relative z-10 flex h-full flex-col items-center justify-center gap-4 p-8 text-papel">
            <Sello className="size-24 !text-papel md:size-32" />
            <p className="font-display text-2xl font-semibold tracking-tight">Melquiadez</p>
            <p className="max-w-[26ch] text-center text-sm text-papel/70">
              Tu empleado siguió trabajando mientras no estabas.
            </p>
          </div>
        </div>
      </div>
    </main>
  )
}
