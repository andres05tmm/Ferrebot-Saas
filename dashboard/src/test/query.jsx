/*
 * Helper de test para los tabs migrados a TanStack Query (ADR 0029).
 * `conQuery(ui)` envuelve el árbol en un QueryClientProvider con un cliente fresco por render
 * (sin retry ni caché compartida entre renders) — equivalente al wrapper de lib/queries.test.jsx.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

export function conQuery(ui) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return <QueryClientProvider client={qc}>{ui}</QueryClientProvider>
}
