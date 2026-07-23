/*
 * TabKds — pantalla de cocina (F4 Pack Restaurante, ADR 0032 D5). Gateada por 'kds'.
 * Columnas por zona (parrilla/bar/cocina) con la cola de comandas activas; cada tarjeta avanza
 * pendiente → en preparación → listo. En vivo por SSE (comanda_nueva/comanda_estado): pantalla
 * siempre encendida, v1 simple — la reconexión la maneja useRealtime.
 */
import { toast } from 'sonner'
import { Flame, CheckCircle2 } from 'lucide-react'
import { api } from '@/lib/api'
import { useFetch } from '@/components/shared.jsx'
import { useRealtimeEvent } from '@/components/RealtimeProvider.jsx'
import { Card } from '@/components/ui/card.jsx'
import { Button } from '@/components/ui/button.jsx'

const SIGUIENTE = { pendiente: 'en_preparacion', en_preparacion: 'listo' }
const ACCION = { pendiente: 'Iniciar', en_preparacion: 'Listo ✓' }

function horaCorta(iso) {
  return new Date(iso).toLocaleTimeString('es-CO', {
    hour: '2-digit', minute: '2-digit', timeZone: 'America/Bogota',
  })
}

export default function TabKds() {
  const kdsQ = useFetch('/kds')
  useRealtimeEvent(['comanda_nueva', 'comanda_estado', 'pedido_confirmado'], () => kdsQ.refetch())
  const zonas = kdsQ.data?.zonas || []
  const comandas = kdsQ.data?.comandas || []
  // Columnas: las zonas configuradas + "cocina" (zona NULL) si tiene comandas.
  const columnas = [...zonas.map(z => ({ id: z.id, nombre: z.nombre }))]
  if (comandas.some(c => c.zona_id == null)) columnas.push({ id: null, nombre: 'cocina' })

  const avanzar = async (c) => {
    const nuevo = SIGUIENTE[c.estado]
    if (!nuevo) return
    try {
      const res = await api(`/kds/comandas/${c.id}/estado`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ estado: nuevo }),
      })
      if (res.ok) kdsQ.refetch()
      else toast.error('No se pudo avanzar la comanda')
    } catch { toast.error('Error de conexión') }
  }

  return (
    <div className="flex flex-col gap-3 h-full min-h-0">
      <h1 className="text-base font-semibold inline-flex items-center gap-2 shrink-0">
        <Flame className="size-4.5 text-primary" /> Cocina
      </h1>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3 flex-1 min-h-0">
        {columnas.map(col => {
          const enZona = comandas.filter(c => c.zona_id === col.id)
          return (
            <div key={col.id ?? 'cocina'} className="flex flex-col min-h-0 rounded-lg border border-border-subtle p-2.5 bg-surface-2/40">
              <div className="shrink-0 pb-2 text-[12px] font-semibold uppercase tracking-wider text-muted-foreground">
                {col.nombre} ({enZona.length})
              </div>
              <div className="flex-1 min-h-0 overflow-y-auto space-y-2">
                {enZona.length === 0 ? (
                  <Card className="p-3 text-center text-[12px] text-muted-foreground">—</Card>
                ) : enZona.map(c => (
                  <Card key={c.id} className={`p-2.5 space-y-1.5 ${c.estado === 'en_preparacion' ? 'ring-1 ring-amber-400' : ''}`}>
                    <div className="flex items-center justify-between text-[12px]">
                      <span className="font-semibold">Pedido #{c.pedido_id}</span>
                      <span className="text-muted-foreground tabular-nums">{horaCorta(c.creada_en)}</span>
                    </div>
                    <ul className="text-[13px] space-y-0.5">
                      {c.items.map((i, k) => (
                        <li key={k}>
                          {Number(i.cantidad)}× {i.nombre}
                          {i.modificadores?.length > 0 && (
                            <span className="block pl-3 text-[11px] italic text-muted-foreground">
                              {i.modificadores.map(m => m.opcion).join(', ')}
                            </span>
                          )}
                        </li>
                      ))}
                    </ul>
                    {SIGUIENTE[c.estado] && (
                      <Button size="sm" className="w-full" onClick={() => avanzar(c)}>
                        {c.estado === 'en_preparacion' && <CheckCircle2 className="size-3.5" />}
                        {ACCION[c.estado]}
                      </Button>
                    )}
                  </Card>
                ))}
              </div>
            </div>
          )
        })}
        {columnas.length === 0 && (
          <Card className="p-4 text-center text-[13px] text-muted-foreground col-span-full">
            Sin comandas activas.
          </Card>
        )}
      </div>
    </div>
  )
}
