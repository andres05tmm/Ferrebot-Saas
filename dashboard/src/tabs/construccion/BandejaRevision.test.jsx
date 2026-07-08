import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'

vi.mock('@/components/RealtimeProvider.jsx', () => ({
  RealtimeProvider: ({ children }) => children,
  useRealtimeEvent: () => {},
}))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn(), message: vi.fn() } }))

import BandejaRevision from './BandejaRevision.jsx'

function jsonResp(data, status = 200) { return { ok: status < 400, status, json: async () => data } }

const REVISION = [
  {
    id: 1, categoria: 'mantenimiento', monto: '15000.00', concepto: null,
    caja_id: 1, usuario_id: null, creado_en: '2026-06-05T14:00:00+00:00',
    obra_id: 5, maquina_id: null, categoria_gasto: 'REPUESTOS',
    comprobante_url: 'https://cdn/recibo1.jpg', origen_registro: 'TELEGRAM_BOT', requiere_revision: true,
  },
  {
    id: 2, categoria: 'otros', monto: '8000.00', concepto: null,
    caja_id: 1, usuario_id: null, creado_en: '2026-06-05T15:00:00+00:00',
    obra_id: null, maquina_id: 9, categoria_gasto: null,
    comprobante_url: null, origen_registro: 'TELEGRAM_BOT', requiere_revision: true,
  },
]

function instalarFetch(lista = REVISION, { revisionStatus = 200 } = {}) {
  const fetchMock = vi.fn((url, opts) => {
    if (String(url).includes('/aprobar') && opts?.method === 'POST') return Promise.resolve(jsonResp({ id: 1, requiere_revision: false }))
    if (String(url).includes('/gastos/revision')) return Promise.resolve(jsonResp(lista, revisionStatus))
    return Promise.resolve(jsonResp([]))
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function comoAdmin() { localStorage.setItem('ferrebot_user', JSON.stringify({ rol: 'admin' })) }

beforeEach(() => { localStorage.clear() })
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('BandejaRevision — cola de recibos del bot (F5)', () => {
  it('pide /gastos/revision y pinta los recibos por revisar (monto, categoría, imputación, chip bot)', async () => {
    comoAdmin()
    const fetchMock = instalarFetch()
    render(<BandejaRevision refreshKey={0} />)

    expect(await screen.findByText('Por revisar · 2 recibos del bot')).toBeInTheDocument()
    expect(fetchMock.mock.calls.some(c => String(c[0]).includes('/gastos/revision'))).toBe(true)
    expect(screen.getByText('$15.000')).toBeInTheDocument()
    expect(screen.getByText('$8.000')).toBeInTheDocument()
    expect(screen.getByText('repuestos')).toBeInTheDocument()       // categoria_gasto normalizada
    expect(screen.getByText('Sin clasificar')).toBeInTheDocument()  // sin categoria_gasto
    expect(screen.getByText('Obra #5')).toBeInTheDocument()
    expect(screen.getByText('Máquina #9')).toBeInTheDocument()
    expect(screen.getAllByText('Bot').length).toBe(2)
  })

  it('aprobar dispara POST /gastos/{id}/aprobar y quita la fila optimista', async () => {
    comoAdmin()
    const fetchMock = instalarFetch()
    render(<BandejaRevision refreshKey={0} />)
    await screen.findByText('Por revisar · 2 recibos del bot')

    fireEvent.click(screen.getAllByText('Aprobar')[0])

    await waitFor(() => {
      expect(fetchMock.mock.calls.some(c => String(c[0]).includes('/gastos/1/aprobar') && c[1]?.method === 'POST')).toBe(true)
    })
    // La fila aprobada sale de la cola (queda 1).
    await waitFor(() => expect(screen.getByText('Por revisar · 1 recibo del bot')).toBeInTheDocument())
    expect(screen.queryByText('$15.000')).toBeNull()
  })

  it('no pinta nada cuando la cola está vacía', async () => {
    comoAdmin()
    instalarFetch([])
    const { container } = render(<BandejaRevision refreshKey={0} />)
    await new Promise((r) => setTimeout(r, 20))
    expect(screen.queryByText(/Por revisar/)).toBeNull()
    expect(container).toBeEmptyDOMElement()
  })

  it('no pinta nada si el fetch falla', async () => {
    comoAdmin()
    // apiJson lanza en status >= 400 → useFetch queda en error.
    instalarFetch(REVISION, { revisionStatus: 500 })
    const { container } = render(<BandejaRevision refreshKey={0} />)
    await new Promise((r) => setTimeout(r, 20))
    expect(container).toBeEmptyDOMElement()
  })

  it('no pinta nada (ni pide datos) si el usuario no es admin', async () => {
    // sin ferrebot_user → isAdmin() = false
    const fetchMock = instalarFetch()
    const { container } = render(<BandejaRevision refreshKey={0} />)
    await new Promise((r) => setTimeout(r, 20))
    expect(container).toBeEmptyDOMElement()
    expect(fetchMock.mock.calls.some(c => String(c[0]).includes('/gastos/revision'))).toBe(false)
  })
})
