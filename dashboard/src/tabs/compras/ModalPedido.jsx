/*
 * ModalPedido — registrar la compra AL HACER EL PEDIDO (arranca el cronómetro).
 *
 * La mercancía todavía no llegó: esto NO mueve inventario. Se capturan los productos con cantidad y
 * costo unitario (obligatorio: es lo que permite saber cuánto se comprometió y cuadrar al recibir) y
 * la forma de pago — de contado ahora, anticipo parcial, o a crédito. Si se paga algo ahora, se
 * declara de dónde sale: solo "efectivo de la caja" mueve el arqueo del día.
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
import { BuscadorProducto, LineasEditor, OrigenFondos, nuevaIdemKey, totalLineas } from './comunes.jsx'

const FORMAS = [
  { id: 'contado', label: 'De contado (pago ahora)' },
  { id: 'anticipado', label: 'Anticipo + saldo' },
  { id: 'credito', label: 'A crédito (le pago después)' },
]

export default function ModalPedido({ abierto, onCerrar, onCreado }) {
  const [proveedor, setProveedor] = useState('')
  const [descripcion, setDescripcion] = useState('')
  const [fechaEstimada, setFechaEstimada] = useState('')
  const [lineas, setLineas] = useState([])   // {producto_id, nombre, cantidad, costo_estimado}
  const [forma, setForma] = useState('contado')
  const [anticipo, setAnticipo] = useState('')
  const [origen, setOrigen] = useState('caja')
  const [enviando, setEnviando] = useState(false)

  const total = totalLineas(lineas, 'costo_estimado')
  const lineasCompletas = lineas.length > 0
    && lineas.every(l => Number(l.cantidad) > 0 && l.costo_estimado !== '' && Number(l.costo_estimado) >= 0)
  const anticipoValido = forma !== 'anticipado'
    || (Number(anticipo) > 0 && Number(anticipo) < total)
  const valido = proveedor.trim() && lineasCompletas && anticipoValido

  function limpiar() {
    setProveedor(''); setDescripcion(''); setFechaEstimada(''); setLineas([])
    setForma('contado'); setAnticipo(''); setOrigen('caja')
  }

  async function crear(e) {
    e?.preventDefault?.()
    if (!valido || enviando) return
    const payload = {
      proveedor: { nombre: proveedor.trim() },
      descripcion: descripcion.trim() || null,
      fecha_estimada: fechaEstimada || null,
      condicion_pago: forma,
      origen_fondos: origen,
      lineas: lineas.map(l => ({
        producto_id: l.producto_id,
        cantidad: Number(l.cantidad),
        costo_estimado: Number(l.costo_estimado),
      })),
    }
    if (forma === 'anticipado') payload.anticipo = Number(anticipo)
    setEnviando(true)
    try {
      const res = await api('/pedidos-proveedor', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Idempotency-Key': nuevaIdemKey() },
        body: JSON.stringify(payload),
      })
      if (res.ok) {
        toast.success('Compra registrada — el cronómetro está corriendo')
        limpiar(); onCreado()
      } else {
        const err = await res.json().catch(() => ({}))
        toast.error(typeof err?.detail === 'string' ? err.detail : 'No se pudo registrar la compra')
      }
    } catch { toast.error('Error de conexión') } finally { setEnviando(false) }
  }

  return (
    <Dialog open={abierto} onOpenChange={(o) => { if (!o) onCerrar() }}>
      <DialogContent aria-describedby="pedido-desc" className="max-w-xl">
        <DialogHeader>
          <DialogTitle>Nueva compra al proveedor</DialogTitle>
          <DialogDescription id="pedido-desc">
            Se registra al hacer el pedido: la mercancía todavía no entra al inventario, pero el
            cronómetro empieza a contar cuánto tarda el proveedor.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={crear} className="space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label htmlFor="pc-proveedor">Proveedor</Label>
              <Input id="pc-proveedor" value={proveedor} onChange={(e) => setProveedor(e.target.value)}
                placeholder="Ferrisariato" aria-label="Proveedor" />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="pc-fecha">Llega aprox.</Label>
              <Input id="pc-fecha" type="date" value={fechaEstimada} aria-label="Fecha estimada"
                onChange={(e) => setFechaEstimada(e.target.value)} />
            </div>
          </div>

          <div className="space-y-1.5">
            <Label>Productos que se están comprando</Label>
            <BuscadorProducto onPick={(p) => setLineas(prev => [...prev, {
              producto_id: p.id, nombre: p.nombre, cantidad: '1',
              costo_estimado: p.precio_compra != null ? String(p.precio_compra) : '',
            }])} />
            <LineasEditor lineas={lineas} setLineas={setLineas} campoCosto="costo_estimado" />
          </div>

          <div className="flex items-center justify-between text-body-sm border-t border-border-subtle pt-2">
            <span className="text-muted-foreground">Total de la compra</span>
            <span className="font-semibold tabular-nums">{cop(total)}</span>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="pc-desc">Nota (opcional)</Label>
            <Input id="pc-desc" value={descripcion} onChange={(e) => setDescripcion(e.target.value)}
              placeholder="Pedido del lunes por teléfono" aria-label="Nota" />
          </div>

          <div className="space-y-1.5">
            <Label>¿Cómo se paga?</Label>
            <div className="flex gap-2 flex-wrap">
              {FORMAS.map(o => (
                <button key={o.id} type="button" onClick={() => setForma(o.id)}
                  aria-pressed={forma === o.id}
                  className={`px-2.5 py-1 rounded-md border text-body-sm ${
                    forma === o.id ? 'border-primary bg-primary/10 text-primary' : 'border-border'}`}>
                  {o.label}
                </button>
              ))}
            </div>
          </div>

          {forma === 'anticipado' && (
            <div className="space-y-1.5">
              <Label htmlFor="pc-anticipo">¿Cuánto se abona ahora?</Label>
              <Input id="pc-anticipo" type="number" inputMode="numeric" min="0" value={anticipo}
                aria-label="Anticipo" onChange={(e) => setAnticipo(e.target.value)} placeholder="0" />
              {anticipo !== '' && !anticipoValido && (
                <p className="text-caption text-warning">
                  El anticipo debe ser mayor que 0 y menor que {cop(total)} (si se paga completo, usa
                  «De contado»).
                </p>
              )}
            </div>
          )}

          {forma !== 'credito' && (
            <div className="space-y-1.5">
              <Label>¿De dónde sale la plata?</Label>
              <OrigenFondos valor={origen} onCambio={setOrigen} />
              <p className="text-caption text-muted-foreground">
                Solo el efectivo de la caja mueve el arqueo del día; lo demás queda registrado con su
                procedencia.
              </p>
            </div>
          )}

          <Button type="submit" disabled={!valido || enviando} className="w-full">
            {enviando ? 'Registrando…' : 'Registrar compra'}
          </Button>
        </form>
      </DialogContent>
    </Dialog>
  )
}
