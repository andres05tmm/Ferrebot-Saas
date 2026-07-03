/*
 * TabFacturacion — historial + emisión de factura electrónica (Fase 12, Slice 3).
 * Gateado por la feature `facturacion_electronica` (la ruta solo aparece si /config la trae).
 * Historial: GET /facturas (estado en vivo por SSE). Emitir: POST /facturas {venta_id} sobre una venta
 * reciente NO facturada, SIEMPRE tras una confirmación fuerte (documento legal e IRREVERSIBLE).
 * Detalle (GET /facturas/{id}) al expandir: total y motivo de rechazo si aplica.
 */
import { useEffect, useMemo, useState } from 'react'
import { useOutletContext } from 'react-router-dom'
import { toast } from 'sonner'
import { AlertTriangle, ChevronDown, FileText, Receipt } from 'lucide-react'
import { api, apiJson } from '@/lib/api'
import { useFetch, cop } from '@/components/shared.jsx'
import { useRealtimeEvent } from '@/components/RealtimeProvider.jsx'
import { Card } from '@/components/ui/card.jsx'
import { Badge } from '@/components/ui/badge.jsx'

const EVENTOS = ['factura_pendiente', 'factura_aceptada', 'factura_rechazada', 'factura_error', 'reconnected']

const FECHA_CO = { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', timeZone: 'America/Bogota' }

const ESTADO_BADGE = {
  pendiente:  'bg-info/10 text-info border-info/20',
  enviada:    'bg-info/10 text-info border-info/20',
  aceptada:   'bg-success/10 text-success border-success/20',
  rechazada:  'bg-destructive/10 text-destructive border-destructive/30',
  error:      'bg-warning/10 text-warning border-warning/20',
}

export default function TabFacturacion() {
  const { refreshKey } = useOutletContext() ?? {}
  const facturasQ = useFetch('/facturas', [refreshKey])
  const ventasQ = useFetch('/ventas', [refreshKey])
  useRealtimeEvent(EVENTOS, () => { facturasQ.refetch(); ventasQ.refetch() })

  const [confirmando, setConfirmando] = useState(null) // venta a facturar (abre el diálogo)
  const [emitiendo, setEmitiendo] = useState(false)

  const facturas = Array.isArray(facturasQ.data) ? facturasQ.data : []
  const ventas = Array.isArray(ventasQ.data) ? ventasQ.data : []
  const facturadas = useMemo(
    () => new Set(facturas.map(f => f.venta_id).filter(v => v != null)),
    [facturas],
  )

  async function emitir(venta) {
    setEmitiendo(true)
    try {
      const res = await api('/facturas', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Idempotency-Key': crypto.randomUUID() },
        body: JSON.stringify({ venta_id: venta.id }),
      })
      if (res.ok) { toast.success('Factura en emisión'); facturasQ.refetch() }
      else if (res.status === 404) toast.error('Facturación electrónica no disponible')
      else toast.error('No se pudo emitir la factura')
    } catch {
      toast.error('Error de conexión')
    } finally {
      setEmitiendo(false)
      setConfirmando(null)
    }
  }

  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
      <EmitirVentas ventas={ventas} facturadas={facturadas} onFacturar={setConfirmando} />
      <Historial facturas={facturas} loading={facturasQ.loading} />

      {confirmando && (
        <ConfirmacionEmision
          venta={confirmando}
          emitiendo={emitiendo}
          onConfirmar={() => emitir(confirmando)}
          onCancelar={() => setConfirmando(null)}
        />
      )}
    </div>
  )
}

function EmitirVentas({ ventas, facturadas, onFacturar }) {
  return (
    <Card className="p-3.5">
      <h2 className="text-sm font-semibold mb-3 inline-flex items-center gap-1.5">
        <Receipt className="size-4" /> Emitir factura
      </h2>
      {ventas.length === 0 ? (
        <p className="py-8 text-center text-sm text-muted-foreground">Sin ventas recientes.</p>
      ) : (
        <ul className="divide-y divide-border-subtle">
          {ventas.map(v => {
            const yaFacturada = facturadas.has(v.id)
            return (
              <li key={v.id} className="py-2 flex items-center gap-2 text-[13px]">
                <div className="min-w-0 flex-1">
                  <div className="font-medium">N.º {v.consecutivo}</div>
                  <div className="text-[11px] text-muted-foreground capitalize">{v.metodo_pago}</div>
                </div>
                <span className="tabular font-semibold shrink-0">{cop(Number(v.total))}</span>
                {yaFacturada ? (
                  <Badge variant="outline" className="h-5 text-[10px] text-muted-foreground shrink-0">facturada</Badge>
                ) : (
                  <button onClick={() => onFacturar(v)}
                    className="text-[11px] px-2.5 h-7 rounded-md bg-primary text-primary-foreground hover:bg-primary-hover shrink-0">
                    Facturar
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

function Historial({ facturas, loading }) {
  return (
    <Card className="p-0 overflow-hidden">
      <div className="px-3.5 py-2.5 border-b border-border-subtle flex items-center gap-2">
        <FileText className="size-4 text-muted-foreground" />
        <h2 className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">Historial de facturas</h2>
      </div>
      {loading ? (
        <p className="py-10 text-center text-sm text-muted-foreground">Cargando…</p>
      ) : facturas.length === 0 ? (
        <p className="py-10 text-center text-sm text-muted-foreground">Aún no hay facturas.</p>
      ) : (
        <ul className="divide-y divide-border-subtle">
          {facturas.map(f => <FacturaRow key={f.id} factura={f} />)}
        </ul>
      )}
    </Card>
  )
}

function FacturaRow({ factura }) {
  const [abierta, setAbierta] = useState(false)
  return (
    <li className="px-3.5 py-2.5">
      <button onClick={() => setAbierta(a => !a)} className="w-full flex items-center gap-3 text-left">
        <div className="min-w-0 flex-1">
          <div className="text-[13px] font-medium">
            {[factura.prefijo, factura.consecutivo].filter(v => v != null).join('-') || `#${factura.id}`}
          </div>
          <div className="text-[11px] text-muted-foreground truncate">
            {factura.creado_en ? new Date(factura.creado_en).toLocaleString('es-CO', FECHA_CO) : '—'}
            {factura.cufe ? ` · CUFE ${String(factura.cufe).slice(0, 12)}…` : ''}
          </div>
        </div>
        <Badge variant="outline" className={`h-5 text-[10px] capitalize shrink-0 ${ESTADO_BADGE[factura.estado] || ''}`}>
          {factura.estado}
        </Badge>
        <ChevronDown className={`size-4 text-muted-foreground shrink-0 transition-transform ${abierta ? 'rotate-180' : ''}`} />
      </button>
      {abierta && <DetalleFactura facturaId={factura.id} />}
    </li>
  )
}

function DetalleFactura({ facturaId }) {
  const [detalle, setDetalle] = useState(null)
  const [estado, setEstado] = useState('cargando')

  useEffect(() => {
    let cancelado = false
    apiJson(`/facturas/${facturaId}`)
      .then(d => { if (!cancelado) { setDetalle(d); setEstado('listo') } })
      .catch(() => { if (!cancelado) setEstado('error') })
    return () => { cancelado = true }
  }, [facturaId])

  if (estado === 'cargando') return <p className="mt-2 text-[11px] text-muted-foreground">Cargando detalle…</p>
  if (estado === 'error' || !detalle) return <p className="mt-2 text-[11px] text-destructive">No se pudo cargar el detalle.</p>

  return (
    <div className="mt-2.5 space-y-1.5 bg-surface-2/50 rounded-md p-2.5 text-[12px]">
      {detalle.total != null && (
        <div className="flex justify-between"><span className="text-muted-foreground">Total</span>
          <span className="tabular font-semibold">{cop(Number(detalle.total))}</span></div>
      )}
      {detalle.emitido_en && (
        <div className="flex justify-between"><span className="text-muted-foreground">Emitida</span>
          <span className="tabular">{new Date(detalle.emitido_en).toLocaleString('es-CO', FECHA_CO)}</span></div>
      )}
      {detalle.cufe && (
        <div className="flex justify-between gap-3"><span className="text-muted-foreground">CUFE</span>
          <span className="tabular truncate">{detalle.cufe}</span></div>
      )}
      {detalle.motivo && (
        <div className="flex items-start gap-1.5 text-destructive pt-1 border-t border-border-subtle">
          <AlertTriangle className="size-3.5 mt-0.5 shrink-0" />
          <span>Motivo del rechazo: {detalle.motivo}</span>
        </div>
      )}
    </div>
  )
}

function ConfirmacionEmision({ venta, emitiendo, onConfirmar, onCancelar }) {
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/60 p-4" role="dialog" aria-modal="true">
      <Card className="max-w-md w-full p-5">
        <div className="flex items-start gap-3">
          <span className="grid place-items-center size-9 rounded-full bg-destructive/10 text-destructive shrink-0">
            <AlertTriangle className="size-5" />
          </span>
          <div>
            <h2 className="text-base font-semibold">Confirmar emisión</h2>
            <p className="text-[13px] text-muted-foreground mt-1.5">
              Vas a emitir una factura electrónica REAL ante la DIAN. Es un documento legal e IRREVERSIBLE.
              ¿Continuar?
            </p>
            <p className="text-[12px] text-muted-foreground mt-2">Venta N.º {venta.consecutivo} · {cop(Number(venta.total))}</p>
          </div>
        </div>
        <div className="flex justify-end gap-2 mt-5">
          <button onClick={onCancelar} disabled={emitiendo}
            className="text-sm px-4 h-9 rounded-md border border-border bg-surface hover:bg-surface-2 disabled:opacity-60">
            Cancelar
          </button>
          <button onClick={onConfirmar} disabled={emitiendo}
            className="text-sm px-4 h-9 rounded-md bg-destructive text-destructive-foreground hover:opacity-90 disabled:opacity-60">
            {emitiendo ? 'Emitiendo…' : 'Sí, emitir factura'}
          </button>
        </div>
      </Card>
    </div>
  )
}
