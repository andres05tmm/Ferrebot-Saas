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
