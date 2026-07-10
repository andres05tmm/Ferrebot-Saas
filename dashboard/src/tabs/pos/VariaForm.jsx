/*
 * VariaForm — venta varia (sin catálogo): descripción + cantidad + precio explícito.
 * No mueve inventario; el precio SÍ viaja al backend (es la única línea con precio del cliente).
 */
import { useState } from 'react'
import { Card } from '@/components/ui/card.jsx'
import { Input } from '@/components/ui/input.jsx'
import { Button } from '@/components/ui/button.jsx'

export default function VariaForm({ onAdd }) {
  const [descripcion, setDescripcion] = useState('')
  const [cantidad, setCantidad] = useState('1')
  const [precio, setPrecio] = useState('')
  function agregar() {
    const c = Number(cantidad), p = Number(precio)
    if (!descripcion.trim() || !c || !p) return
    onAdd({ descripcion: descripcion.trim(), cantidad: c, precio_unitario: p })
    setDescripcion(''); setCantidad('1'); setPrecio('')
  }
  return (
    <Card className="p-3">
      <h2 className="text-caption font-semibold uppercase tracking-wider text-muted-foreground mb-2">Venta varia (sin catálogo)</h2>
      <div className="flex flex-wrap items-center gap-2">
        <Input value={descripcion} onChange={(e) => setDescripcion(e.target.value)}
          placeholder="Descripción" aria-label="Descripción varia" className="flex-1 min-w-[140px] h-9" />
        <Input type="number" min="0" step="any" value={cantidad} onChange={(e) => setCantidad(e.target.value)}
          aria-label="Cantidad varia" className="w-20 h-9 text-center" />
        <Input type="number" min="0" step="any" value={precio} onChange={(e) => setPrecio(e.target.value)}
          placeholder="Precio" aria-label="Precio varia" className="w-28 h-9" />
        <Button variant="outline" onClick={agregar} className="h-9">Agregar</Button>
      </div>
    </Card>
  )
}
