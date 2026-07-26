/*
 * ModalRecibir — llegó la mercancía: se confirma lo que llegó DE VERDAD.
 *
 * Aquí sí entra al inventario (ENTRADA + costo promedio), nace la deuda si quedó a crédito y sale el
 * pago si se paga ahora. Las líneas vienen prellenadas con lo que se pidió y la condición de pago con
 * la que se declaró al pedir; ambas se pueden corregir (la realidad manda). El cuadre de inventario
 * progresivo por línea deja el stock en lo que hay FÍSICAMENTE.
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
import {
  BuscadorProducto, LineasEditor, OrigenFondos, lineaDe, pagoCuadra, partesPago, totalLineas,
} from './comunes.jsx'

export default function ModalRecibir({ pedido, onCerrar, onRecibido }) {
  const [lineas, setLineas] = useState(() =>
    (pedido?.detalles || [])
      .filter(d => d.producto_id != null)
      .map(d => ({
        producto_id: d.producto_id, nombre: d.descripcion || `Producto #${d.producto_id}`,
        // El pedido ya quedó guardado en la sub-unidad del stock: la recepción confirma en esa misma
        // unidad (si el dueño prefiere cajas, marca la casilla de la línea).
        unidad: 'sub', unidad_medida: d.unidad_medida ?? null,
        unidades_por_paquete: d.unidades_por_paquete ?? null,
        cantidad: String(d.cantidad), costo: d.costo_estimado != null ? String(d.costo_estimado) : '',
        cuadrar: false, cantidad_fisica: '',
      })),
  )
  const [condicion, setCondicion] = useState(pedido?.condicion_pago || 'contado')
  const [pagoAhora, setPagoAhora] = useState(false)
  const [origen, setOrigen] = useState('caja')
  const [pagos, setPagos] = useState([])       // partes del pago mixto ([] = un solo medio)
  const [numeroFactura, setNumeroFactura] = useState('')
  const [vencimiento, setVencimiento] = useState('')
  const [enviando, setEnviando] = useState(false)

  const total = totalLineas(lineas)
  const anticipo = Number(pedido?.anticipo || 0)
  const remanente = Math.max(0, total - anticipo)
  const lineasValidas = lineas.length > 0 && lineas.every(l => Number(l.cantidad) > 0 && l.costo !== '')
  const necesitaDestinoRemanente = condicion === 'anticipado' && remanente > 0
    && !pagoAhora && !numeroFactura.trim()
  // Lo que se paga en la recepción: el remanente si hubo anticipo, si no el total.
  const montoAhora = pagoAhora ? (anticipo > 0 ? remanente : total) : 0
  const valido = lineasValidas && !necesitaDestinoRemanente && pagoCuadra(pagos, montoAhora)

  async function recibir(e) {
    e?.preventDefault?.()
    if (!valido || enviando) return
    const payload = {
      lineas: lineas.map(l => ({
        producto_id: l.producto_id, cantidad: Number(l.cantidad), costo: Number(l.costo),
        unidad: l.unidad || 'sub',
        cantidad_fisica: l.cuadrar && l.cantidad_fisica !== '' ? Number(l.cantidad_fisica) : null,
      })),
      condicion_pago: condicion,
      pago_ahora: pagoAhora,
      origen_fondos: origen,
      pagos: pagos.length > 0 ? partesPago(pagos) : [],
      numero_factura: numeroFactura.trim() || null,
      fecha_vencimiento: vencimiento || null,
    }
    setEnviando(true)
    try {
      const res = await api(`/pedidos-proveedor/${pedido.id}/recibir`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })
      if (res.ok) {
        toast.success('Mercancía recibida: inventario y cuentas al día')
        onRecibido()
      } else {
        const err = await res.json().catch(() => ({}))
        toast.error(typeof err?.detail === 'string' ? err.detail : 'No se pudo registrar la recepción')
      }
    } catch { toast.error('Error de conexión') } finally { setEnviando(false) }
  }

  return (
    <Dialog open={pedido != null} onOpenChange={(o) => { if (!o) onCerrar() }}>
      <DialogContent aria-describedby="recibir-desc" className="max-w-xl">
        <DialogHeader>
          <DialogTitle>Llegó la mercancía — compra #{pedido?.id}</DialogTitle>
          <DialogDescription id="recibir-desc">
            Confirma lo que llegó de verdad (cantidad y costo real). Esto entra al inventario y
            asienta la deuda o el pago; después se puede corregir si hubo un error.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={recibir} className="space-y-3">
          <BuscadorProducto onPick={(p) => setLineas(prev => [...prev, lineaDe(p)])}
            placeholder="Agregar producto que llegó…" />

          <LineasEditor
            lineas={lineas} setLineas={setLineas} etiquetaCosto="costo real"
            extraFila={(l, i, set) => (
              <label className="flex items-center gap-2 text-caption text-muted-foreground">
                <input type="checkbox" checked={l.cuadrar}
                  onChange={(e) => set(i, {
                    cuadrar: e.target.checked,
                    cantidad_fisica: e.target.checked ? l.cantidad : '',
                  })} />
                Cuadrar inventario: ¿cuánto hay físicamente ahora?
                {l.cuadrar && (
                  <Input type="number" inputMode="decimal" min="0" value={l.cantidad_fisica}
                    aria-label={`Cantidad física ${l.nombre}`} className="w-24 h-7"
                    onChange={(e) => set(i, { cantidad_fisica: e.target.value })} />
                )}
              </label>
            )}
          />

          <div className="flex items-center justify-between text-body-sm">
            <span className="text-muted-foreground">Total real</span>
            <span className="font-semibold tabular-nums">{cop(total)}</span>
          </div>
          {anticipo > 0 && (
            <div className="flex items-center justify-between text-body-sm">
              <span className="text-muted-foreground">Ya se había pagado</span>
              <span className="tabular-nums">−{cop(anticipo)} → queda {cop(remanente)}</span>
            </div>
          )}

          <div className="space-y-1.5">
            <Label>¿Cómo se paga{anticipo > 0 ? ' el resto' : ''}?</Label>
            <div className="flex gap-2 flex-wrap">
              {[
                ...(anticipo > 0 ? [{ id: 'anticipado', label: 'Ya estaba pagado' }] : []),
                { id: 'contado', label: 'De contado' },
                { id: 'credito', label: 'A crédito (queda debiendo)' },
              ].map(o => (
                <button key={o.id} type="button" onClick={() => setCondicion(o.id)}
                  aria-pressed={condicion === o.id}
                  className={`px-2.5 py-1 rounded-md border text-body-sm ${
                    condicion === o.id ? 'border-primary bg-primary/10 text-primary' : 'border-border'}`}>
                  {o.label}
                </button>
              ))}
            </div>
          </div>

          {(condicion === 'contado' || (condicion === 'anticipado' && remanente > 0)) && (
            <div className="space-y-1.5">
              <label className="flex items-center gap-2 text-body-sm">
                <input type="checkbox" checked={pagoAhora}
                  onChange={(e) => setPagoAhora(e.target.checked)} />
                {condicion === 'contado'
                  ? 'Se paga ahora'
                  : `El resto (${cop(remanente)}) se paga ahora`}
              </label>
              {pagoAhora && (
                <OrigenFondos valor={origen} onCambio={setOrigen} pagos={pagos} onPagos={setPagos}
                  monto={montoAhora} id="recibir-origen" />
              )}
            </div>
          )}
          {(condicion === 'credito' || (condicion === 'anticipado' && remanente > 0 && !pagoAhora)) && (
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <Label htmlFor="pr-factura">Nº factura del proveedor</Label>
                <Input id="pr-factura" value={numeroFactura} aria-label="Número de factura"
                  onChange={(e) => setNumeroFactura(e.target.value)}
                  placeholder={condicion === 'credito' ? `PED-${pedido?.id}` : 'obligatorio'} />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="pr-vence">Vence</Label>
                <Input id="pr-vence" type="date" value={vencimiento} aria-label="Vencimiento"
                  onChange={(e) => setVencimiento(e.target.value)} />
              </div>
            </div>
          )}
          {necesitaDestinoRemanente && (
            <p className="text-caption text-warning">
              La mercancía costó más que lo abonado: indica si el resto se paga ahora o queda a crédito.
            </p>
          )}

          <Button type="submit" disabled={!valido || enviando} className="w-full">
            {enviando ? 'Registrando…' : 'Registrar llegada'}
          </Button>
        </form>
      </DialogContent>
    </Dialog>
  )
}
