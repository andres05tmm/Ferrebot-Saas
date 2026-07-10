/*
 * DrawerCarrito — el carrito/checkout del POS como drawer inferior en móvil.
 * Mismo patrón hecho a mano de components/MobileNav.jsx (overlay + slide-in-from-bottom + safe-area):
 * cero dependencias nuevas. El CONTENIDO llega por children (el panel único del tab: líneas,
 * cliente, checkout) — el estado nunca se duplica entre desktop y móvil.
 */
import { X } from 'lucide-react'

export default function DrawerCarrito({ abierto, onCerrar, children }) {
  if (!abierto) return null
  return (
    <div
      onClick={onCerrar}
      className="fixed inset-0 z-[99] bg-black/55 backdrop-blur-sm flex flex-col justify-end animate-in fade-in duration-200"
    >
      <div
        onClick={e => e.stopPropagation()}
        role="dialog" aria-label="Carrito"
        className="bg-surface border-t border-border rounded-t-xl pb-[calc(72px+env(safe-area-inset-bottom))] pt-3 px-4 animate-in slide-in-from-bottom duration-200 max-h-[85dvh] overflow-y-auto scrollbar-aurora"
      >
        <div className="flex items-center justify-between mb-1">
          <span className="text-sm font-semibold">Carrito</span>
          <button onClick={onCerrar} aria-label="Cerrar carrito"
            className="size-8 grid place-items-center rounded-md hover:bg-surface-2">
            <X className="size-4 text-muted-foreground" />
          </button>
        </div>
        {children}
      </div>
    </div>
  )
}
