/*
 * SetPassword — establecer/restablecer la contraseña desde el enlace con token (login real, ADR 0009).
 *
 * El token viene en la URL (?token=...). POST /auth/set-password {token, password}. Sirve tanto para el
 * alta (identidad creada sin clave) como para el reset (el enlace de "olvidé mi contraseña" apunta aquí).
 * Política mínima de longitud en el cliente (el backend la revalida). Mensajes claros: enlace inválido/
 * expirado (400) vs. error genérico.
 */
import { useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { Loader2 } from 'lucide-react'
import { Card } from '@/components/ui/card.jsx'
import { api } from '@/lib/api.js'

const MIN_PASSWORD = 8

export default function SetPassword() {
  const [params] = useSearchParams()
  const token = params.get('token') || ''
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [loading, setLoading] = useState(false)
  const [ok, setOk] = useState(false)
  const [error, setError] = useState('')

  async function enviar(e) {
    e.preventDefault()
    setError('')
    if (password.length < MIN_PASSWORD) {
      setError(`La contraseña debe tener al menos ${MIN_PASSWORD} caracteres.`)
      return
    }
    if (password !== confirm) {
      setError('Las contraseñas no coinciden.')
      return
    }
    setLoading(true)
    try {
      const res = await api('/auth/set-password', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token, password }),
      })
      if (res.ok) setOk(true)
      else if (res.status === 400) setError('El enlace no es válido o ya expiró. Solicita uno nuevo.')
      else setError('No pudimos guardar la contraseña. Intenta de nuevo.')
    } catch {
      setError('Error de conexión. Intenta de nuevo.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <main className="min-h-[100dvh] bg-background flex flex-col items-center justify-center p-5 text-foreground">
      <Card className="w-full max-w-sm px-10 py-12 flex flex-col items-center gap-6">
        <h1 className="m-0 text-[20px] font-extrabold tracking-tight text-center">Establecer contraseña</h1>

        {!token ? (
          <p role="alert" className="text-xs text-destructive text-center">
            Enlace inválido: falta el token. Solicita uno nuevo desde{' '}
            <Link to="/recuperar" className="underline">¿Olvidaste tu contraseña?</Link>
          </p>
        ) : ok ? (
          <p className="text-sm text-center text-muted-foreground">
            ¡Listo! Tu contraseña quedó establecida.{' '}
            <Link to="/login" className="text-primary underline">Iniciar sesión</Link>
          </p>
        ) : (
          <form onSubmit={enviar} className="w-full flex flex-col gap-3" aria-label="Establecer contraseña">
            <div className="flex flex-col gap-1">
              <label htmlFor="password" className="text-[11px] font-medium text-muted-foreground">Nueva contraseña</label>
              <input
                id="password" type="password" autoComplete="new-password" value={password}
                onChange={(e) => setPassword(e.target.value)} required
                className="w-full text-sm px-3 py-2 rounded-md border border-border bg-surface text-foreground"
              />
            </div>
            <div className="flex flex-col gap-1">
              <label htmlFor="confirm" className="text-[11px] font-medium text-muted-foreground">Confirmar contraseña</label>
              <input
                id="confirm" type="password" autoComplete="new-password" value={confirm}
                onChange={(e) => setConfirm(e.target.value)} required
                className="w-full text-sm px-3 py-2 rounded-md border border-border bg-surface text-foreground"
              />
            </div>
            <button
              type="submit" disabled={loading}
              className="mt-1 inline-flex items-center justify-center gap-2 text-sm font-semibold px-3 py-2 rounded-md bg-primary text-primary-foreground hover:bg-primary-hover disabled:opacity-60"
            >
              {loading && <Loader2 className="size-3.5 animate-spin" />}
              {loading ? 'Guardando…' : 'Guardar contraseña'}
            </button>
          </form>
        )}

        {error && (
          <div role="alert" className="w-full text-center bg-destructive/10 border border-destructive/40 rounded-md px-3.5 py-2.5 text-xs text-destructive font-medium">
            {error}
          </div>
        )}
      </Card>
    </main>
  )
}
