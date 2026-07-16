/*
 * PillCarrito — el carrito colapsado como pill flotante (desktop): conteo + total en vivo; un toque
 * abre el panel lateral (Sheet). Es el rol de BarraMovilPos pero flotante: la grilla usa todo el
 * ancho SIEMPRE. Superficie neutra; el acento rojo queda solo en el segmento Cobrar (acción primaria).
 */
import { ShoppingCart } from 'lucide-react'
import { cop } from '@/components/shared.jsx'

export default function PillCarrito({ total, numItems, onAbrir }) {
  const vacio = numItems === 0
  return (
    <button onClick={onAbrir} aria-label="Abrir carrito"
      className="fixed bottom-4 right-4 z-40 flex items-center gap-3 rounded-full border border-border bg-surface pl-4 pr-1.5 py-1.5 shadow-lg transition-colors hover:border-primary/40">
      <span className="relative shrink-0">
        <ShoppingCart className={`size-5 ${vacio ? 'text-muted-foreground/60' : 'text-foreground'}`} aria-hidden="true" />
        {!vacio && (
          <span className="absolute -top-2 -right-2.5 min-w-[18px] h-[18px] px-1 grid place-items-center rounded-full bg-primary text-primary-foreground text-[10px] font-bold tabular"
            aria-label={`${numItems} en el carrito`}>
            {numItems}
          </span>
        )}
      </span>
      {vacio ? (
        <span className="text-body-sm text-muted-foreground pr-2.5 py-1.5">Carrito</span>
      ) : (
        <>
          <span className="text-body font-semibold tabular">{cop(total)}</span>
          <span className="h-9 px-4 inline-flex items-center gap-1.5 rounded-full bg-primary text-primary-foreground text-body-sm font-medium">
            Cobrar <kbd className="opacity-70 text-[10px]" aria-hidden="true">F4</kbd>
          </span>
        </>
      )}
    </button>
  )
}
