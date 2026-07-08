/*
 * TabGastos — gastos del día (E6, recableado a endpoints SaaS).
 * GET /gastos (?desde&hasta = hoy Colombia) lista; POST /gastos {categoria, monto, concepto}
 * (+Idempotency-Key). Requiere caja abierta (409 → avisar). Live: gasto_registrado / reconnected.
 */
import { useMemo, useState } from 'react'
import { useOutletContext } from 'react-router-dom'
import { toast } from 'sonner'
import { Receipt } from 'lucide-react'
import { api } from '@/lib/api'
import { useFetch, cop, rangoHoyCO } from '@/components/shared.jsx'
import { useRealtimeEvent } from '@/components/RealtimeProvider.jsx'
import { useAuth } from '@/hooks/useAuth.js'
import { Card } from '@/components/ui/card.jsx'
import { Input } from '@/components/ui/input.jsx'
import BandejaRevision from './construccion/BandejaRevision.jsx'

const CATEGORIAS = ['transporte', 'papeleria', 'servicios', 'nomina', 'mantenimiento', 'otros']

function nuevaKey() { return crypto?.randomUUID?.() || `k-${Date.now()}-${Math.random()}` }

export default function TabGastos() {
  const { refreshKey } = useOutletContext() ?? {}
  const { isAdmin } = useAuth()
  const { desde, hasta } = useMemo(() => rangoHoyCO(), [])
  const gastosQ = useFetch(`/gastos?desde=${encodeURIComponent(desde)}&hasta=${encodeURIComponent(hasta)}`, [refreshKey])
  useRealtimeEvent(['gasto_registrado', 'reconnected'], gastosQ.refetch)

  const gastos = Array.isArray(gastosQ.data) ? gastosQ.data : []
  const total = gastos.reduce((a, g) => a + Number(g.monto), 0)

  return (
    <div className="space-y-3 max-w-2xl">
      {/* Cola de recibos que el bot importó con baja confianza (F5). Solo admin y silenciosa si vacía. */}
      {isAdmin() && <BandejaRevision refreshKey={refreshKey} />}

      <GastoForm onDone={gastosQ.refetch} />

      <Card className="p-3.5">
        <div className="flex items-center justify-between mb-2.5">
          <h2 className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground inline-flex items-center gap-1.5">
            <Receipt className="size-3.5" /> Gastos de hoy
          </h2>
          {gastos.length > 0 && <span className="text-[12px] tabular font-semibold">{cop(total)}</span>}
        </div>
        {gastosQ.loading ? (
          <p className="py-8 text-center text-sm text-muted-foreground">Cargando…</p>
        ) : gastos.length === 0 ? (
          <p className="py-8 text-center text-sm text-muted-foreground">Sin gastos registrados hoy.</p>
        ) : (
          <ul className="divide-y divide-border-subtle">
            {gastos.map(g => (
              <li key={g.id} className="py-2 flex items-center gap-2 text-[13px]">
                <span className="capitalize w-28 shrink-0 text-muted-foreground">{g.categoria}</span>
                <span className="flex-1 truncate">{g.concepto || '—'}</span>
                <span className="tabular font-semibold shrink-0">{cop(Number(g.monto))}</span>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  )
}

function GastoForm({ onDone }) {
  const [categoria, setCategoria] = useState(CATEGORIAS[0])
  const [monto, setMonto] = useState('')
  const [concepto, setConcepto] = useState('')
  const [enviando, setEnviando] = useState(false)

  async function registrar() {
    const n = Number(monto)
    if (!n || n <= 0) { toast.error('Indica el monto'); return }
    setEnviando(true)
    try {
      const res = await api('/gastos', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Idempotency-Key': nuevaKey() },
        body: JSON.stringify({ categoria, monto: n, concepto: concepto.trim() || null }),
      })
      if (res.ok) {
        toast.success('Gasto registrado'); setMonto(''); setConcepto(''); onDone()
      } else if (res.status === 409) {
        toast.error('Abre la caja antes de registrar gastos')
      } else {
        toast.error('No se pudo registrar el gasto')
      }
    } catch { toast.error('Error de conexión') } finally { setEnviando(false) }
  }

  return (
    <Card className="p-3.5">
      <h2 className="text-sm font-semibold mb-2">Registrar gasto</h2>
      <div className="flex flex-wrap items-center gap-2">
        <select value={categoria} onChange={(e) => setCategoria(e.target.value)} aria-label="Categoría"
          className="h-9 px-2 rounded-md border border-border bg-surface text-sm capitalize">
          {CATEGORIAS.map(c => <option key={c} value={c}>{c}</option>)}
        </select>
        <Input type="number" min="0" step="any" value={monto} onChange={(e) => setMonto(e.target.value)}
          placeholder="Monto" aria-label="Monto" className="w-32 h-9" />
        <Input value={concepto} onChange={(e) => setConcepto(e.target.value)}
          placeholder="Concepto (opcional)" aria-label="Concepto" className="flex-1 min-w-[120px] h-9" />
        <button onClick={registrar} disabled={enviando}
          className="h-9 px-4 rounded-md bg-primary text-primary-foreground font-medium hover:bg-primary-hover disabled:opacity-60">
          {enviando ? 'Guardando…' : 'Registrar'}
        </button>
      </div>
    </Card>
  )
}
