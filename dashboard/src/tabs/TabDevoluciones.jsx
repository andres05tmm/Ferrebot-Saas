/*
 * TabDevoluciones — devoluciones con reintegro + nota crédito (ADR 0026). Gateada por la feature 'ventas'.
 * Propósito: emitir la NOTA CRÉDITO de una venta facturada. Lista las ventas con documento fiscal vivo
 * (POS/FE) —las únicas sobre las que procede una nota crédito— buscables por número o por CUFE. Al elegir
 * una, se cargan sus líneas (GET /ventas/{id}) y se registra la devolución (POST /devoluciones) total o
 * parcial: IDEMPOTENTE (Idempotency-Key por venta cargada) y el backend emite la nota crédito
 * (reintentable) sin bloquear el reintegro. RBAC vendedor+.
 */
import { useEffect, useState } from 'react'
import { toast } from 'sonner'
import { Undo2, Search, ArrowLeft, FileText } from 'lucide-react'
import { cop } from '@/components/shared.jsx'
import { useVenta, useRegistrarDevolucion, useVentasFacturadas } from '@/lib/queries'
import BadgeFiscal, { etiquetaIdentificador } from '@/components/BadgeFiscal.jsx'
import { Card } from '@/components/ui/card.jsx'
import { Input } from '@/components/ui/input.jsx'
import { Button } from '@/components/ui/button.jsx'

const arr = (d) => (Array.isArray(d) ? d : [])

// Clave idempotente por intento (uuid del navegador, con fallback simple para entornos sin crypto).
function nuevaKey() {
  try { return `dev-${crypto.randomUUID()}` } catch { return `dev-${Date.now()}-${Math.random().toString(16).slice(2)}` }
}

function fechaCorta(iso) {
  if (!iso) return '—'
  return new Date(iso).toLocaleDateString('es-CO', { day: '2-digit', month: 'short', year: 'numeric', timeZone: 'America/Bogota' })
}

function DetalleVenta({ venta, onHecho }) {
  const lineas = arr(venta.lineas).filter(l => l.producto_id != null)  // solo catálogo se puede devolver por línea
  const [parcial, setParcial] = useState(false)
  const [sel, setSel] = useState({})   // producto_id → cantidad
  const [motivo, setMotivo] = useState('')
  const [key] = useState(nuevaKey)
  const [enviando, setEnviando] = useState(false)
  const registrarM = useRegistrarDevolucion()

  function toggle(l) {
    setSel(prev => {
      const next = { ...prev }
      if (next[l.producto_id] != null) delete next[l.producto_id]
      else next[l.producto_id] = String(l.cantidad)
      return next
    })
  }

  async function registrar() {
    const body = { venta_id: venta.id, motivo: motivo.trim() || null }
    if (parcial) {
      const lineasBody = Object.entries(sel)
        .map(([pid, cant]) => ({ producto_id: Number(pid), cantidad: Number(cant) }))
        .filter(l => l.cantidad > 0)
      if (lineasBody.length === 0) { toast.error('Elige al menos una línea a devolver'); return }
      body.lineas = lineasBody
    }
    setEnviando(true)
    try {
      const res = await registrarM.mutateAsync({ body, key })
      if (res.ok) {
        const data = await res.json().catch(() => ({}))
        toast.success(`Devolución registrada · reintegro ${cop(data.total)} (${data.metodo_reintegro || 'caja'})`)
        onHecho()
      } else if (res.status === 409) {
        const b = await res.json().catch(() => ({}))
        toast.error(b.detail || 'No se puede devolver (¿caja abierta?, ¿ya devuelta?)')
      } else if (res.status === 422) {
        toast.error('Alguna línea no pertenece a esta venta')
      } else if (res.status === 404) {
        toast.error('La venta no existe')
      } else {
        toast.error('No se pudo registrar la devolución')
      }
    } catch { toast.error('Error de conexión') } finally { setEnviando(false) }
  }

  return (
    <Card className="p-3.5 space-y-3">
      <div className="flex items-center gap-2 flex-wrap">
        <h2 className="text-sm font-semibold">Venta #{venta.consecutivo}</h2>
        <span className="text-[11px] text-muted-foreground">{fechaCorta(venta.fecha)}</span>
        {venta.fiscal && <BadgeFiscal fiscal={venta.fiscal} className="text-[10px] h-5 px-1.5" />}
        <span className="ml-auto tabular-nums font-semibold">{cop(venta.total)}</span>
      </div>

      {venta.fiscal && (
        <p className="text-[12px] text-muted-foreground inline-flex items-center gap-1.5">
          <FileText className="size-3.5 shrink-0" />
          Al devolver se emite la nota crédito de esta {venta.fiscal.tipo === 'pos' ? 'venta POS' : 'factura'}
          {venta.fiscal.cufe && <span className="font-mono text-[10px] opacity-70">· {etiquetaIdentificador(venta.fiscal.tipo)} {venta.fiscal.cufe.slice(0, 12)}…</span>}
        </p>
      )}

      <label className="inline-flex items-center gap-2 text-sm">
        <input type="checkbox" checked={parcial} aria-label="Devolución parcial"
          onChange={e => { setParcial(e.target.checked); setSel({}) }} />
        Devolución parcial (elegir líneas)
      </label>

      <ul className="divide-y divide-border-subtle border-y border-border-subtle">
        {lineas.length === 0 ? (
          <li className="py-3 text-[12px] text-muted-foreground text-center">
            Esta venta no tiene líneas de catálogo devolvibles.
          </li>
        ) : lineas.map(l => (
          <li key={l.producto_id} className="py-2 flex items-center gap-2 text-[13px]">
            {parcial && (
              <input type="checkbox" checked={sel[l.producto_id] != null} onChange={() => toggle(l)}
                aria-label={`Devolver ${l.descripcion || l.producto_id}`} />
            )}
            <span className="flex-1 truncate">{l.descripcion || `Producto ${l.producto_id}`}</span>
            <span className="text-[11px] text-muted-foreground tabular-nums">vendidas {Number(l.cantidad)}</span>
            {parcial && sel[l.producto_id] != null && (
              <Input type="number" min="0" max={Number(l.cantidad)} value={sel[l.producto_id]}
                onChange={e => setSel(prev => ({ ...prev, [l.producto_id]: e.target.value }))}
                aria-label={`Cantidad a devolver ${l.producto_id}`} className="h-8 w-20" />
            )}
            <span className="tabular-nums shrink-0">{cop(l.precio_unitario)}</span>
          </li>
        ))}
      </ul>

      <Input value={motivo} onChange={e => setMotivo(e.target.value)} placeholder="Motivo (opcional)"
        aria-label="Motivo" className="h-9" />

      <div className="flex justify-end gap-2">
        <Button variant="ghost" onClick={onHecho}>Cancelar</Button>
        <Button disabled={enviando || lineas.length === 0} onClick={registrar}>
          {enviando ? 'Registrando…' : parcial ? 'Devolver líneas' : 'Devolver todo'}
        </Button>
      </div>
    </Card>
  )
}

// Una venta facturada en la lista: número, fecha, documento (badge) + CUFE, método y total. Al clic
// carga su detalle para emitir la nota crédito.
function FilaFacturada({ v, onElegir }) {
  const fiscal = { tipo: v.fiscal_tipo, estado: v.fiscal_estado, cufe: v.cufe, numero: v.fiscal_numero, prefijo: v.fiscal_prefijo }
  return (
    <li>
      <button onClick={() => onElegir(v.id)}
        className="w-full text-left py-2.5 px-1 flex items-center gap-3 hover:bg-surface-2 rounded-md transition-colors">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="text-[13px] font-semibold">N.º {v.consecutivo}</span>
            <BadgeFiscal fiscal={fiscal} className="text-[10px] h-5 px-1.5" />
            <span className="text-[11px] text-muted-foreground">{fechaCorta(v.fecha)}</span>
          </div>
          {v.cufe && (
            <div className="text-[10px] font-mono text-muted-foreground truncate mt-0.5">
              {etiquetaIdentificador(v.fiscal_tipo)} {v.cufe}
            </div>
          )}
        </div>
        <span className="text-[13px] font-semibold tabular-nums shrink-0">{cop(v.total)}</span>
      </button>
    </li>
  )
}

export default function TabDevoluciones() {
  const [q, setQ] = useState('')
  const [qDebounced, setQDebounced] = useState('')
  const [seleccionada, setSeleccionada] = useState(null)   // id de la venta a cargar en detalle

  // Debounce del término: evita una consulta por tecla mientras el cajero escribe/pega el CUFE.
  useEffect(() => {
    const t = setTimeout(() => setQDebounced(q), 250)
    return () => clearTimeout(t)
  }, [q])

  const listaQ = useVentasFacturadas(qDebounced)
  const ventas = arr(listaQ.data)
  const ventaQ = useVenta(seleccionada)
  const venta = ventaQ.data

  function volver() { setSeleccionada(null) }

  return (
    <div className="space-y-3">
      <div>
        <h1 className="text-base font-semibold inline-flex items-center gap-2">
          <Undo2 className="size-4.5 text-primary" /> Devoluciones
        </h1>
        <p className="text-[12px] text-muted-foreground mt-0.5">
          Emite la nota crédito de una venta facturada (POS o factura electrónica).
        </p>
      </div>

      {seleccionada ? (
        <div className="space-y-3">
          <Button variant="ghost" size="sm" onClick={volver} className="gap-1.5 -ml-1">
            <ArrowLeft className="size-4" /> Volver a la lista
          </Button>
          {ventaQ.isLoading ? (
            <Card className="p-10 text-center text-sm text-muted-foreground">Cargando venta…</Card>
          ) : ventaQ.isError || !venta ? (
            <Card className="p-10 text-center text-sm text-destructive">No se pudo cargar la venta.</Card>
          ) : (
            <DetalleVenta venta={venta} onHecho={volver} />
          )}
        </div>
      ) : (
        <>
          <Card className="p-3">
            <div className="relative">
              <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 size-4 text-muted-foreground" />
              <Input value={q} onChange={e => setQ(e.target.value)}
                placeholder="Buscar por número de venta o CUFE…" aria-label="Buscar venta facturada"
                className="h-10 pl-8" />
            </div>
          </Card>

          <Card className="p-3">
            {listaQ.isLoading ? (
              <p className="py-10 text-center text-sm text-muted-foreground">Cargando ventas facturadas…</p>
            ) : ventas.length === 0 ? (
              <p className="py-10 text-center text-sm text-muted-foreground">
                {qDebounced
                  ? `Sin ventas facturadas para "${qDebounced}".`
                  : 'No hay ventas con factura POS o electrónica todavía.'}
              </p>
            ) : (
              <ul className="divide-y divide-border-subtle">
                {ventas.map(v => <FilaFacturada key={v.id} v={v} onElegir={setSeleccionada} />)}
              </ul>
            )}
          </Card>
        </>
      )}
    </div>
  )
}
