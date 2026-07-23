/*
 * TabMesas — el salón (F3 Pack Restaurante, ADR 0032 D4). Gateado por 'pack_mesas'.
 * Grilla de mesas con estado y total EN VIVO (SSE mesa_abierta/mesa_items/pedido_estado); al
 * seleccionar una mesa: abrir, agregar ítems por ronda (nombre + cantidad — el backend resuelve
 * contra el catálogo real y jamás inventa precios), precuenta y cobro con propina opcional.
 */
import { useState } from 'react'
import { toast } from 'sonner'
import { Armchair, Plus, Receipt } from 'lucide-react'
import { api } from '@/lib/api'
import { cop, useFetch } from '@/components/shared.jsx'
import { useRealtimeEvent } from '@/components/RealtimeProvider.jsx'
import { Card } from '@/components/ui/card.jsx'
import { Button } from '@/components/ui/button.jsx'
import { Input } from '@/components/ui/input.jsx'

const arr = (d) => (Array.isArray(d) ? d : [])

async function llamar(path, method, body) {
  try {
    const res = await api(path, {
      method,
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    })
    if (res.ok) return await res.json()
    const detalle = (await res.json().catch(() => null))?.detail
    toast.error(typeof detalle === 'string' ? detalle : 'No se pudo guardar')
  } catch { toast.error('Error de conexión') }
  return null
}

function PanelMesa({ mesa, onCambio }) {
  const [producto, setProducto] = useState('')
  const [cantidad, setCantidad] = useState('1')
  const [propina, setPropina] = useState('')
  const [metodo, setMetodo] = useState('efectivo')
  const abierta = mesa.pedido_id != null
  const precuentaQ = useFetch(abierta ? `/mesas/${mesa.id}/precuenta` : null)
  const pre = precuentaQ.data

  const abrir = async () => {
    if (await llamar(`/mesas/${mesa.id}/abrir`, 'POST')) { toast.success(`${mesa.nombre} abierta`); onCambio() }
  }
  const agregar = async () => {
    if (!producto.trim()) return
    const ok = await llamar(`/mesas/${mesa.id}/items`, 'POST', {
      items: [{ producto, cantidad }],
    })
    if (ok) { setProducto(''); setCantidad('1'); precuentaQ.refetch(); onCambio() }
  }
  const cobrar = async () => {
    const ok = await llamar(`/mesas/${mesa.id}/cobrar`, 'POST', {
      metodo_pago: metodo, propina: propina || null,
    })
    if (ok) { toast.success(`${mesa.nombre} cobrada: ${cop(ok.total)}`); onCambio() }
  }

  if (!abierta) {
    return (
      <Card className="p-4 space-y-3">
        <div className="font-semibold">{mesa.nombre} — libre</div>
        <Button onClick={abrir}><Armchair className="size-4" /> Abrir mesa</Button>
      </Card>
    )
  }
  return (
    <Card className="p-4 space-y-3">
      <div className="font-semibold">{mesa.nombre} — orden #{mesa.pedido_id}</div>
      {/* Precuenta en vivo */}
      <ul className="text-[13px] text-muted-foreground space-y-0.5">
        {(pre?.items || []).map(i => (
          <li key={i.id}>
            {Number(i.cantidad)}× {i.nombre} — {cop(i.subtotal)}
            {i.modificadores?.length > 0 && (
              <span className="block pl-3 text-[11px] italic">
                {i.modificadores.map(m => m.opcion).join(', ')}
              </span>
            )}
          </li>
        ))}
      </ul>
      <div className="font-semibold tabular-nums">Total: {cop(pre?.total ?? mesa.total ?? 0)}</div>
      {/* Agregar ronda */}
      <div className="flex gap-2">
        <Input placeholder="Producto" value={producto} onChange={e => setProducto(e.target.value)} />
        <Input className="w-20" type="number" min="0.001" step="any" aria-label="Cantidad"
          value={cantidad} onChange={e => setCantidad(e.target.value)} />
        <Button variant="outline" onClick={agregar} aria-label="Agregar ítem"><Plus className="size-4" /></Button>
      </div>
      {/* Cobro: método + propina opcional (elegida por el cliente; solo salón) */}
      <div className="flex gap-2 items-center">
        <select className="h-9 rounded-md border border-input bg-transparent px-2 text-sm"
          aria-label="Método de pago" value={metodo} onChange={e => setMetodo(e.target.value)}>
          <option value="efectivo">Efectivo</option>
          <option value="transferencia">Transferencia</option>
          <option value="datafono">Datáfono</option>
        </select>
        <Input className="w-28" type="number" min="0" placeholder="Propina $" aria-label="Propina"
          value={propina} onChange={e => setPropina(e.target.value)} />
        <Button onClick={cobrar}><Receipt className="size-4" /> Cobrar</Button>
      </div>
    </Card>
  )
}

export default function TabMesas() {
  const mesasQ = useFetch('/mesas')
  const [seleccion, setSeleccion] = useState(null)
  useRealtimeEvent(['mesa_abierta', 'mesa_items', 'pedido_estado'], () => mesasQ.refetch())
  const mesas = arr(mesasQ.data)
  const activa = mesas.find(m => m.id === seleccion) || null

  return (
    <div className="flex flex-col gap-3">
      <h1 className="text-base font-semibold inline-flex items-center gap-2">
        <Armchair className="size-4.5 text-primary" /> Mesas
      </h1>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
        {mesas.map(m => (
          <Card key={m.id}
            className={`p-3 cursor-pointer space-y-1 ${m.id === seleccion ? 'ring-2 ring-primary' : ''}`}
            onClick={() => setSeleccion(m.id)} role="button" aria-label={`Mesa ${m.nombre}`}>
            <div className="font-semibold text-[13px]">{m.nombre}</div>
            <div className="text-[12px] text-muted-foreground">{m.zona || ''}</div>
            {m.pedido_id != null
              ? <div className="text-[13px] font-semibold tabular-nums text-primary">{cop(m.total)}</div>
              : <div className="text-[12px] text-emerald-600">Libre</div>}
          </Card>
        ))}
        {mesas.length === 0 && (
          <Card className="p-3 col-span-full text-center text-[13px] text-muted-foreground">
            Sin mesas configuradas (créalas desde administración).
          </Card>
        )}
      </div>
      {activa && <PanelMesa mesa={activa} onCambio={() => mesasQ.refetch()} />}
    </div>
  )
}
