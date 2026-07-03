import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useProductos } from './queries'

const MARTILLO = { id: 1, nombre: 'Martillo', precio_venta: '11900', unidad_medida: 'unidad' }

function jsonResp(data) { return { ok: true, status: 200, json: async () => data } }

// Wrapper con un QueryClient fresco por render (sin retry ni caché entre tests).
function crearWrapper() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return ({ children }) => <QueryClientProvider client={qc}>{children}</QueryClientProvider>
}

beforeEach(() => { localStorage.clear() })
afterEach(() => { vi.restoreAllMocks() })

describe('useProductos (patrón useQuery sobre lib/api)', () => {
  it('con q busca GET /productos y expone los datos', async () => {
    const fetchMock = vi.fn(() => Promise.resolve(jsonResp([MARTILLO])))
    vi.stubGlobal('fetch', fetchMock)

    const { result } = renderHook(() => useProductos('mar'), { wrapper: crearWrapper() })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data).toEqual([MARTILLO])
    expect(fetchMock.mock.calls.some(c => String(c[0]).includes('/productos?q=mar'))).toBe(true)
  })

  it('con q vacío queda deshabilitada (no llama a fetch)', async () => {
    const fetchMock = vi.fn(() => Promise.resolve(jsonResp([])))
    vi.stubGlobal('fetch', fetchMock)

    const { result } = renderHook(() => useProductos('   '), { wrapper: crearWrapper() })

    expect(result.current.fetchStatus).toBe('idle')
    expect(fetchMock).not.toHaveBeenCalled()
  })
})
