/*
 * RecuperarPassword — "olvidé mi contraseña" (login real, ADR 0009). POST /auth/reset/solicitar {email}.
 *
 * SIN enumeración de usuarios: pase lo que pase (exista o no el email, falle o no la red) se muestra el
 * MISMO mensaje genérico. El backend genera el token y lo entrega (hoy por log; envío de email = TODO).
 */
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Loader2 } from 'lucide-react'
import { Card } from '@/components/ui/card.jsx'
import { api } from '@/lib/api'

export default function RecuperarPassword() {
  const [email, setEmail] = useState('')
  const [loading, setLoading] = useState(false)
  const [enviado, setEnviado] = useState(false)

  async function enviar(e) {
    e.preventDefault()
    if (!email.trim()) return
    setLoading(true)
    try {
      await api('/auth/reset/solicitar', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: email.trim() }),
      })
    } catch {
      /* sin enumeración: ni siquiera un error de red cambia el mensaje */
    } finally {
      setLoading(false)
      setEnviado(true)   // SIEMPRE el mismo resultado genérico
    }
  }

  return (
    <main className="min-h-[100dvh] bg-background flex flex-col items-center justify-center p-5 text-foreground">
      <Card className="w-full max-w-sm px-10 py-12 flex flex-col items-center gap-6">
        <h1 className="m-0 text-[20px] font-extrabold tracking-tight text-center">Recuperar contraseña</h1>

        {enviado ? (
          <p className="text-sm text-center text-muted-foreground">
            Si el email existe, te enviaremos un enlace para restablecer tu contraseña.{' '}
            <Link to="/login" className="text-primary underline">Volver al inicio</Link>
          </p>
        ) : (
          <form onSubmit={enviar} className="w-full flex flex-col gap-3" aria-label="Recuperar contraseña">
            <div className="flex flex-col gap-1">
              <label htmlFor="email" className="text-[11px] font-medium text-muted-foreground">Email</label>
              <input
                id="email" type="email" autoComplete="email" value={email}
                onChange={(e) => setEmail(e.target.value)} required
                className="w-full text-sm px-3 py-2 rounded-md border border-border bg-surface text-foreground"
              />
            </div>
            <button
              type="submit" disabled={loading}
              className="mt-1 inline-flex items-center justify-center gap-2 text-sm font-semibold px-3 py-2 rounded-md bg-primary text-primary-foreground hover:bg-primary-hover disabled:opacity-60"
            >
              {loading && <Loader2 className="size-3.5 animate-spin" />}
              {loading ? 'Enviando…' : 'Enviar enlace'}
            </button>
            <Link to="/login" className="text-[11px] text-muted-foreground hover:text-foreground text-center">
              Volver al inicio de sesión
            </Link>
          </form>
        )}
      </Card>
    </main>
  )
}
