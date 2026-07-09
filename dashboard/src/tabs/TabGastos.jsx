/*
 * TabGastos — gastos del día (E6, recableado a endpoints SaaS).
 * GET /gastos (?desde&hasta = hoy Colombia) lista; el registro va por el modal COMPARTIDO
 * `ModalGastoRapido` (F4: el mismo del cockpit /hoy — un solo lugar con el POST, la key idempotente
 * y el 409 de caja cerrada). Live: gasto_registrado / reconnected.
 */
import { useMemo, useState } from 'react'
import { useOutletContext } from 'react-router-dom'
import { Plus, Receipt } from 'lucide-react'
import { useFetch, cop, rangoHoyCO } from '@/components/shared.jsx'
import { useRealtimeEvent } from '@/components/RealtimeProvider.jsx'
import { useAuth } from '@/hooks/useAuth.js'
import { Card } from '@/components/ui/card.jsx'
import { Button } from '@/components/ui/button.jsx'
import ModalGastoRapido from '@/components/ModalGastoRapido.jsx'
import BandejaRevision from './construccion/BandejaRevision.jsx'

export default function TabGastos() {
  const { refreshKey } = useOutletContext() ?? {}
  const { isAdmin } = useAuth()
  const [modalAbierto, setModalAbierto] = useState(false)
  const { desde, hasta } = useMemo(() => rangoHoyCO(), [])
  const gastosQ = useFetch(`/gastos?desde=${encodeURIComponent(desde)}&hasta=${encodeURIComponent(hasta)}`, [refreshKey])
  useRealtimeEvent(['gasto_registrado', 'reconnected'], gastosQ.refetch)

  const gastos = Array.isArray(gastosQ.data) ? gastosQ.data : []
  const total = gastos.reduce((a, g) => a + Number(g.monto), 0)

  return (
    <div className="space-y-3 max-w-2xl">
      {/* Cola de recibos que el bot importó con baja confianza (F5). Solo admin y silenciosa si vacía. */}
      {isAdmin() && <BandejaRevision refreshKey={refreshKey} />}

      <Card className="p-3.5">
        <div className="flex items-center justify-between mb-2.5">
          <h2 className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground inline-flex items-center gap-1.5">
            <Receipt className="size-3.5" /> Gastos de hoy
          </h2>
          <div className="flex items-center gap-2.5">
            {gastos.length > 0 && <span className="text-[12px] tabular font-semibold">{cop(total)}</span>}
            <Button size="sm" onClick={() => setModalAbierto(true)} className="h-8 gap-1">
              <Plus className="size-3.5" /> Nuevo gasto
            </Button>
          </div>
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

      <ModalGastoRapido abierto={modalAbierto} onCerrar={() => setModalAbierto(false)}
        onRegistrado={gastosQ.refetch} />
    </div>
  )
}
