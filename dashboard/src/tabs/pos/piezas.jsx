/*
 * Piezas compartidas del POS (reforma grilla híbrida): átomos de UI + utilidades de localStorage.
 * Sin estado propio — todo presentación o funciones puras; los dueños del estado son el tab y sus hooks.
 */

// Métodos de pago del POS. 'mixto' (F5) abre el cobro dividido; Alt+1..5 indexa esta lista.
export const METODOS = ['efectivo', 'transferencia', 'datafono', 'fiado', 'mixto']
// Segundo método de un cobro MIXTO (F5): dinero que entra YA — fiado queda fuera (v1).
export const METODOS_MIXTO_RESTO = ['transferencia', 'datafono']

export function leerLS(key, fallback) {
  try {
    const d = JSON.parse(localStorage.getItem(key))
    return Array.isArray(d) ? d : fallback
  } catch { return fallback }
}

export function guardarLS(key, valor) {
  try { localStorage.setItem(key, JSON.stringify(valor)) } catch { /* almacenamiento lleno/privado */ }
}

export function nuevaKey() {
  return (crypto?.randomUUID?.() || `k-${Date.now()}-${Math.random()}`)
}

export function Seg({ activo, onClick, children, ...props }) {
  return (
    <button type="button" onClick={onClick} aria-pressed={activo} {...props}
      className={`flex-1 h-7 px-2 rounded-md border text-caption tabular transition-colors ${
        activo ? 'border-primary bg-primary/10 text-primary font-medium'
          : 'border-border bg-surface text-muted-foreground hover:bg-surface-2'}`}>
      {children}
    </button>
  )
}

export function Kbd({ children }) {
  return (
    <kbd className="inline-flex items-center rounded border border-border bg-surface-2 px-1 text-[10px] font-medium text-muted-foreground">
      {children}
    </kbd>
  )
}

export function AtajosHint() {
  return (
    <p className="flex flex-wrap items-center gap-x-3 gap-y-1 px-1 text-caption text-muted-foreground">
      <span><Kbd>F2</Kbd> o <Kbd>/</Kbd> buscar</span>
      <span><Kbd>↑</Kbd><Kbd>↓</Kbd> elegir</span>
      <span><Kbd>Enter</Kbd> agrega</span>
      <span><Kbd>F4</Kbd> carrito</span>
      <span><Kbd>F9</Kbd> cobrar</span>
      <span><Kbd>Alt</Kbd>+<Kbd>1</Kbd>–<Kbd>5</Kbd> pago</span>
      <span>o escanea un código</span>
    </p>
  )
}
