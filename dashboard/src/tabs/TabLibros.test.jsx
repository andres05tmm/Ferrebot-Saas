import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter, Routes, Route } from 'react-router-dom'

vi.mock('@/components/RealtimeProvider.jsx', () => ({
  RealtimeProvider: ({ children }) => children,
  useRealtimeEvent: () => {},
}))

import TabLibros from './TabLibros.jsx'
import { USER_KEY } from '@/lib/api'
import { conQuery } from '@/test/query.jsx'

const MAYOR = [
  { concepto: 'ventas', naturaleza: 'ingreso', total: '500000' },
  { concepto: 'compras', naturaleza: 'egreso', total: '200000' },
]
const AUXILIAR = [
  { fecha: '2026-06-10', concepto: 'ventas', naturaleza: 'ingreso', referencia: 'venta #42', valor: '100000' },
]

function jsonResp(data, status = 200) { return { ok: status < 400, status, json: async () => data } }

function instalarFetch() {
  const fetchMock = vi.fn((url) => {
    const u = String(url)
    if (u.includes('/reportes/libro-mayor')) return Promise.resolve(jsonResp(MAYOR))
    if (u.includes('/reportes/libro-auxiliar')) return Promise.resolve(jsonResp(AUXILIAR))
    return Promise.resolve(jsonResp([]))
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function comoAdmin() { localStorage.setItem(USER_KEY, JSON.stringify({ id: 1, rol: 'admin', tenant: 'pr' })) }

// TabLibros usa useOutletContext (refreshKey): lo envolvemos en una ruta con Outlet context.
function renderConOutlet() {
  return render(
    conQuery(
      <MemoryRouter>
        <Routes>
          <Route element={<OutletCtx />}>
            <Route path="*" element={<TabLibros />} />
          </Route>
        </Routes>
      </MemoryRouter>,
    ),
  )
}
import { Outlet } from 'react-router-dom'
function OutletCtx() { return <Outlet context={{ refreshKey: 0 }} /> }

beforeEach(() => { localStorage.clear() })
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('TabLibros', () => {
  it('sin rol admin muestra el aviso', () => {
    instalarFetch()
    renderConOutlet()
    expect(screen.getByText(/solo para administradores/i)).toBeInTheDocument()
  })

  it('admin ve el Libro Mayor y puede cambiar a Auxiliar', async () => {
    comoAdmin(); instalarFetch()
    renderConOutlet()

    expect(await screen.findByText('ventas')).toBeInTheDocument()
    expect(screen.getByText('$500.000')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /Auxiliar/ }))
    expect(await screen.findByText('venta #42')).toBeInTheDocument()
  })
})
