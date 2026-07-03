/*
 * CrearTenantForm — arma un manifiesto desde un formulario y lo ENCOLA (POST /admin/tenants), luego
 * hace polling del estado del job (GET /admin/jobs/{job_id}): encolado→corriendo→ok/error.
 *
 * SEGURIDAD: nada se persiste en localStorage. Si en el futuro se agregan campos de SECRETO (p. ej.
 * MATIAS de una ferretería) van solo en el estado transitorio del form, por HTTPS, y se limpian tras
 * enviar — JAMÁS a localStorage. v1 no pide secretos: los datos de pack ricos van por un área JSON.
 */
import { useEffect, useState } from 'react'
import { Loader2 } from 'lucide-react'
import { Card } from '@/components/ui/card.jsx'
import { Input } from '@/components/ui/input.jsx'
import { Button } from '@/components/ui/button.jsx'
import { api, apiJson } from '@/lib/api'
import { FEATURES_OPCIONALES } from './features.js'

const TERMINALES = new Set(['ok', 'error'])

// El `detail` de un 422 puede venir como string (HTTPException de validar()) o como array de
// {loc, msg, type} (validación de Pydantic del esquema). Lo vuelve legible para el operador.
function detalleLegible(detail) {
  if (Array.isArray(detail)) return detail.map((e) => e?.msg || JSON.stringify(e)).join('; ')
  if (typeof detail === 'string') return detail
  return ''
}

function armarManifiesto({ slug, nombre, nit, email, planNombre, features, waPhone, waNumero, packsJson }) {
  const m = { version: 1, identidad: { slug: slug.trim(), nombre: nombre.trim(), nit: nit.trim() } }
  if (email.trim()) m.admin = { email: email.trim() }
  if (features.length) m.plan = { nombre: planNombre.trim() || 'Custom', features }
  if (waPhone.trim()) {
    m.canal = { whatsapp: { phone_number_id: waPhone.trim(), ...(waNumero.trim() ? { numero: waNumero.trim() } : {}) } }
  }
  if (packsJson.trim()) m.packs = JSON.parse(packsJson)   // lanza SyntaxError → se captura como error de form
  return m
}

export default function CrearTenantForm({ intervaloMs = 1500, onProvisionado }) {
  const [slug, setSlug] = useState('')
  const [nombre, setNombre] = useState('')
  const [nit, setNit] = useState('')
  const [email, setEmail] = useState('')
  const [planNombre, setPlanNombre] = useState('')
  const [features, setFeatures] = useState([])
  const [waPhone, setWaPhone] = useState('')
  const [waNumero, setWaNumero] = useState('')
  const [packsJson, setPacksJson] = useState('')
  const [error, setError] = useState('')
  const [enviando, setEnviando] = useState(false)
  const [jobId, setJobId] = useState('')
  const [job, setJob] = useState(null)

  function toggleFeature(f) {
    setFeatures((prev) => (prev.includes(f) ? prev.filter((x) => x !== f) : [...prev, f]))
  }

  async function enviar(e) {
    e.preventDefault()
    setError('')
    setJob(null)
    let manifiesto
    try {
      manifiesto = armarManifiesto({ slug, nombre, nit, email, planNombre, features, waPhone, waNumero, packsJson })
    } catch {
      setError('Los datos de packs no son JSON válido.')
      return
    }
    setEnviando(true)
    try {
      const res = await api('/admin/tenants', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(manifiesto),
      })
      if (res.status === 202) {
        const { job_id } = await res.json()
        setJobId(job_id)
      } else {
        const cuerpo = await res.json().catch(() => ({}))
        const motivo = detalleLegible(cuerpo.detail)
        setError(motivo ? `Manifiesto inválido: ${motivo}` : 'No se pudo encolar el alta.')
      }
    } catch {
      setError('Error de conexión. Intenta de nuevo.')
    } finally {
      setEnviando(false)
    }
  }

  // Polling del estado del job: primer tick inmediato y luego cada `intervaloMs` hasta un estado terminal.
  useEffect(() => {
    if (!jobId) return undefined
    let cancelado = false
    let timer
    const tick = async () => {
      try {
        const data = await apiJson(`/admin/jobs/${jobId}`)
        if (cancelado) return
        setJob(data)
        if (TERMINALES.has(data.estado)) {
          if (data.estado === 'ok') onProvisionado?.()
          return
        }
      } catch {
        if (cancelado) return
      }
      timer = setTimeout(tick, intervaloMs)
    }
    tick()
    return () => { cancelado = true; clearTimeout(timer) }
  }, [jobId, intervaloMs, onProvisionado])

  return (
    <Card className="p-4 flex flex-col gap-3">
      <h2 className="text-sm font-semibold text-foreground">Crear empresa</h2>
      <form onSubmit={enviar} className="flex flex-col gap-3" aria-label="Crear empresa">
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
          <label className="flex flex-col gap-1 text-[11px] text-muted-foreground">
            Slug
            <Input value={slug} onChange={(e) => setSlug(e.target.value)} required aria-label="Slug" placeholder="clinica-demo" />
          </label>
          <label className="flex flex-col gap-1 text-[11px] text-muted-foreground">
            Nombre
            <Input value={nombre} onChange={(e) => setNombre(e.target.value)} required aria-label="Nombre" />
          </label>
          <label className="flex flex-col gap-1 text-[11px] text-muted-foreground">
            NIT
            <Input value={nit} onChange={(e) => setNit(e.target.value)} required aria-label="NIT" />
          </label>
        </div>

        <label className="flex flex-col gap-1 text-[11px] text-muted-foreground">
          Email del admin
          <Input type="email" value={email} onChange={(e) => setEmail(e.target.value)} aria-label="Email del admin" placeholder="dueño@empresa.co" />
        </label>

        <fieldset className="flex flex-col gap-1">
          <legend className="text-[11px] text-muted-foreground mb-1">Plan / packs (features a activar)</legend>
          <Input value={planNombre} onChange={(e) => setPlanNombre(e.target.value)} aria-label="Nombre del plan" placeholder="Nombre del plan (opcional)" className="mb-2" />
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-1.5">
            {FEATURES_OPCIONALES.map(([f, label]) => (
              <label key={f} className="flex items-center gap-1.5 text-[12px] text-foreground">
                <input type="checkbox" checked={features.includes(f)} onChange={() => toggleFeature(f)} aria-label={label} />
                {label}
              </label>
            ))}
          </div>
        </fieldset>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
          <label className="flex flex-col gap-1 text-[11px] text-muted-foreground">
            WhatsApp phone_number_id
            <Input value={waPhone} onChange={(e) => setWaPhone(e.target.value)} aria-label="WhatsApp phone_number_id" />
          </label>
          <label className="flex flex-col gap-1 text-[11px] text-muted-foreground">
            WhatsApp número (visible)
            <Input value={waNumero} onChange={(e) => setWaNumero(e.target.value)} aria-label="WhatsApp número" placeholder="+57 300 0000000" />
          </label>
        </div>

        <label className="flex flex-col gap-1 text-[11px] text-muted-foreground">
          Datos de packs (JSON, opcional) — p. ej. agenda/faq
          <textarea
            value={packsJson} onChange={(e) => setPacksJson(e.target.value)} aria-label="Datos de packs (JSON)"
            rows={4} placeholder='{"agenda": {"servicios": [...]}}'
            className="w-full text-xs font-mono rounded-md border border-input bg-surface px-3 py-2 text-foreground"
          />
        </label>

        <Button type="submit" disabled={enviando} className="self-start">
          {enviando && <Loader2 className="animate-spin" />}
          {enviando ? 'Encolando…' : 'Crear empresa'}
        </Button>
      </form>

      {error && (
        <div role="alert" className="text-xs text-destructive bg-destructive/10 border border-destructive/40 rounded-md px-3 py-2">
          {error}
        </div>
      )}

      {job && (
        <div role="status" className="text-xs rounded-md border border-border bg-surface-2 px-3 py-2 flex flex-col gap-1">
          <div className="flex items-center gap-2">
            <span className="text-muted-foreground">Estado del alta:</span>
            <span className="font-semibold text-foreground">{job.estado}</span>
            {!TERMINALES.has(job.estado) && <Loader2 className="size-3.5 animate-spin text-muted-foreground" />}
          </div>
          {job.estado === 'ok' && job.resumen && <p className="text-success break-all">{job.resumen}</p>}
          {job.estado === 'error' && <p className="text-destructive">{job.error || 'Falló el aprovisionamiento.'}</p>}
        </div>
      )}
    </Card>
  )
}
