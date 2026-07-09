/*
 * ModalAbonoProveedor — abonar a una cuenta por pagar sin salir del cockpit /hoy (reforma F4).
 * Lista las facturas con pendiente (GET /proveedores/facturas) y registra el abono
 * (POST /proveedores/abonos; el backend rechaza abonos que excedan el pendiente).
 */
import { useEffect, useState } from 'react'
import { toast } from 'sonner'
import { api, apiJson } from '@/lib/api'
import { cop } from '@/components/shared.jsx'
import { Button } from '@/components/ui/button.jsx'
import { Input } from '@/components/ui/input.jsx'
import { Label } from '@/components/ui/label.jsx'
import {
  Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle,
} from '@/components/ui/dialog.jsx'

export default function ModalAbonoProveedor({ abierto, onCerrar, onRegistrado }) {
  const [facturas, setFacturas] = useState([])
  const [facturaId, setFacturaId] = useState('')
  const [monto, setMonto] = useState('')
  const [fecha, setFecha] = useState('')   // opcional: abono con fecha distinta a hoy
  const [enviando, setEnviando] = useState(false)

  useEffect(() => {
    if (!abierto) return
    apiJson('/proveedores/facturas')
      .then(d => setFacturas((Array.isArray(d) ? d : []).filter(f => Number(f.pendiente) > 0)))
      .catch(() => setFacturas([]))
  }, [abierto])

  const factura = facturas.find(f => f.id === facturaId)
  const valido = factura && Number(monto) > 0 && Number(monto) <= Number(factura.pendiente)

  async function abonar(e) {
    e?.preventDefault?.()
    if (!valido || enviando) return
    const payload = { factura_id: facturaId, monto: Number(monto) }
    if (fecha) payload.fecha = fecha
    setEnviando(true)
    try {
      const res = await api('/proveedores/abonos', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })
      if (res.ok) {
        const factura = await res.json().catch(() => null)
        toast.success(factura
          ? `Abono registrado · pendiente ${cop(Number(factura.pendiente))} (${factura.estado})`
          : 'Abono registrado')
        setMonto(''); setFacturaId(''); setFecha('')
        onRegistrado?.()
        onCerrar()
      } else if (res.status === 422) {
        toast.error('El abono excede el saldo pendiente')
      } else if (res.status === 404) {
        toast.error('La factura no existe')
      } else {
        const err = await res.json().catch(() => ({}))
        toast.error(typeof err?.detail === 'string' ? err.detail : 'No se pudo registrar el abono')
      }
    } catch { toast.error('Error de conexión') } finally { setEnviando(false) }
  }

  return (
    <Dialog open={abierto} onOpenChange={(o) => { if (!o && !enviando) onCerrar() }}>
      <DialogContent aria-describedby="abono-prov-desc">
        <DialogHeader>
          <DialogTitle>Abonar a proveedor</DialogTitle>
          <DialogDescription id="abono-prov-desc">
            Elige la factura y el monto: el saldo se recalcula al instante.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={abonar} className="space-y-3">
          <div className="space-y-1.5">
            <Label htmlFor="ap-factura">Factura</Label>
            <select id="ap-factura" value={facturaId} onChange={(e) => setFacturaId(e.target.value)}
              className="w-full h-9 rounded-md border border-border bg-surface px-2 text-body-sm">
              <option value="">— elige una factura con saldo —</option>
              {facturas.map(f => (
                <option key={f.id} value={f.id}>
                  {f.proveedor} · {f.id} · debe {cop(Number(f.pendiente))}
                </option>
              ))}
            </select>
            {facturas.length === 0 && (
              <p className="text-caption text-muted-foreground">No hay facturas con saldo pendiente.</p>
            )}
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="ap-monto">Monto del abono</Label>
            <Input id="ap-monto" type="number" inputMode="numeric" min="0" step="any"
              value={monto} onChange={(e) => setMonto(e.target.value)}
              placeholder={factura ? `hasta ${cop(Number(factura.pendiente))}` : '0'} />
            {factura && Number(monto) > Number(factura.pendiente) && (
              <p className="text-caption text-warning">
                El abono no puede superar el pendiente ({cop(Number(factura.pendiente))}).
              </p>
            )}
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="ap-fecha">Fecha (opcional)</Label>
            <Input id="ap-fecha" type="date" value={fecha} onChange={(e) => setFecha(e.target.value)}
              aria-label="Fecha abono" />
          </div>
          <Button type="submit" disabled={!valido || enviando} className="w-full">
            {enviando ? 'Registrando…' : 'Registrar abono'}
          </Button>
        </form>
      </DialogContent>
    </Dialog>
  )
}
