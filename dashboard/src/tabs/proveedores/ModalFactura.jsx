/*
 * ModalFactura — registrar a mano una factura del proveedor (la deuda que no nació de una compra).
 *
 * Viene del formulario suelto que tenía el tab viejo; ahora se abre desde la ficha del proveedor, así
 * que la deuda queda ligada a ÉL (`proveedor_id`, 0070) en vez de depender de que el nombre escrito
 * case con el registrado.
 */
import { useState } from 'react'
import { toast } from 'sonner'
import { api } from '@/lib/api'
import { Button } from '@/components/ui/button.jsx'
import { Input } from '@/components/ui/input.jsx'
import { Label } from '@/components/ui/label.jsx'
import {
  Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle,
} from '@/components/ui/dialog.jsx'

export default function ModalFactura({ proveedor, onCerrar, onCreada }) {
  const [f, setF] = useState({ id: '', descripcion: '', total: '', fecha: '', fecha_vencimiento: '' })
  const [enviando, setEnviando] = useState(false)
  const set = (k) => (e) => setF(prev => ({ ...prev, [k]: e.target.value }))

  const valido = f.id.trim() && Number(f.total) > 0
    && !(f.fecha && f.fecha_vencimiento && f.fecha_vencimiento < f.fecha)

  async function crear(e) {
    e?.preventDefault?.()
    if (!valido || enviando) return
    const payload = {
      id: f.id.trim(), proveedor: proveedor.nombre, proveedor_id: proveedor.id,
      descripcion: f.descripcion.trim() || null, total: Number(f.total),
    }
    if (f.fecha) payload.fecha = f.fecha
    if (f.fecha_vencimiento) payload.fecha_vencimiento = f.fecha_vencimiento
    setEnviando(true)
    try {
      const res = await api('/proveedores/facturas', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
      })
      if (res.status === 409) { toast.error('Ya existe una factura con ese número'); return }
      if (!res.ok) { toast.error('No se pudo registrar la factura'); return }
      toast.success('Factura registrada')
      onCreada()
    } catch { toast.error('Error de conexión') } finally { setEnviando(false) }
  }

  return (
    <Dialog open onOpenChange={(o) => { if (!o) onCerrar() }}>
      <DialogContent aria-describedby="factura-desc">
        <DialogHeader>
          <DialogTitle>Factura de {proveedor.nombre}</DialogTitle>
          <DialogDescription id="factura-desc">
            La deuda queda a nombre de este proveedor y aparece en su estado de cuenta.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={crear} className="space-y-3">
          <div className="space-y-1.5">
            <Label htmlFor="fp-id">N.º de factura</Label>
            <Input id="fp-id" value={f.id} onChange={set('id')} aria-label="Número de factura"
              placeholder="F-1234" autoFocus />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="fp-desc">Descripción (opcional)</Label>
            <Input id="fp-desc" value={f.descripcion} onChange={set('descripcion')}
              aria-label="Descripción" placeholder="Mercancía de julio" />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label htmlFor="fp-total">Total</Label>
              <Input id="fp-total" type="number" inputMode="numeric" min="0" value={f.total}
                onChange={set('total')} aria-label="Total" placeholder="0" />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="fp-fecha">Fecha</Label>
              <Input id="fp-fecha" type="date" value={f.fecha} onChange={set('fecha')}
                aria-label="Fecha factura" />
            </div>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="fp-vence">Vence (opcional)</Label>
            <Input id="fp-vence" type="date" value={f.fecha_vencimiento}
              onChange={set('fecha_vencimiento')} aria-label="Fecha de vencimiento" />
            {f.fecha && f.fecha_vencimiento && f.fecha_vencimiento < f.fecha && (
              <p className="text-caption text-warning">
                El vencimiento no puede ser anterior a la fecha de la factura.
              </p>
            )}
          </div>
          <Button type="submit" disabled={!valido || enviando} className="w-full">
            {enviando ? 'Guardando…' : 'Registrar factura'}
          </Button>
        </form>
      </DialogContent>
    </Dialog>
  )
}
