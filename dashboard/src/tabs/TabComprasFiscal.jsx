/*
 * TabComprasFiscal — compras fiscales + eventos RADIAN sobre FE recibidas (Fase 12, Slices 6a/6b).
 * SOLO admin. Gateado por la feature `compras_fiscal` (la ruta solo aparece con la capacidad).
 *
 * DATOS (6a): registrar (POST /compras-fiscal), listar, y "marcar fiscal" una compra normal (to-fiscal).
 * RADIAN-FE (6b): sobre cada compra fiscal, pegar el CUFE + Importar (acuse 030), luego Aceptar (032+033)
 * o Reclamar (031). ⚠️ Son ACCIONES DIAN REALES: cada una exige CONFIRMACIÓN FUERTE que muestra el
 * ambiente (pruebas|produccion); solo al confirmar se dispara el POST. Datos por api.js. Live: 'reconnected'.
 */
import { useMemo, useState } from 'react'
import { useOutletContext } from 'react-router-dom'
import { toast } from 'sonner'
import { AlertTriangle, FileCog, Tag } from 'lucide-react'
import { api } from '@/lib/api'
import { useFetch, cop, mesActualCO } from '@/components/shared.jsx'
import { useRealtimeEvent } from '@/components/RealtimeProvider.jsx'
import { useAuth } from '@/hooks/useAuth.js'
import { Card } from '@/components/ui/card.jsx'
import { Input } from '@/components/ui/input.jsx'
import { Badge } from '@/components/ui/badge.jsx'

const FECHA_CO = { day: '2-digit', month: 'short', timeZone: 'America/Bogota' }

const ESTADO_BADGE = {
  pendiente: 'bg-info/10 text-info border-info/20',
  aceptada: 'bg-success/10 text-success border-success/20',
  reclamada: 'bg-warning/10 text-warning border-warning/20',
}

// Descripción del evento DIAN por acción (para la confirmación fuerte).
const EVENTO_TEXTO = {
  importar: 'acuse de recibo (030)',
  aceptar: 'aceptación expresa (032 + 033)',
  reclamar: 'reclamo (031)',
}

export default function TabComprasFiscal() {
  const { isAdmin } = useAuth()
  // Registrar/listar compras fiscales es admin-only en el backend: el tab se gatea igual para el vendedor.
  if (!isAdmin()) {
    return (
      <Card className="p-8 text-center text-sm text-muted-foreground">
        Las compras fiscales son solo para administradores.
      </Card>
    )
  }
  return <ComprasFiscalContenido />
}

function ComprasFiscalContenido() {
  const { refreshKey } = useOutletContext() ?? {}
  const [rango, setRango] = useState(mesActualCO())
  const setCampoRango = (k) => (e) => setRango(prev => ({ ...prev, [k]: e.target.value }))

  const deps = [refreshKey, rango.desde, rango.hasta]
  const fiscalesQ = useFetch(`/compras-fiscal?desde=${rango.desde}&hasta=${rango.hasta}`, deps)
  const comprasQ = useFetch(`/compras?desde=${rango.desde}&hasta=${rango.hasta}`, deps)
  const ambienteQ = useFetch('/compras-fiscal/ambiente', [refreshKey])
  function recargar() { fiscalesQ.refetch(); comprasQ.refetch() }
  useRealtimeEvent(['reconnected', 'compra_registrada'], recargar)

  const fiscales = Array.isArray(fiscalesQ.data) ? fiscalesQ.data : []
  const compras = Array.isArray(comprasQ.data) ? comprasQ.data : []
  const ambiente = ambienteQ.data?.ambiente || 'pruebas'   // default seguro si aún no cargó
  // Compras que ya tienen una fiscal ligada → no se pueden volver a marcar (la idempotencia lo respeta).
  const yaFiscal = useMemo(
    () => new Set(fiscales.map(f => f.compra_id).filter(id => id != null)),
    [fiscales],
  )

  const [confirmando, setConfirmando] = useState(null)   // { accion, fiscal, cufe?, motivo? }
  const [ejecutando, setEjecutando] = useState(false)

  async function marcarFiscal(compra) {
    try {
      const res = await api(`/compras/${compra.id}/to-fiscal`, { method: 'POST' })
      if (res.ok) { toast.success('Compra marcada como fiscal'); recargar() }
      else toast.error('No se pudo marcar la compra como fiscal')
    } catch { toast.error('Error de conexión') }
  }

  async function ejecutarEvento() {
    const { accion, fiscal, cufe, motivo } = confirmando
    const url = `/compras-fiscal/${fiscal.id}/${accion}`
    const body = accion === 'importar' ? { cufe } : accion === 'reclamar' ? { motivo: motivo || null } : null
    const opciones = { method: 'POST' }
    if (body) { opciones.headers = { 'Content-Type': 'application/json' }; opciones.body = JSON.stringify(body) }
    setEjecutando(true)
    try {
      const res = await api(url, opciones)
      if (res.ok) {
        toast.success('Evento DIAN enviado')
        recargar()
      } else if (res.status === 502) {
        const b = await res.json().catch(() => ({}))
        toast.error(`MATIAS rechazó el evento: ${b.evento_error || 'error'}`)
        recargar()
      } else if (res.status === 409) {
        toast.error('Primero importa el CUFE de la factura')
      } else {
        toast.error('No se pudo enviar el evento')
      }
    } catch { toast.error('Error de conexión') } finally { setEjecutando(false); setConfirmando(null) }
  }

  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
      <div className="space-y-3">
        <RegistrarCompraFiscal onRegistrada={recargar} />
        <ComprasNormales compras={compras} loading={comprasQ.loading} yaFiscal={yaFiscal} onMarcar={marcarFiscal} />
      </div>

      <Card className="p-0 overflow-hidden">
        <div className="px-3.5 py-2.5 border-b border-border-subtle flex items-center gap-2">
          <FileCog className="size-4 text-muted-foreground" />
          <h2 className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground mr-auto">Compras fiscales · FE recibidas</h2>
          <Input type="date" value={rango.desde} onChange={setCampoRango('desde')} aria-label="Desde" className="h-7 w-[8.5rem] text-[11px]" />
          <Input type="date" value={rango.hasta} onChange={setCampoRango('hasta')} aria-label="Hasta" className="h-7 w-[8.5rem] text-[11px]" />
        </div>
        <p className="px-3.5 py-1.5 text-[11px] text-muted-foreground bg-surface-2/50 border-b border-border-subtle">
          Eventos DIAN — ambiente: <strong className="text-foreground">{ambiente}</strong>
        </p>
        {fiscalesQ.loading ? (
          <p className="py-10 text-center text-sm text-muted-foreground">Cargando…</p>
        ) : fiscales.length === 0 ? (
          <p className="py-10 text-center text-sm text-muted-foreground">Sin compras fiscales en el periodo.</p>
        ) : (
          <ul className="divide-y divide-border-subtle">
            {fiscales.map(f => <FiscalRow key={f.id} fiscal={f} onAccion={setConfirmando} />)}
          </ul>
        )}
      </Card>

      {confirmando && (
        <ConfirmacionRadian
          accion={confirmando.accion}
          ambiente={ambiente}
          ejecutando={ejecutando}
          onConfirmar={ejecutarEvento}
          onCancelar={() => setConfirmando(null)}
        />
      )}
    </div>
  )
}

function FiscalRow({ fiscal, onAccion }) {
  const [cufe, setCufe] = useState('')
  const [motivo, setMotivo] = useState('')
  const importado = !!fiscal.cufe_proveedor

  return (
    <li className="px-3.5 py-2.5 space-y-2 text-[13px]">
      <div className="flex items-center gap-3">
        <div className="min-w-0 flex-1">
          <div className="font-medium truncate">{fiscal.proveedor_nit ? `NIT ${fiscal.proveedor_nit}` : 'Sin NIT'}</div>
          <div className="text-[11px] text-muted-foreground tabular">
            base {cop(Number(fiscal.base))} · IVA {cop(Number(fiscal.iva))}
            {fiscal.creado_en ? ` · ${new Date(fiscal.creado_en).toLocaleDateString('es-CO', FECHA_CO)}` : ''}
          </div>
        </div>
        <span className="tabular font-semibold shrink-0">{cop(Number(fiscal.total))}</span>
        {fiscal.evento_estado && (
          <Badge variant="outline" className={`h-5 text-[10px] capitalize shrink-0 ${ESTADO_BADGE[fiscal.evento_estado] || ''}`}>
            {fiscal.evento_estado}
          </Badge>
        )}
      </div>

      {!importado ? (
        <div className="flex gap-2">
          <Input value={cufe} onChange={(e) => setCufe(e.target.value)} placeholder="Pega el CUFE de la factura del proveedor"
            aria-label={`CUFE ${fiscal.id}`} className="h-8 flex-1 text-[12px]" />
          <button
            onClick={() => cufe.trim() ? onAccion({ accion: 'importar', fiscal, cufe: cufe.trim() }) : toast.error('Pega el CUFE primero')}
            className="text-[11px] px-2.5 h-8 rounded-md bg-primary text-primary-foreground hover:bg-primary-hover shrink-0">
            Importar
          </button>
        </div>
      ) : (
        <div className="space-y-1.5">
          <div className="text-[11px] text-muted-foreground truncate">
            CUFE {String(fiscal.cufe_proveedor).slice(0, 16)}…
            {fiscal.evento_030_at ? ' · acuse ✓' : ''}
            {fiscal.evento_032_at ? ' · recibo ✓' : ''}
            {fiscal.evento_033_at ? ' · aceptación ✓' : ''}
            {fiscal.evento_031_at ? ' · reclamo ✓' : ''}
          </div>
          <div className="flex gap-2 items-center">
            <button onClick={() => onAccion({ accion: 'aceptar', fiscal })}
              className="text-[11px] px-2.5 h-8 rounded-md bg-success/15 text-success border border-success/30 hover:bg-success/25 shrink-0">
              Aceptar
            </button>
            <Input value={motivo} onChange={(e) => setMotivo(e.target.value)} placeholder="motivo del reclamo (opcional)"
              aria-label={`Motivo ${fiscal.id}`} className="h-8 flex-1 text-[12px]" />
            <button onClick={() => onAccion({ accion: 'reclamar', fiscal, motivo: motivo.trim() })}
              className="text-[11px] px-2.5 h-8 rounded-md bg-warning/15 text-warning border border-warning/30 hover:bg-warning/25 shrink-0">
              Reclamar
            </button>
          </div>
        </div>
      )}

      {fiscal.evento_error && (
        <div className="flex items-start gap-1.5 text-[11px] text-destructive">
          <AlertTriangle className="size-3.5 mt-0.5 shrink-0" />
          <span>Último error DIAN: {fiscal.evento_error}</span>
        </div>
      )}
    </li>
  )
}

function ConfirmacionRadian({ accion, ambiente, ejecutando, onConfirmar, onCancelar }) {
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/60 p-4" role="dialog" aria-modal="true">
      <Card className="max-w-md w-full p-5">
        <div className="flex items-start gap-3">
          <span className="grid place-items-center size-9 rounded-full bg-destructive/10 text-destructive shrink-0">
            <AlertTriangle className="size-5" />
          </span>
          <div>
            <h2 className="text-base font-semibold">Confirmar evento DIAN</h2>
            <p className="text-[13px] text-muted-foreground mt-1.5">
              Vas a enviar un evento DIAN REAL ({EVENTO_TEXTO[accion]}) sobre esta factura recibida.
              Es una acción ante la DIAN. Ambiente: <strong className="text-foreground">{ambiente}</strong>.
              ¿Continuar?
            </p>
          </div>
        </div>
        <div className="flex justify-end gap-2 mt-5">
          <button onClick={onCancelar} disabled={ejecutando}
            className="text-sm px-4 h-9 rounded-md border border-border bg-surface hover:bg-surface-2 disabled:opacity-60">
            Cancelar
          </button>
          <button onClick={onConfirmar} disabled={ejecutando}
            className="text-sm px-4 h-9 rounded-md bg-destructive text-destructive-foreground hover:opacity-90 disabled:opacity-60">
            {ejecutando ? 'Enviando…' : 'Sí, enviar evento'}
          </button>
        </div>
      </Card>
    </div>
  )
}

function RegistrarCompraFiscal({ onRegistrada }) {
  const [f, setF] = useState({ proveedor_nit: '', base: '', iva: '', total: '', soporte_url: '' })
  const [enviando, setEnviando] = useState(false)
  const set = (k) => (e) => setF(prev => ({ ...prev, [k]: e.target.value }))
  // Sugerencia de total = base + iva (informativa; el backend exige coherencia ±1 centavo).
  const sugerido = (Number(f.base) || 0) + (Number(f.iva) || 0)

  async function registrar() {
    if (!f.proveedor_nit.trim()) { toast.error('Indica el NIT del proveedor'); return }
    if (!(Number(f.total) >= 0) || !(Number(f.base) >= 0) || !(Number(f.iva) >= 0)) {
      toast.error('Base, IVA y total deben ser válidos'); return
    }
    const payload = {
      proveedor_nit: f.proveedor_nit.trim(),
      base: Number(f.base), iva: Number(f.iva), total: Number(f.total),
      soporte_url: f.soporte_url.trim() || null,
    }
    setEnviando(true)
    try {
      const res = await api('/compras-fiscal', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
      })
      if (res.status === 422) { toast.error('Montos incoherentes: base + IVA debe igualar el total'); return }
      if (!res.ok) { toast.error('No se pudo registrar la compra fiscal'); return }
      toast.success('Compra fiscal registrada')
      setF({ proveedor_nit: '', base: '', iva: '', total: '', soporte_url: '' })
      onRegistrada()
    } catch { toast.error('Error de conexión') } finally { setEnviando(false) }
  }

  return (
    <Card className="p-3.5">
      <h2 className="text-sm font-semibold mb-3 inline-flex items-center gap-1.5">
        <FileCog className="size-4" /> Nueva compra fiscal
      </h2>
      <div className="space-y-2">
        <Input value={f.proveedor_nit} onChange={set('proveedor_nit')} placeholder="NIT del proveedor *" aria-label="NIT del proveedor" className="h-9" />
        <div className="flex gap-2">
          <Input type="number" value={f.base} onChange={set('base')} placeholder="Base" aria-label="Base" className="h-9 flex-1" />
          <Input type="number" value={f.iva} onChange={set('iva')} placeholder="IVA" aria-label="IVA" className="h-9 flex-1" />
        </div>
        <div className="flex items-center gap-2">
          <Input type="number" value={f.total} onChange={set('total')} placeholder="Total *" aria-label="Total" className="h-9 flex-1" />
          {sugerido > 0 && (
            <button type="button" onClick={() => setF(prev => ({ ...prev, total: String(sugerido) }))}
              className="text-[11px] px-2 h-9 rounded-md border border-border bg-surface hover:bg-surface-2 shrink-0 text-muted-foreground">
              = {cop(sugerido)}
            </button>
          )}
        </div>
        <Input value={f.soporte_url} onChange={set('soporte_url')} placeholder="URL de soporte (opcional)" aria-label="URL de soporte" className="h-9" />
        <button onClick={registrar} disabled={enviando}
          className="w-full h-10 rounded-md bg-primary text-primary-foreground font-medium hover:bg-primary-hover disabled:opacity-60">
          {enviando ? 'Registrando…' : 'Registrar compra fiscal'}
        </button>
      </div>
    </Card>
  )
}

function ComprasNormales({ compras, loading, yaFiscal, onMarcar }) {
  return (
    <Card className="p-0 overflow-hidden">
      <div className="px-3.5 py-2.5 border-b border-border-subtle flex items-center gap-2">
        <Tag className="size-4 text-muted-foreground" />
        <h2 className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">Compras del periodo</h2>
      </div>
      {loading ? (
        <p className="py-8 text-center text-sm text-muted-foreground">Cargando…</p>
      ) : compras.length === 0 ? (
        <p className="py-8 text-center text-sm text-muted-foreground">Sin compras en el periodo.</p>
      ) : (
        <ul className="divide-y divide-border-subtle">
          {compras.map(c => {
            const fiscalizada = yaFiscal.has(c.id)
            return (
              <li key={c.id} className="px-3.5 py-2.5 flex items-center gap-3 text-[13px]">
                <div className="min-w-0 flex-1">
                  <div className="font-medium truncate">{c.proveedor_nombre || 'Proveedor'}</div>
                  <div className="text-[11px] text-muted-foreground tabular">{cop(Number(c.total))}</div>
                </div>
                {fiscalizada ? (
                  <Badge variant="outline" className="h-5 text-[10px] shrink-0 bg-success/10 text-success border-success/20">fiscal</Badge>
                ) : (
                  <button onClick={() => onMarcar(c)}
                    className="text-[11px] px-2.5 h-7 rounded-md border border-border bg-surface hover:bg-surface-2 shrink-0">
                    marcar fiscal
                  </button>
                )}
              </li>
            )
          })}
        </ul>
      )}
    </Card>
  )
}
