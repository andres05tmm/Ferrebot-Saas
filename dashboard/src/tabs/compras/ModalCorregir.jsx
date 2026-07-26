/*
 * ModalCorregir — arreglar una compra ya recibida (se digitó mal una cantidad o un costo).
 *
 * Se edita el detalle y el backend aplica SOLO las diferencias: cada cambio de cantidad entra al
 * kárdex como AJUSTE y la plata se concilia (la deuda al proveedor sigue al nuevo total; con
 * "ajustar el pago" la diferencia sale o entra de la caja). Antes de guardar se ve el impacto:
 * cuánto stock se mueve y cuánta plata cambia.
 */
import { useState } from 'react'
import { toast } from 'sonner'
import { api } from '@/lib/api'
import { cop } from '@/components/shared.jsx'
import { Button } from '@/components/ui/button.jsx'
import { Input } from '@/components/ui/input.jsx'
import { Label } from '@/components/ui/label.jsx'
import {
  Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle,
} from '@/components/ui/dialog.jsx'
import { BuscadorProducto, LineasEditor, nuevaIdemKey, totalLineas } from './comunes.jsx'

export default function ModalCorregir({ pedido, onCerrar, onCorregido }) {
  const originales = (pedido?.detalles || [])
    .filter(d => d.producto_id != null)
    .map(d => ({
      producto_id: d.producto_id, nombre: d.descripcion || `Producto #${d.producto_id}`,
      cantidad: String(d.cantidad), costo: d.costo_estimado != null ? String(d.costo_estimado) : '',
    }))
  const [lineas, setLineas] = useState(originales)
  const [motivo, setMotivo] = useState('')
  const [ajustarPago, setAjustarPago] = useState(true)
  const [enviando, setEnviando] = useState(false)

  const totalAnterior = totalLineas(originales)
  const totalNuevo = totalLineas(lineas)
  const diferencia = totalNuevo - totalAnterior
  // Qué se va a mover en el inventario, línea por línea (lo que el backend hará como AJUSTE).
  const deltas = lineas
    .map(l => {
      const previa = originales.find(o => o.producto_id === l.producto_id)
      return { nombre: l.nombre, delta: Number(l.cantidad || 0) - Number(previa?.cantidad || 0) }
    })
    .filter(d => d.delta !== 0)
  const valido = lineas.length > 0
    && lineas.every(l => Number(l.cantidad) > 0 && l.costo !== '')
    && motivo.trim().length >= 3

  async function guardar(e) {
    e?.preventDefault?.()
    if (!valido || enviando) return
    setEnviando(true)
    try {
      const res = await api(`/compras/${pedido.compra_id}/corregir`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Idempotency-Key': nuevaIdemKey() },
        body: JSON.stringify({
          lineas: lineas.map(l => ({
            producto_id: l.producto_id, cantidad: Number(l.cantidad), costo: Number(l.costo),
          })),
          motivo: motivo.trim(),
          ajustar_pago: ajustarPago && diferencia !== 0,
        }),
      })
      if (res.ok) {
        toast.success('Compra corregida: inventario y plata al día')
        onCorregido()
      } else {
        const err = await res.json().catch(() => ({}))
        toast.error(typeof err?.detail === 'string' ? err.detail : 'No se pudo corregir la compra')
      }
    } catch { toast.error('Error de conexión') } finally { setEnviando(false) }
  }

  return (
    <Dialog open={pedido != null} onOpenChange={(o) => { if (!o) onCerrar() }}>
      <DialogContent aria-describedby="corregir-desc" className="max-w-xl">
        <DialogHeader>
          <DialogTitle>Corregir compra #{pedido?.id}</DialogTitle>
          <DialogDescription id="corregir-desc">
            Deja las cantidades y costos como debieron quedar. El inventario se ajusta por la
            diferencia (queda en el kárdex) y la plata se concilia sola.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={guardar} className="space-y-3">
          <BuscadorProducto onPick={(p) => setLineas(prev => [...prev, {
            producto_id: p.id, nombre: p.nombre, cantidad: '1',
            costo: p.precio_compra != null ? String(p.precio_compra) : '',
          }])} placeholder="Agregar un producto que faltaba…" />

          <LineasEditor lineas={lineas} setLineas={setLineas} etiquetaCosto="costo real" />

          <div className="rounded-md bg-surface-2/60 p-2.5 space-y-1 text-body-sm">
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground">Total anterior</span>
              <span className="tabular-nums">{cop(totalAnterior)}</span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground">Total corregido</span>
              <span className="tabular-nums font-semibold">{cop(totalNuevo)}</span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground">Diferencia</span>
              <span className={`tabular-nums font-semibold ${
                diferencia > 0 ? 'text-danger' : diferencia < 0 ? 'text-success' : ''}`}>
                {diferencia > 0 ? '+' : ''}{cop(diferencia)}
              </span>
            </div>
            {deltas.length > 0 && (
              <p className="text-caption text-muted-foreground pt-1 border-t border-border-subtle">
                Inventario: {deltas.map(d => (
                  `${d.nombre} ${d.delta > 0 ? `entran ${d.delta}` : `salen ${Math.abs(d.delta)}`}`
                )).join(' · ')}
              </p>
            )}
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="pk-motivo">¿Qué pasó?</Label>
            <Input id="pk-motivo" value={motivo} onChange={(e) => setMotivo(e.target.value)}
              aria-label="Motivo" placeholder="Digité mal el costo del bulto" />
          </div>

          {diferencia !== 0 && (
            <label className="flex items-center gap-2 text-body-sm">
              <input type="checkbox" checked={ajustarPago}
                onChange={(e) => setAjustarPago(e.target.checked)} />
              {diferencia > 0
                ? `Pagar la diferencia (${cop(diferencia)}) desde la caja`
                : `Entra a la caja lo que el proveedor devuelve (${cop(Math.abs(diferencia))})`}
            </label>
          )}

          <Button type="submit" disabled={!valido || enviando} className="w-full">
            {enviando ? 'Corrigiendo…' : 'Guardar corrección'}
          </Button>
        </form>
      </DialogContent>
    </Dialog>
  )
}
