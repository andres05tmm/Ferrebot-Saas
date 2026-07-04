import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useProductos, useFacturasRecibidas, useEscanearQR } from './queries'

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

const RECIBIDA = {
  cufe: 'a'.repeat(96), fiscal_id: 1, proveedor_nit: '900123456', total: '119000',
  evento_estado: 'pendiente', cuenta_por_pagar_id: 'a'.repeat(96), fecha_vencimiento: '2026-07-31',
}

describe('useFacturasRecibidas (ADR 0020)', () => {
  it('lista GET /facturas-recibidas', async () => {
    const fetchMock = vi.fn(() => Promise.resolve(jsonResp([RECIBIDA])))
    vi.stubGlobal('fetch', fetchMock)

    const { result } = renderHook(() => useFacturasRecibidas(), { wrapper: crearWrapper() })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data).toEqual([RECIBIDA])
    expect(fetchMock.mock.calls.some(c => String(c[0]).includes('/facturas-recibidas'))).toBe(true)
  })
})

describe('useEscanearQR (ADR 0020)', () => {
  it('POST /facturas-recibidas/escanear devuelve la factura recibida', async () => {
    const fetchMock = vi.fn(() => Promise.resolve(jsonResp(RECIBIDA)))
    vi.stubGlobal('fetch', fetchMock)

    const { result } = renderHook(() => useEscanearQR(), { wrapper: crearWrapper() })
    const data = await result.current.mutateAsync({ qr: 'x', proveedor_nit: '900', total: 119000 })

    expect(data).toEqual(RECIBIDA)
    const call = fetchMock.mock.calls.find(c => String(c[0]).includes('/facturas-recibidas/escanear'))
    expect(call?.[1]?.method).toBe('POST')
  })

  it('un 422 (QR sin CUFE) lanza qr_invalido', async () => {
    const fetchMock = vi.fn(() => Promise.resolve({ ok: false, status: 422, json: async () => ({}) }))
    vi.stubGlobal('fetch', fetchMock)

    const { result } = renderHook(() => useEscanearQR(), { wrapper: crearWrapper() })
    await expect(
      result.current.mutateAsync({ qr: 'basura', proveedor_nit: '900', total: 1 }),
    ).rejects.toThrow('qr_invalido')
  })
})
