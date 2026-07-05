/*
 * FeedActividad — feed en vivo de lo que hace el agente/negocio, alimentado por el SSE compartido.
 *
 * Se suscribe a los eventos que YA publican los repos (ventas, gastos, fiados, facturación, avisos de
 * pago) + el nuevo `transferencia_recibida` de la ingesta Bancolombia. No consulta endpoints: es un
 * espejo cronológico de lo que llega por el stream (útil para ver "el agente registró una venta",
 * "entró una transferencia"). Mantiene los últimos ~30 en memoria; se vacía al recargar.
 */
import { useState } from 'react'
import {
  ShoppingCart, Receipt, HandCoins, FileText, Banknote, Landmark, Activity,
} from 'lucide-react'
import { useRealtimeEvent } from '@/components/RealtimeProvider.jsx'
import { cop } from '@/components/shared.jsx'
import { Card } from '@/components/ui/card.jsx'

const MAX = 30

// Cada tipo → cómo se pinta (icono, tono, y cómo se lee su `data`).
const TIPOS = {
  venta_registrada: { icon: ShoppingCart, tono: 'text-success', texto: () => 'Venta registrada', monto: (d) => d?.total },
  venta_anulada: { icon: ShoppingCart, tono: 'text-destructive', texto: () => 'Venta anulada', monto: (d) => d?.total },
  gasto_registrado: { icon: Receipt, tono: 'text-warning', texto: () => 'Gasto registrado', monto: (d) => d?.monto },
  fiado_registrado: { icon: HandCoins, tono: 'text-info', texto: (d) => `Fiado a ${d?.cliente || 'cliente'}`, monto: (d) => d?.monto },
  abono_registrado: { icon: HandCoins, tono: 'text-success', texto: () => 'Abono a fiado', monto: (d) => d?.monto },
  factura_emitida: { icon: FileText, tono: 'text-info', texto: () => 'Factura emitida', monto: (d) => d?.total },
  pagar_aviso: { icon: Banknote, tono: 'text-warning', texto: () => 'Cuenta por pagar', monto: (d) => d?.monto },
  transferencia_recibida: { icon: Landmark, tono: 'text-success', texto: (d) => `Transferencia${d?.remitente ? ' de ' + d.remitente : ''}`, monto: (d) => d?.monto },
}
const EVENTOS = Object.keys(TIPOS)

function horaCO() {
  return new Date().toLocaleTimeString('es-CO', { hour: '2-digit', minute: '2-digit', timeZone: 'America/Bogota' })
}

export default function FeedActividad() {
  const [items, setItems] = useState([])
  useRealtimeEvent(EVENTOS, (tipo, data) => {
    if (!TIPOS[tipo]) return   // defensivo: solo tipos conocidos (un mock/stream raro no rompe el feed)
    setItems((prev) => [
      { id: `${Date.now()}-${Math.random()}`, tipo, data, hora: horaCO() },
      ...prev,
    ].slice(0, MAX))
  })

  return (
    <Card className="p-3.5 flex flex-col">
      <div className="flex items-center gap-1.5 text-caption font-semibold uppercase tracking-wider text-muted-foreground mb-2.5">
        <Activity className="size-3.5" /> Actividad en vivo
      </div>
      {items.length === 0 ? (
        <p className="py-6 text-center text-body-sm text-muted-foreground">
          Aquí aparece en tiempo real lo que registra el agente y el negocio.
        </p>
      ) : (
        <ul className="divide-y divide-border-subtle -mx-1">
          {items.map((it) => {
            const def = TIPOS[it.tipo]
            const Icon = def.icon
            const monto = def.monto(it.data)
            return (
              <li key={it.id} className="flex items-center gap-2.5 px-1 py-2">
                <Icon className={`size-4 shrink-0 ${def.tono}`} />
                <span className="text-body-sm truncate flex-1">{def.texto(it.data)}</span>
                {monto != null && <span className="text-body-sm tabular shrink-0">{cop(Number(monto))}</span>}
                <span className="text-caption text-muted-foreground shrink-0 tabular">{it.hora}</span>
              </li>
            )
          })}
        </ul>
      )}
    </Card>
  )
}
