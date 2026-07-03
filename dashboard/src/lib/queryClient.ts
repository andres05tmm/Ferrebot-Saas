/*
 * queryClient.ts — cliente único de TanStack Query del dashboard (ADR 0029).
 *
 * REGLA DE CONVIVENCIA (sin big-bang): toda pantalla NUEVA, o aquella cuyo data-layer se rehaga,
 * lee datos con `useQuery` y escribe con `useMutation` sobre `lib/api`; las mutaciones invalidan
 * las queries afectadas (ver lib/queries.ts). El hook casero `useFetch` (components/shared.jsx)
 * SIGUE VÁLIDO en los 25+ tabs existentes — no se migran. El tiempo real por SSE (useRealtime)
 * NO cambia: sigue empujando eventos y disparando refetch/invalidación donde ya lo hace.
 */
import { QueryClient } from '@tanstack/react-query'

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // El dashboard tiene SSE: las novedades llegan por el stream, no hace falta refetch agresivo.
      staleTime: 30_000,
      refetchOnWindowFocus: false,
      retry: 1,
    },
  },
})
