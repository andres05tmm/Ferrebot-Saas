/*
 * HuerfanasFacturas — las deudas viejas que no casaron con ningún proveedor registrado.
 *
 * El backfill de la migración 0070 enlazó por nombre lo que casaba sin ambigüedad; lo demás quedó
 * suelto (un nombre escrito distinto, un typo). En vez de inventar un proveedor por cada texto, se
 * muestran aquí para asignarlas a mano. Si no hay ninguna, la tarjeta no se pinta.
 */
import { useState } from 'react'
import { toast } from 'sonner'
import { useQuery } from '@tanstack/react-query'
import { AlertTriangle } from '@/lib/icons.jsx'
import { api, apiJson } from '@/lib/api'
import { cop } from '@/components/shared.jsx'
import { Card } from '@/components/ui/card.jsx'

const arr = (d) => (Array.isArray(d) ? d : [])

export default function HuerfanasFacturas({ proveedores, onAsignada }) {
  const [guardando, setGuardando] = useState(null)
  const facturasQ = useQuery({
    queryKey: ['proveedores', 'facturas', 'huerfanas'],
    queryFn: () => apiJson('/proveedores/facturas'),
  })
  const huerfanas = arr(facturasQ.data).filter(f => f.proveedor_id == null)
  if (huerfanas.length === 0) return null

  async function asignar(factura, proveedorId) {
    if (!proveedorId) return
    setGuardando(factura.id)
    try {
      const res = await api(`/proveedores/facturas/${encodeURIComponent(factura.id)}/proveedor`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ proveedor_id: Number(proveedorId) }),
      })
      if (res.ok) {
        toast.success('Factura asignada')
        facturasQ.refetch(); onAsignada?.()
      } else toast.error('No se pudo asignar la factura')
    } catch { toast.error('Error de conexión') } finally { setGuardando(null) }
  }

  return (
    <Card className="p-3.5">
      <h3 className="text-body-sm font-semibold inline-flex items-center gap-1.5 mb-1">
        <AlertTriangle className="size-4 text-warning" /> Facturas sin proveedor asignado
      </h3>
      <p className="text-caption text-muted-foreground mb-2">
        Estas deudas quedaron con un nombre suelto que no casó con ningún proveedor registrado.
        Elige a quién pertenece cada una para que entre en su estado de cuenta.
      </p>
      <ul className="divide-y divide-border-subtle">
        {huerfanas.map(f => (
          <li key={f.id} className="py-2 flex items-center gap-2 flex-wrap text-body-sm">
            <div className="min-w-0 flex-1">
              <div className="truncate">{f.id} · {f.proveedor}</div>
              <div className="text-caption text-muted-foreground">
                pendiente {cop(Number(f.pendiente))} de {cop(Number(f.total))}
              </div>
            </div>
            <select defaultValue="" disabled={guardando === f.id}
              aria-label={`Proveedor de ${f.id}`}
              onChange={(e) => asignar(f, e.target.value)}
              className="h-8 px-2 rounded-md border border-border bg-surface text-body-sm">
              <option value="">— elegir proveedor —</option>
              {proveedores.map(p => <option key={p.id} value={p.id}>{p.nombre}</option>)}
            </select>
          </li>
        ))}
      </ul>
    </Card>
  )
}
