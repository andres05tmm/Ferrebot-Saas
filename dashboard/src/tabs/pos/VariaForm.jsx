/*
 * VariaForm — venta miscelánea (sin catálogo): descripción + cantidad + precio explícito.
 * No mueve inventario; el precio SÍ viaja al backend (única línea con precio del cliente).
 * Se usa DENTRO del modal "Venta miscelánea" (réplica del viejo): sin Card ni heading propios.
 */
import { useState } from 'react'
import { Input } from '@/components/ui/input.jsx'
import { Button } from '@/components/ui/button.jsx'
import { Label } from '@/components/ui/label.jsx'

export default function VariaForm({ onAdd }) {
  const [descripcion, setDescripcion] = useState('')
  const [cantidad, setCantidad] = useState('1')
  const [precio, setPrecio] = useState('')
  const valido = descripcion.trim() && Number(cantidad) > 0 && Number(precio) > 0

  function agregar(e) {
    e?.preventDefault?.()
    if (!valido) return
    onAdd({ descripcion: descripcion.trim(), cantidad: Number(cantidad), precio_unitario: Number(precio) })
    setDescripcion(''); setCantidad('1'); setPrecio('')
  }

  return (
    <form onSubmit={agregar} className="space-y-3">
      <div className="space-y-1.5">
        <Label htmlFor="vf-desc">Descripción</Label>
        <Input id="vf-desc" value={descripcion} onChange={(e) => setDescripcion(e.target.value)} autoFocus
          placeholder="Flete, alambre suelto…" aria-label="Descripción varia" />
      </div>
      <div className="flex gap-2">
        <div className="space-y-1.5 w-24">
          <Label htmlFor="vf-cant">Cantidad</Label>
          <Input id="vf-cant" type="number" min="0" step="any" value={cantidad}
            onChange={(e) => setCantidad(e.target.value)} aria-label="Cantidad varia" className="text-center" />
        </div>
        <div className="space-y-1.5 flex-1">
          <Label htmlFor="vf-precio">Precio</Label>
          <Input id="vf-precio" type="number" min="0" step="any" value={precio}
            onChange={(e) => setPrecio(e.target.value)} placeholder="0" aria-label="Precio varia" />
        </div>
      </div>
      <Button type="submit" disabled={!valido} className="w-full">Agregar al carrito</Button>
    </form>
  )
}
