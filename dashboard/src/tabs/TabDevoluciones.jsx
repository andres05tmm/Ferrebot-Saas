/*
 * TabDevoluciones — devoluciones con reintegro (ADR 0026). Gateada por la feature fina 'ventas'.
 * Flujo: buscar una venta por su número (GET /ventas/{id}), ver sus líneas y registrar la devolución
 * (POST /devoluciones) total o parcial. La operación es IDEMPOTENTE: se genera una Idempotency-Key por
 * venta cargada, así un doble clic no duplica el reintegro. Si la venta fue facturada, el backend emite
 * la nota crédito (reintentable) sin bloquear la devolución. RBAC vendedor+.
 */
import { useState } from 'react'
import { toast } from 'sonner'
import { Undo2, Search } from 'lucide-react'
import { cop } from '@/components/shared.jsx'
import { useVenta, useRegistrarDevolucion } from '@/lib/queries'
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
      <div className="flex items-center gap-2">
        <h2 className="text-sm font-semibold">Venta #{venta.consecutivo}</h2>
        <span className="text-[11px] text-muted-foreground">{fechaCorta(venta.fecha)}</span>
        <span className="ml-auto tabular-nums font-semibold">{cop(venta.total)}</span>
      </div>

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

export default function TabDevoluciones() {
  const [ventaId, setVentaId] = useState('')
  const [buscar, setBuscar] = useState(null)   // id confirmado a cargar

  const ventaQ = useVenta(buscar)
  const venta = ventaQ.data

  function onBuscar() {
    const id = Number(ventaId)
    if (!(id > 0)) { toast.error('Escribe el número de la venta'); return }
    setBuscar(id)
  }

  function reset() { setBuscar(null); setVentaId('') }

  return (
    <div className="space-y-3">
      <h1 className="text-base font-semibold inline-flex items-center gap-2">
        <Undo2 className="size-4.5 text-primary" /> Devoluciones
      </h1>

      <Card className="p-3">
        <div className="flex items-end gap-2">
          <div className="relative flex-1">
            <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 size-4 text-muted-foreground" />
            <Input type="number" value={ventaId} onChange={e => setVentaId(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') onBuscar() }}
              placeholder="Número de la venta a devolver" aria-label="Número de venta" className="h-10 pl-8" />
          </div>
          <Button onClick={onBuscar}>Buscar venta</Button>
        </div>
      </Card>

      {!buscar ? (
        <Card className="p-10 text-center text-sm text-muted-foreground">
          Escribe el número de una venta para registrar su devolución.
        </Card>
      ) : ventaQ.isLoading ? (
        <Card className="p-10 text-center text-sm text-muted-foreground">Cargando venta…</Card>
      ) : ventaQ.isError || !venta ? (
        <Card className="p-10 text-center text-sm text-destructive">
          No se encontró la venta #{buscar}.
        </Card>
      ) : (
        <DetalleVenta venta={venta} onHecho={reset} />
      )}
    </div>
  )
}
