/*
 * ModalAbonoFiado — registrar un abono a un fiado desde el dashboard (F2.3).
 *
 * Antes los abonos solo entraban por el bot de Telegram y la cartera del dashboard era de solo
 * lectura (hallazgo F1). Este modal cierra ese hueco en los dos frentes que comparten el ledger:
 * los deudores de cobranza (fiados del cliente vía GET /fiados?cliente_id=) y las obras de la
 * cartera de alquiler (fiados ya presentes en los cargos).
 *
 *   POST /fiados/{id}/abono {monto} + Idempotency-Key → 201 (200 = replay). 422 SobreAbono → detail.
 *
 * El abono BAJA EL SALDO DEL FIADO y NO mueve caja (paridad con el bot: el pago puede entrar por
 * transferencia). Si algún día se quiere "abono en efectivo → caja", es una feature aparte.
 */
import { useMemo, useState } from 'react'
import { toast } from 'sonner'
import { api } from '@/lib/api'
import { cop, useFetch } from '@/components/shared.jsx'
import { Button } from '@/components/ui/button.jsx'
import { Input } from '@/components/ui/input.jsx'
import { Label } from '@/components/ui/label.jsx'
import {
  Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle,
} from '@/components/ui/dialog.jsx'

const arr = (x) => (Array.isArray(x) ? x : [])
const n = (v) => { const x = Number(v); return Number.isFinite(x) ? x : 0 }

function fechaCorta(iso) {
  if (!iso) return ''
  try {
    return new Date(iso).toLocaleDateString('es-CO', { timeZone: 'America/Bogota', day: '2-digit', month: 'short' })
  } catch { return '' }
}

async function detalleError(res) {
  try { const b = await res.json(); return typeof b?.detail === 'string' ? b.detail : null } catch { return null }
}

/**
 * `fiados`: lista [{id, saldo, label?}] ya conocida (cartera de alquiler: los cargos de la obra), O
 * `clienteId`: el modal pide GET /fiados?cliente_id= (deudores de cobranza). Uno de los dos.
 */
export default function ModalAbonoFiado({ abierto, onCerrar, titulo, fiados, clienteId, onExito }) {
  const fiadosQ = useFetch(abierto && clienteId != null ? `/fiados?cliente_id=${clienteId}` : null)
  const lista = clienteId != null
    ? arr(fiadosQ.data).map((f) => ({ id: f.id, saldo: n(f.saldo), label: `Fiado #${f.id} · ${fechaCorta(f.creado_en)}` }))
    : arr(fiados)

  return (
    <Dialog open={abierto} onOpenChange={(o) => { if (!o) onCerrar() }}>
      <DialogContent aria-describedby="abono-fiado-desc">
        <DialogHeader>
          <DialogTitle>{titulo || 'Registrar abono'}</DialogTitle>
          <DialogDescription id="abono-fiado-desc">
            Baja el saldo del fiado. No mueve caja: el pago puede entrar por transferencia.
          </DialogDescription>
        </DialogHeader>
        {clienteId != null && fiadosQ.loading ? (
          <p className="py-4 text-center text-sm text-muted-foreground">Cargando fiados…</p>
        ) : lista.length === 0 ? (
          <p className="py-4 text-center text-sm text-muted-foreground">Este cliente no tiene fiados con saldo.</p>
        ) : (
          <FormAbono key={lista.map((f) => f.id).join('-')} fiados={lista} onExito={onExito} />
        )}
      </DialogContent>
    </Dialog>
  )
}

function FormAbono({ fiados, onExito }) {
  const [fiadoId, setFiadoId] = useState(String(fiados[0].id))
  const [monto, setMonto] = useState('')
  const [enviando, setEnviando] = useState(false)

  const elegido = fiados.find((f) => String(f.id) === fiadoId) || fiados[0]
  const saldo = n(elegido.saldo)
  const valido = n(monto) > 0 && n(monto) <= saldo

  // Key estable mientras el payload no cambie: reintentar tras timeout es replay, no un abono doble.
  const idemKey = useMemo(() => crypto.randomUUID(), [fiadoId, monto])

  async function abonar(e) {
    e?.preventDefault?.()
    if (!valido || enviando) return
    setEnviando(true)
    try {
      const res = await api(`/fiados/${elegido.id}/abono`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Idempotency-Key': idemKey },
        body: JSON.stringify({ monto: String(n(monto)) }),
      })
      if (res.status === 200) toast.message('Ese abono ya estaba registrado')
      else if (res.ok) toast.success(`Abono de ${cop(n(monto))} registrado`)
      else { toast.error((await detalleError(res)) || 'No se pudo registrar el abono'); return }
      onExito?.()
    } catch { toast.error('Error de conexión') } finally { setEnviando(false) }
  }

  return (
    <form onSubmit={abonar} className="space-y-3">
      {fiados.length > 1 ? (
        <div className="space-y-1.5">
          <Label htmlFor="af-fiado">Fiado</Label>
          <select id="af-fiado" value={fiadoId} onChange={(e) => setFiadoId(e.target.value)}
            className="h-10 w-full rounded-md border border-input bg-surface px-2 text-sm text-foreground sm:h-9">
            {fiados.map((f) => (
              <option key={f.id} value={f.id}>{f.label || `Fiado #${f.id}`} — saldo {cop(n(f.saldo))}</option>
            ))}
          </select>
        </div>
      ) : (
        <p className="text-sm text-muted-foreground">{elegido.label || `Fiado #${elegido.id}`}</p>
      )}
      <div className="space-y-1.5">
        <Label htmlFor="af-monto">Monto</Label>
        <Input id="af-monto" type="number" inputMode="numeric" min="0" step="any" autoFocus
          value={monto} onChange={(e) => setMonto(e.target.value)} placeholder="0" />
        <p className="text-caption text-muted-foreground">Saldo del fiado: <b className="tabular">{cop(saldo)}</b> (tope del abono).</p>
      </div>
      <Button type="submit" disabled={!valido || enviando} className="w-full">
        {enviando ? 'Registrando…' : 'Registrar abono'}
      </Button>
    </form>
  )
}
