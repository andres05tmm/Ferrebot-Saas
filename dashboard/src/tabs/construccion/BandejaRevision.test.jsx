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

  it('cola vacía → estado "al día" visible (F2.2: la bandeja ya no desaparece en silencio)', async () => {
    comoAdmin()
    instalarFetch([])
    render(<BandejaRevision refreshKey={0} />)
    expect(await screen.findByText(/Bandeja del bot al día/)).toBeInTheDocument()
    expect(screen.queryByText(/Por revisar/)).toBeNull()
  })

  it('rechazar abre confirmación con motivo y dispara POST /gastos/{id}/rechazar (F2.2)', async () => {
    comoAdmin()
    const fetchMock = instalarFetch()
    render(<BandejaRevision refreshKey={0} />)
    await screen.findByText('Por revisar · 2 recibos del bot')

    fireEvent.click(screen.getAllByText('Rechazar')[0])
    // Confirmación destructiva con el copy de la reversa.
    expect(await screen.findByText(/¿Rechazar el recibo de \$15\.000\?/)).toBeInTheDocument()
    expect(screen.getByText(/movimiento inverso/)).toBeInTheDocument()
    fireEvent.change(screen.getByPlaceholderText(/monto ilegible/), { target: { value: 'recibo repetido' } })
    fireEvent.click(screen.getByRole('button', { name: /Rechazar y devolver a caja/ }))

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(
        (c) => String(c[0]).includes('/gastos/1/rechazar') && c[1]?.method === 'POST',
      )
      expect(call).toBeTruthy()
      expect(JSON.parse(call[1].body)).toEqual({ motivo: 'recibo repetido' })
    })
    // La fila rechazada sale de la cola (optimista).
    await waitFor(() => expect(screen.getByText('Por revisar · 1 recibo del bot')).toBeInTheDocument())
  })

  it('corregir imputación dispara PATCH /gastos/{id}/imputacion con solo lo elegido (F2.2)', async () => {
    comoAdmin()
    const fetchMock = instalarFetch()
    render(<BandejaRevision refreshKey={0} />)
    await screen.findByText('Por revisar · 2 recibos del bot')

    fireEvent.click(screen.getAllByTitle('Corregir imputación')[1])   // el gasto #2, sin categoría
    fireEvent.change(await screen.findByLabelText('Categoría'), { target: { value: 'COMBUSTIBLE' } })
    fireEvent.change(screen.getByLabelText('Concepto'), { target: { value: 'ACPM retro' } })
    fireEvent.click(screen.getByRole('button', { name: /Guardar imputación/ }))

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(
        (c) => String(c[0]).includes('/gastos/2/imputacion') && c[1]?.method === 'PATCH',
      )
      expect(call).toBeTruthy()
      const body = JSON.parse(call[1].body)
      expect(body.categoria_gasto).toBe('COMBUSTIBLE')
      expect(body.concepto).toBe('ACPM retro')
      expect(body).not.toHaveProperty('monto')   // el monto JAMÁS viaja
    })
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
