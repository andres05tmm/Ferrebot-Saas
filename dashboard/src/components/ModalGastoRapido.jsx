/*
 * ModalGastoRapido — registrar un gasto sin salir del cockpit /hoy (reforma F4).
 * POST /gastos (idempotente); exige caja abierta (409 → mensaje claro con el porqué).
 */
import { useMemo, useState } from 'react'
import { toast } from 'sonner'
import { api } from '@/lib/api'
import { Button } from '@/components/ui/button.jsx'
import { Input } from '@/components/ui/input.jsx'
import { Label } from '@/components/ui/label.jsx'
import {
  Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle,
} from '@/components/ui/dialog.jsx'

const CATEGORIAS = [
  ['transporte', 'Transporte'], ['papeleria', 'Papelería'], ['servicios', 'Servicios'],
  ['nomina', 'Nómina'], ['mantenimiento', 'Mantenimiento'], ['otros', 'Otros'],
]

export default function ModalGastoRapido({ abierto, onCerrar, onRegistrado }) {
  const [categoria, setCategoria] = useState('otros')
  const [monto, setMonto] = useState('')
  const [concepto, setConcepto] = useState('')
  const [enviando, setEnviando] = useState(false)

  const valido = Number(monto) > 0
  // Key estable mientras el payload no cambie: un reintento tras timeout (el server SÍ commiteó) es
  // replay, no duplicado. Editar cualquier campo renueva la key (payload nuevo = operación nueva).
  const idemKey = useMemo(() => crypto.randomUUID(), [categoria, monto, concepto])

  async function registrar(e) {
    e?.preventDefault?.()
    if (!valido || enviando) return
    setEnviando(true)
    try {
      const res = await api('/gastos', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Idempotency-Key': idemKey },
        body: JSON.stringify({ categoria, monto: Number(monto), concepto: concepto.trim() || null }),
      })
      if (res.ok) {
        toast.success('Gasto registrado')
        setMonto(''); setConcepto('')
        onRegistrado?.()
        onCerrar()
      } else if (res.status === 409) {
        toast.error('No hay caja abierta: abre la caja antes de registrar gastos.')
      } else {
        toast.error('No se pudo registrar el gasto')
      }
    } catch { toast.error('Error de conexión') } finally { setEnviando(false) }
  }

  return (
    <Dialog open={abierto} onOpenChange={(o) => { if (!o && !enviando) onCerrar() }}>
      <DialogContent aria-describedby="gasto-rapido-desc">
        <DialogHeader>
          <DialogTitle>Registrar gasto</DialogTitle>
          <DialogDescription id="gasto-rapido-desc">
            Sale de la caja abierta y queda en la contabilidad del día.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={registrar} className="space-y-3">
          <div className="space-y-1.5">
            <Label htmlFor="gr-monto">Monto</Label>
            <Input id="gr-monto" type="number" inputMode="numeric" min="0" step="any" autoFocus
              value={monto} onChange={(e) => setMonto(e.target.value)} placeholder="0" />
          </div>
          <div className="space-y-1.5">
            <Label>Categoría</Label>
            <div className="flex gap-1.5 flex-wrap">
              {CATEGORIAS.map(([v, l]) => (
                <button key={v} type="button" onClick={() => setCategoria(v)} aria-pressed={categoria === v}
                  className={`px-2 py-0.5 rounded-md border text-caption ${
                    categoria === v ? 'border-primary bg-primary/10 text-primary' : 'border-border'}`}>
                  {l}
                </button>
              ))}
            </div>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="gr-concepto">Concepto (opcional)</Label>
            <Input id="gr-concepto" value={concepto} onChange={(e) => setConcepto(e.target.value)}
              placeholder="Almuerzo, transporte de mercancía…" />
          </div>
          <Button type="submit" disabled={!valido || enviando} className="w-full">
            {enviando ? 'Registrando…' : 'Registrar gasto'}
          </Button>
        </form>
      </DialogContent>
    </Dialog>
  )
}
