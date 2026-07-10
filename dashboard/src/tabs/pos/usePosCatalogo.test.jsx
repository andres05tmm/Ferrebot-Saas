import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, render, screen, waitFor } from '@testing-library/react'

let rtHandler = null
vi.mock('@/components/RealtimeProvider.jsx', () => ({
  useRealtimeEvent: (_tipos, handler) => { rtHandler = handler },
}))

import usePosCatalogo from './usePosCatalogo.js'

function Sonda() {
  const { productos, categorias, cargando, parcial } = usePosCatalogo()
  return (
    <div>
      <span data-testid="n">{productos.length}</span>
      <span data-testid="cats">{categorias.join(',')}</span>
      <span data-testid="estado">{cargando ? 'cargando' : parcial ? 'parcial' : 'completo'}</span>
    </div>
  )
}

function producto(i) {
  return { id: i, nombre: `P${i}`, categoria: i % 2 ? 'Pinturas' : 'Herramientas', precio_venta: '1000' }
}

function jsonResp(data) { return { ok: true, status: 200, json: async () => data } }

function instalarFetch(total) {
  const fetchMock = vi.fn((url) => {
    const u = new URL(String(url), 'http://t')
    const offset = Number(u.searchParams.get('offset') || 0)
    const limite = Number(u.searchParams.get('limite') || 200)
    const pagina = []
    for (let i = offset; i < Math.min(offset + limite, total); i++) pagina.push(producto(i + 1))
    return Promise.resolve(jsonResp(pagina))
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

beforeEach(() => { localStorage.clear(); rtHandler = null; vi.useRealTimers() })
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('usePosCatalogo', () => {
  it('pagina hasta la última página incompleta y deriva las categorías', async () => {
    const fetchMock = instalarFetch(450)   // 200 + 200 + 50 → 3 requests
    render(<Sonda />)
    await waitFor(() => expect(screen.getByTestId('estado').textContent).toBe('completo'))
    expect(screen.getByTestId('n').textContent).toBe('450')
    expect(screen.getByTestId('cats').textContent).toBe('Herramientas,Pinturas')
    const llamadas = fetchMock.mock.calls.map(c => String(c[0])).filter(u => u.includes('/productos'))
    expect(llamadas).toHaveLength(3)
    expect(llamadas[2]).toContain('offset=400')
  })

  it('al alcanzar el tope marca `parcial` (tenant white-label con catálogo enorme)', async () => {
    instalarFetch(5000)
    render(<Sonda />)
    await waitFor(() => expect(screen.getByTestId('estado').textContent).toBe('parcial'))
    expect(Number(screen.getByTestId('n').textContent)).toBe(3000)   // tope de seguridad
  })

  it('recarga (con debounce) ante inventario_actualizado', async () => {
    const fetchMock = instalarFetch(10)
    render(<Sonda />)
    await waitFor(() => expect(screen.getByTestId('estado').textContent).toBe('completo'))
    const antes = fetchMock.mock.calls.length

    vi.useFakeTimers()
    act(() => { rtHandler(); rtHandler() })            // ráfaga: colapsa en UNA recarga
    await act(async () => { vi.advanceTimersByTime(2100) })
    vi.useRealTimers()
    await waitFor(() => expect(fetchMock.mock.calls.length).toBe(antes + 1))
  })
})
