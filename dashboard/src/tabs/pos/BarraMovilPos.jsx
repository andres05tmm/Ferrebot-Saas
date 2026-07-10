/*
 * BarraMovilPos — barra fija inferior del POS en móvil: total en vivo + abrir el carrito.
 * Vive ENCIMA del bottom nav de la app (72px + safe-area): el cajero vende desde el celular
 * sin perder de vista cuánto va. Solo se monta en modo móvil (useIsMobile en el tab).
 */
import { ShoppingCart } from 'lucide-react'
import { cop } from '@/components/shared.jsx'

export default function BarraMovilPos({ total, numItems, onAbrir }) {
  return (
    <div
      className="fixed inset-x-0 z-[98] bg-surface-sidebar/95 backdrop-blur-md border-t border-border px-3 py-2 flex items-center gap-3"
      style={{ bottom: 'calc(72px + env(safe-area-inset-bottom))' }}
      role="region" aria-label="Resumen de la venta"
    >
      <div className="flex-1 min-w-0">
        <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
          {numItems} {numItems === 1 ? 'ítem' : 'ítems'}
        </div>
        <div className="text-lg font-semibold tabular leading-tight">{cop(total)}</div>
      </div>
      <button onClick={onAbrir} disabled={numItems === 0}
        aria-label="Abrir carrito"
        className="h-10 px-4 inline-flex items-center gap-2 rounded-md bg-primary text-primary-foreground font-medium disabled:opacity-50">
        <ShoppingCart className="size-4" /> Cobrar
      </button>
    </div>
  )
}
