/*
 * ModalAbrirCaja — guard de apertura de caja del POS (modo `caja_obligatoria`).
 *
 * Aparece cuando alguien intenta cobrar sin caja abierta: pregunta "¿Cuánto dinero hay en caja?",
 * abre la caja con ese monto (POST /caja/apertura) y deja que el POS registre la venta pendiente
 * SIN que el cajero repita nada (el carrito y la Idempotency-Key ya generada quedan intactos).
 * Cerrar el modal solo cancela el cobro (la venta no se pierde: el carrito sigue ahí).
 */
import { useState } from 'react'
import { toast } from 'sonner'
import { Lock } from 'lucide-react'
import { api } from '@/lib/api'
import { Button } from '@/components/ui/button.jsx'
import { Input } from '@/components/ui/input.jsx'
import { Label } from '@/components/ui/label.jsx'
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '@/components/ui/dialog.jsx'

export default function ModalAbrirCaja({ abierto, onCancelar, onCajaAbierta }) {
  const [monto, setMonto] = useState('')
  const [enviando, setEnviando] = useState(false)

  const montoValido = monto !== '' && Number(monto) >= 0

  async function abrirCaja(e) {
    e?.preventDefault?.()
    if (!montoValido || enviando) return
    setEnviando(true)
    try {
      const res = await api('/caja/apertura', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ saldo_inicial: Number(monto) }),
      })
      if (res.ok) {
        setMonto('')
        await onCajaAbierta()   // el POS registra la venta pendiente con su misma key
      } else {
        const err = await res.json().catch(() => ({}))
        toast.error(typeof err?.detail === 'string' ? err.detail : 'No se pudo abrir la caja')
      }
    } catch {
      toast.error('Error de conexión')
    } finally {
      setEnviando(false)
    }
  }

  return (
    <Dialog open={abierto} onOpenChange={(o) => { if (!o && !enviando) onCancelar() }}>
      <DialogContent aria-describedby="abrir-caja-desc">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Lock className="size-5 text-primary" aria-hidden="true" />
            ¿Cuánto dinero hay en caja?
          </DialogTitle>
          <DialogDescription id="abrir-caja-desc">
            Es la primera venta del día: cuenta el efectivo que hay en la caja y ábrela con ese
            monto. La venta que digitaste se registrará enseguida, sin repetir nada.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={abrirCaja} className="space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor="monto-apertura">Dinero en caja</Label>
            <Input
              id="monto-apertura" type="number" inputMode="numeric" min="0" step="any"
              value={monto} onChange={(e) => setMonto(e.target.value)}
              placeholder="0" autoFocus
            />
          </div>
          <DialogFooter>
            <Button type="submit" disabled={!montoValido || enviando} className="w-full sm:w-auto">
              {enviando ? 'Abriendo caja…' : 'Abrir caja y registrar la venta'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
