/*
 * TabCaja — caja del día (E6, recableado a endpoints SaaS).
 * GET /caja/actual (404 = sin caja → estado cerrada). POST /caja/apertura {saldo_inicial},
 * POST /caja/movimiento {tipo, monto, concepto} (+Idempotency-Key), POST /caja/cierre {saldo_contado}
 * (muestra la diferencia que devuelve). Live: caja_abierta/cerrada/movimiento/reconnected.
 * Diferido: vender (va en Ventas rápidas) y el cuadre rico (el backend no expone sus componentes).
 */
import { useCallback, useEffect, useState } from 'react'
import { useOutletContext } from 'react-router-dom'
import { toast } from 'sonner'
import { Wallet } from 'lucide-react'
import { api } from '@/lib/api.js'
import { cop } from '@/components/shared.jsx'
import { useRealtimeEvent } from '@/components/RealtimeProvider.jsx'
import { Card } from '@/components/ui/card.jsx'
import { Input } from '@/components/ui/input.jsx'

function nuevaKey() { return crypto?.randomUUID?.() || `k-${Date.now()}-${Math.random()}` }

export default function TabCaja() {
  const { refreshKey } = useOutletContext() ?? {}
  const [caja, setCaja] = useState(null)
  const [cargando, setCargando] = useState(true)

  const cargar = useCallback(async () => {
    setCargando(true)
    try {
      const res = await api('/caja/actual')
      setCaja(res.ok ? await res.json() : null)   // 404 → sin caja (cerrada)
    } catch {
      setCaja(null)
    } finally {
      setCargando(false)
    }
  }, [])

  useEffect(() => { cargar() }, [cargar, refreshKey])
  useRealtimeEvent(['caja_abierta', 'caja_cerrada', 'caja_movimiento', 'reconnected'], cargar)

  if (cargando && caja === null) {
    return <Card className="p-8 text-center text-sm text-muted-foreground">Cargando…</Card>
  }

  const abierta = caja?.estado === 'abierta'
  return (
    <div className="space-y-3 max-w-2xl">
      {abierta ? (
        <>
          <CajaResumen caja={caja} />
          <MovimientoForm onDone={cargar} />
          <CierreForm onDone={cargar} />
        </>
      ) : (
        <AperturaForm onDone={cargar} />
      )}
    </div>
  )
}

function CajaResumen({ caja }) {
  return (
    <Card className="p-3.5">
      <div className="flex items-center gap-2 mb-2">
        <span className="grid place-items-center rounded-md size-7 bg-success/15 text-success">
          <Wallet className="size-4" />
        </span>
        <span className="text-sm font-semibold">Caja abierta</span>
      </div>
      <dl className="grid grid-cols-2 gap-2 text-[13px]">
        <div><dt className="text-muted-foreground text-[11px] uppercase tracking-wider">Base inicial</dt>
          <dd className="tabular font-semibold">{cop(Number(caja.saldo_inicial))}</dd></div>
        <div><dt className="text-muted-foreground text-[11px] uppercase tracking-wider">Apertura</dt>
          <dd className="tabular">{new Date(caja.fecha_apertura).toLocaleTimeString('es-CO', { hour: '2-digit', minute: '2-digit', timeZone: 'America/Bogota' })}</dd></div>
      </dl>
    </Card>
  )
}

function AperturaForm({ onDone }) {
  const [saldo, setSaldo] = useState('')
  const [enviando, setEnviando] = useState(false)

  async function abrir() {
    const n = Number(saldo)
    if (Number.isNaN(n) || n < 0) { toast.error('Indica el saldo inicial'); return }
    setEnviando(true)
    try {
      const res = await api('/caja/apertura', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ saldo_inicial: n }),
      })
      if (res.ok) { toast.success('Caja abierta'); onDone() }
      else toast.error('No se pudo abrir la caja')
    } catch { toast.error('Error de conexión') } finally { setEnviando(false) }
  }

  return (
    <Card className="p-3.5">
      <h2 className="text-sm font-semibold mb-2">Abrir caja</h2>
      <p className="text-[12px] text-muted-foreground mb-3">No hay caja abierta. Registra el saldo inicial para empezar el día.</p>
      <div className="flex items-center gap-2">
        <Input type="number" min="0" step="any" value={saldo} onChange={(e) => setSaldo(e.target.value)}
          placeholder="Saldo inicial" aria-label="Saldo inicial" className="flex-1 h-9" />
        <button onClick={abrir} disabled={enviando}
          className="h-9 px-4 rounded-md bg-primary text-primary-foreground font-medium hover:bg-primary-hover disabled:opacity-60">
          {enviando ? 'Abriendo…' : 'Abrir caja'}
        </button>
      </div>
    </Card>
  )
}

function MovimientoForm({ onDone }) {
  const [tipo, setTipo] = useState('ingreso')
  const [monto, setMonto] = useState('')
  const [concepto, setConcepto] = useState('')
  const [enviando, setEnviando] = useState(false)

  async function registrar() {
    const n = Number(monto)
    if (!n || n <= 0) { toast.error('Indica el monto'); return }
    setEnviando(true)
    try {
      const res = await api('/caja/movimiento', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Idempotency-Key': nuevaKey() },
        body: JSON.stringify({ tipo, monto: n, concepto: concepto.trim() || null }),
      })
      if (res.ok) { toast.success('Movimiento registrado'); setMonto(''); setConcepto(''); onDone() }
      else toast.error('No se pudo registrar el movimiento')
    } catch { toast.error('Error de conexión') } finally { setEnviando(false) }
  }

  return (
    <Card className="p-3.5">
      <h2 className="text-sm font-semibold mb-2">Movimiento de caja</h2>
      <div className="flex flex-wrap items-center gap-2">
        <select value={tipo} onChange={(e) => setTipo(e.target.value)} aria-label="Tipo de movimiento"
          className="h-9 px-2 rounded-md border border-border bg-surface text-sm">
          <option value="ingreso">Ingreso</option>
          <option value="egreso">Egreso</option>
        </select>
        <Input type="number" min="0" step="any" value={monto} onChange={(e) => setMonto(e.target.value)}
          placeholder="Monto" aria-label="Monto" className="w-32 h-9" />
        <Input value={concepto} onChange={(e) => setConcepto(e.target.value)}
          placeholder="Concepto (opcional)" aria-label="Concepto" className="flex-1 min-w-[120px] h-9" />
        <button onClick={registrar} disabled={enviando}
          className="h-9 px-4 rounded-md border border-border bg-surface text-sm hover:bg-surface-2 disabled:opacity-60">
          Registrar
        </button>
      </div>
    </Card>
  )
}

function CierreForm({ onDone }) {
  const [contado, setContado] = useState('')
  const [enviando, setEnviando] = useState(false)

  async function cerrar() {
    const n = Number(contado)
    if (Number.isNaN(n) || n < 0) { toast.error('Indica el saldo contado'); return }
    setEnviando(true)
    try {
      const res = await api('/caja/cierre', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ saldo_contado: n }),
      })
      if (res.ok) {
        const data = await res.json()
        const dif = Number(data.diferencia ?? 0)
        toast.success(`Caja cerrada · diferencia ${cop(dif)}`)
        onDone()
      } else {
        toast.error('No se pudo cerrar la caja')
      }
    } catch { toast.error('Error de conexión') } finally { setEnviando(false) }
  }

  return (
    <Card className="p-3.5">
      <h2 className="text-sm font-semibold mb-2">Cerrar caja</h2>
      <div className="flex items-center gap-2">
        <Input type="number" min="0" step="any" value={contado} onChange={(e) => setContado(e.target.value)}
          placeholder="Saldo contado" aria-label="Saldo contado" className="flex-1 h-9" />
        <button onClick={cerrar} disabled={enviando}
          className="h-9 px-4 rounded-md bg-primary text-primary-foreground font-medium hover:bg-primary-hover disabled:opacity-60">
          {enviando ? 'Cerrando…' : 'Cerrar caja'}
        </button>
      </div>
    </Card>
  )
}
