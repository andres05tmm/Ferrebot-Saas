import '@testing-library/jest-dom'

// jsdom no trae ResizeObserver; recharts (ResponsiveContainer) lo necesita al montar. Stub no-op:
// el contenedor queda en 0×0 (no dibuja en tests), pero no rompe el render de las pestañas con gráfica.
if (typeof globalThis.ResizeObserver === 'undefined') {
  globalThis.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
}

// jsdom no implementa matchMedia; `useIsMobile()` (components/shared.jsx) y el theming lo consultan al
// montar. Stub: por defecto NO-match (viewport de escritorio) con la API de listeners completa. Un test
// que necesite simular móvil puede sobrescribir `window.matchMedia` con una impl que devuelva matches:true.
if (typeof window !== 'undefined' && typeof window.matchMedia !== 'function') {
  window.matchMedia = (query) => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener() {},
    removeEventListener() {},
    addListener() {},      // API vieja (deprecada) que algunas libs aún usan
    removeListener() {},
    dispatchEvent() { return false },
  })
}
