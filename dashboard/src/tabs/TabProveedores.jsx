/*
 * TabProveedores — cuentas por pagar a proveedor (Fase 12, Slice 4b). SOLO admin.
 * Resumen del total adeudado + lista de facturas con saldo; registrar factura y abono (recalcula el
 * saldo); subir foto de soporte SOLO si Cloudinary está disponible (si el endpoint da 503, se oculta el
 * control con un aviso). Datos por api.js. Live: re-fetch ante 'reconnected'.
 */
import { useState } from 'react'
import { useOutletContext } from 'react-router-dom'
import { toast } from 'sonner'
import { Building2, ImagePlus, Receipt } from 'lucide-react'
import { api } from '@/lib/api.js'
import { useFetch, cop } from '@/components/shared.jsx'
import { useRealtimeEvent } from '@/components/RealtimeProvider.jsx'
import { useAuth } from '@/hooks/useAuth.js'
import { Card } from '@/components/ui/card.jsx'
import { Input } from '@/components/ui/input.jsx'
import { Badge } from '@/components/ui/badge.jsx'

const ESTADO_BADGE = {
  pendiente: 'bg-warning/10 text-warning border-warning/20',
  pagada: 'bg-success/10 text-success border-success/20',
}

export default function TabProveedores() {
  const { isAdmin } = useAuth()
  if (!isAdmin()) {
    return (
      <Card className="p-8 text-center text-sm text-muted-foreground">
        Las cuentas por pagar son solo para administradores.
      </Card>
    )
  }
  return <ProveedoresContenido />
}

function ProveedoresContenido() {
  const { refreshKey } = useOutletContext() ?? {}
  const facturasQ = useFetch('/proveedores/facturas', [refreshKey])
  const resumenQ = useFetch('/proveedores/resumen', [refreshKey])
  useRealtimeEvent(['reconnected'], () => { facturasQ.refetch(); resumenQ.refetch() })

  // Disponibilidad de fotos: optimista; si una subida responde 503, se apaga con aviso.
  const [fotosDisponibles, setFotosDisponibles] = useState(true)

  const facturas = Array.isArray(facturasQ.data) ? facturasQ.data : []
  const resumen = resumenQ.data || { total_adeudado: 0, facturas_pendientes: 0 }

  function recargar() { facturasQ.refetch(); resumenQ.refetch() }

  async function subirFoto(factura, file) {
    const fd = new FormData()
    fd.append('file', file)
    try {
      const res = await api(`/proveedores/facturas/${encodeURIComponent(factura.id)}/foto`, { method: 'POST', body: fd })
      if (res.status === 503) {
        setFotosDisponibles(false)
        toast.error('Fotos no disponibles: Cloudinary no está configurado para esta empresa.')
        return
      }
      if (res.ok) { toast.success('Foto subida'); recargar() }
      else toast.error('No se pudo subir la foto')
    } catch { toast.error('Error de conexión') }
  }

  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
      <div className="space-y-3">
        <Card className="p-3.5">
          <div className="flex items-center gap-2">
            <Building2 className="size-4 text-muted-foreground" />
            <span className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">Total adeudado</span>
          </div>
          <div className="mt-1.5 text-2xl font-semibold tabular text-warning">{cop(Number(resumen.total_adeudado))}</div>
          <div className="text-[11px] text-muted-foreground">{resumen.facturas_pendientes} factura(s) pendiente(s)</div>
        </Card>

        <RegistrarFactura onCreada={recargar} />
        <RegistrarAbono facturas={facturas} onAbonado={recargar} />
      </div>

      <Card className="p-0 overflow-hidden">
        <div className="px-3.5 py-2.5 border-b border-border-subtle flex items-center gap-2">
          <Receipt className="size-4 text-muted-foreground" />
          <h2 className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">Cuentas por pagar</h2>
        </div>
        {!fotosDisponibles && (
          <p className="px-3.5 py-2 text-[11px] text-muted-foreground bg-surface-2/50 border-b border-border-subtle">
            Las fotos de soporte están deshabilitadas (Cloudinary no configurado).
          </p>
        )}
        {facturasQ.loading ? (
          <p className="py-10 text-center text-sm text-muted-foreground">Cargando…</p>
        ) : facturas.length === 0 ? (
          <p className="py-10 text-center text-sm text-muted-foreground">Sin cuentas por pagar.</p>
        ) : (
          <ul className="divide-y divide-border-subtle">
            {facturas.map(f => (
              <li key={f.id} className="px-3.5 py-2.5">
                <div className="flex items-center gap-3">
                  <div className="min-w-0 flex-1">
                    <div className="text-[13px] font-medium truncate">{f.proveedor} · {f.id}</div>
                    <div className="text-[11px] text-muted-foreground">
                      Pendiente <span className="tabular font-semibold">{cop(Number(f.pendiente))}</span> de {cop(Number(f.total))}
                    </div>
                  </div>
                  <Badge variant="outline" className={`h-5 text-[10px] capitalize shrink-0 ${ESTADO_BADGE[f.estado] || ''}`}>
                    {f.estado}
                  </Badge>
                  {fotosDisponibles && (
                    <label className="size-8 grid place-items-center rounded-md border border-border bg-surface text-muted-foreground hover:text-foreground hover:bg-surface-2 cursor-pointer shrink-0"
                      title="Subir foto">
                      <ImagePlus className="size-4" />
                      <input type="file" className="hidden" aria-label={`Subir foto ${f.id}`}
                        onChange={(e) => { const file = e.target.files?.[0]; if (file) subirFoto(f, file) }} />
                    </label>
                  )}
                </div>
                {f.foto_url && <a href={f.foto_url} target="_blank" rel="noreferrer" className="text-[11px] text-primary hover:underline">ver soporte</a>}
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  )
}

function RegistrarFactura({ onCreada }) {
  const [f, setF] = useState({ id: '', proveedor: '', descripcion: '', total: '', fecha: '' })
  const [enviando, setEnviando] = useState(false)
  const set = (k) => (e) => setF(prev => ({ ...prev, [k]: e.target.value }))

  async function crear() {
    if (!f.id.trim() || !f.proveedor.trim() || !(Number(f.total) > 0)) {
      toast.error('Indica nº de factura, proveedor y total válido'); return
    }
    const payload = {
      id: f.id.trim(), proveedor: f.proveedor.trim(),
      descripcion: f.descripcion.trim() || null, total: Number(f.total),
    }
    if (f.fecha) payload.fecha = f.fecha
    setEnviando(true)
    try {
      const res = await api('/proveedores/facturas', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
      })
      if (res.status === 409) { toast.error('Ya existe una factura con ese número'); return }
      if (!res.ok) { toast.error('No se pudo registrar la factura'); return }
      toast.success('Factura registrada')
      setF({ id: '', proveedor: '', descripcion: '', total: '', fecha: '' })
      onCreada()
    } catch { toast.error('Error de conexión') } finally { setEnviando(false) }
  }

  return (
    <Card className="p-3.5">
      <h2 className="text-sm font-semibold mb-3">Nueva factura de proveedor</h2>
      <div className="space-y-2">
        <Input value={f.id} onChange={set('id')} placeholder="N.º de factura *" aria-label="Número de factura" className="h-9" />
        <Input value={f.proveedor} onChange={set('proveedor')} placeholder="Proveedor *" aria-label="Proveedor" className="h-9" />
        <Input value={f.descripcion} onChange={set('descripcion')} placeholder="Descripción" aria-label="Descripción" className="h-9" />
        <div className="flex gap-2">
          <Input type="number" value={f.total} onChange={set('total')} placeholder="Total *" aria-label="Total" className="h-9 flex-1" />
          <Input type="date" value={f.fecha} onChange={set('fecha')} aria-label="Fecha factura" className="h-9 flex-1" />
        </div>
        <button onClick={crear} disabled={enviando}
          className="w-full h-10 rounded-md bg-primary text-primary-foreground font-medium hover:bg-primary-hover disabled:opacity-60">
          {enviando ? 'Guardando…' : 'Registrar factura'}
        </button>
      </div>
    </Card>
  )
}

function RegistrarAbono({ facturas, onAbonado }) {
  const pendientes = facturas.filter(f => f.estado !== 'pagada')
  const [a, setA] = useState({ factura_id: '', monto: '', fecha: '' })
  const [enviando, setEnviando] = useState(false)
  const set = (k) => (e) => setA(prev => ({ ...prev, [k]: e.target.value }))

  async function abonar() {
    if (!a.factura_id || !(Number(a.monto) > 0)) { toast.error('Elige una factura y un monto válido'); return }
    const payload = { factura_id: a.factura_id, monto: Number(a.monto) }
    if (a.fecha) payload.fecha = a.fecha
    setEnviando(true)
    try {
      const res = await api('/proveedores/abonos', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
      })
      if (res.status === 422) { toast.error('El abono excede el saldo pendiente'); return }
      if (res.status === 404) { toast.error('La factura no existe'); return }
      if (!res.ok) { toast.error('No se pudo registrar el abono'); return }
      const factura = await res.json()
      toast.success(`Abono registrado · pendiente ${cop(Number(factura.pendiente))} (${factura.estado})`)
      setA({ factura_id: '', monto: '', fecha: '' })
      onAbonado()
    } catch { toast.error('Error de conexión') } finally { setEnviando(false) }
  }

  return (
    <Card className="p-3.5">
      <h2 className="text-sm font-semibold mb-3">Nuevo abono</h2>
      <div className="space-y-2">
        <select value={a.factura_id} onChange={set('factura_id')} aria-label="Factura a abonar"
          className="h-9 w-full px-2 rounded-md border border-border bg-surface text-sm">
          <option value="">Elige una factura…</option>
          {pendientes.map(f => (
            <option key={f.id} value={f.id}>{f.proveedor} · {f.id} (pendiente {cop(Number(f.pendiente))})</option>
          ))}
        </select>
        <div className="flex gap-2">
          <Input type="number" value={a.monto} onChange={set('monto')} placeholder="Monto *" aria-label="Monto del abono" className="h-9 flex-1" />
          <Input type="date" value={a.fecha} onChange={set('fecha')} aria-label="Fecha abono" className="h-9 flex-1" />
        </div>
        <button onClick={abonar} disabled={enviando}
          className="w-full h-10 rounded-md bg-primary text-primary-foreground font-medium hover:bg-primary-hover disabled:opacity-60">
          {enviando ? 'Guardando…' : 'Registrar abono'}
        </button>
      </div>
    </Card>
  )
}
