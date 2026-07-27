import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

vi.mock('@/components/RealtimeProvider.jsx', () => ({
  RealtimeProvider: ({ children }) => children,
  useRealtimeEvent: () => {},
}))

import TabConciliacion from './TabConciliacion.jsx'
import { USER_KEY } from '@/lib/api'
import { conQuery } from '@/test/query.jsx'

const MOVS = [
  {
    movimiento: { id: 10, referencia_bancaria: 'REF-1', fecha: '2026-06-10', monto: '100000',
      naturaleza: 'credito', estado_conciliacion: 'no_conciliado', conciliado_con_tipo: null,
      conciliado_con_id: null, conciliado_en: null },
    candidatos: [{ tipo: 'venta', id: 42, monto: '100000', fecha: '2026-06-10', descripcion: 'Venta #42' }],
  },
  {
    movimiento: { id: 11, referencia_bancaria: 'REF-2', fecha: '2026-06-11', monto: '50000',
      naturaleza: 'debito', estado_conciliacion: 'conciliado', conciliado_con_tipo: 'gasto',
      conciliado_con_id: 7, conciliado_en: '2026-06-11T12:00:00+00:00' },
    candidatos: [],
  },
]

function jsonResp(data, status = 200) { return { ok: status < 400, status, json: async () => data } }

function instalarFetch() {
  const fetchMock = vi.fn((url, opts = {}) => {
    const u = String(url)
    if (u.includes('/bancos/sugerir')) return Promise.resolve(jsonResp({ sugeridos: 1 }))
    if (u.includes('/bancos/movimientos/10/conciliar')) return Promise.resolve(jsonResp({ ...MOVS[0].movimiento, estado_conciliacion: 'conciliado' }))
    if (u.includes('/bancos/movimientos')) return Promise.resolve(jsonResp(MOVS))
    return Promise.resolve(jsonResp([]))
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function comoAdmin() { localStorage.setItem(USER_KEY, JSON.stringify({ id: 1, rol: 'admin', tenant: 'pr' })) }

beforeEach(() => { localStorage.clear() })
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('TabConciliacion', () => {
  it('sin rol admin muestra el aviso', () => {
    instalarFetch()
    render(conQuery(<MemoryRouter><TabConciliacion /></MemoryRouter>))
    expect(screen.getByText(/solo para administradores/i)).toBeInTheDocument()
  })

  it('lista movimientos con candidatos y concilia uno', async () => {
    comoAdmin()
    const fetchMock = instalarFetch()
    render(conQuery(<MemoryRouter><TabConciliacion /></MemoryRouter>))

    expect(await screen.findByText('REF-1')).toBeInTheDocument()
    expect(screen.getByText(/enlazado con gasto #7/)).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /Conciliar 10 con venta 42/ }))
    await screen.findByText('REF-1')
    const llamada = fetchMock.mock.calls.find(c => String(c[0]).includes('/bancos/movimientos/10/conciliar'))
    expect(JSON.parse(llamada[1].body)).toEqual({ tipo: 'venta', id_interno: 42 })
  })

  it('correr sugerencias llama a POST /bancos/sugerir', async () => {
    comoAdmin()
    const fetchMock = instalarFetch()
    render(conQuery(<MemoryRouter><TabConciliacion /></MemoryRouter>))
    await screen.findByText('REF-1')

    fireEvent.click(screen.getByRole('button', { name: /Correr sugerencias/ }))
    await screen.findByText('REF-1')
    expect(fetchMock.mock.calls.some(c => String(c[0]).includes('/bancos/sugerir') && c[1]?.method === 'POST')).toBe(true)
  })
})

// --- 0073: transferencias del correo + "no es venta" -------------------------

const GMAIL = [
  {
    movimiento: { id: 20, referencia_bancaria: null, fecha: '2026-07-27', monto: '150000',
      naturaleza: 'credito', estado_conciliacion: 'no_conciliado', conciliado_con_tipo: null,
      conciliado_con_id: null, conciliado_en: null, remitente: 'JHON JAIRO GARCIA MORALES',
      hora: '08:31', cuenta_destino: '*3891', tipo_transaccion: 'Código QR',
      descartado_en: null, origen: 'gmail' },
    candidatos: [],
  },
  {
    movimiento: { id: 21, referencia_bancaria: null, fecha: '2026-07-27', monto: '80000',
      naturaleza: 'credito', estado_conciliacion: 'no_conciliado', conciliado_con_tipo: null,
      conciliado_con_id: null, conciliado_en: null, remitente: 'MARIA GOMEZ',
      hora: '09:02', cuenta_destino: '*6485', tipo_transaccion: 'Nequi',
      descartado_en: null, origen: 'gmail' },
    candidatos: [
      { tipo: 'venta', id: 50, monto: '80000', fecha: '2026-07-27', descripcion: 'venta #50' },
      { tipo: 'venta', id: 51, monto: '80000', fecha: '2026-07-27', descripcion: 'venta #51' },
    ],
  },
]

function fetchGmail() {
  const fetchMock = vi.fn((url) => {
    const u = String(url)
    if (u.includes('/descarte')) return Promise.resolve(jsonResp({ ...GMAIL[0].movimiento, descartado_en: 'x' }))
    if (u.includes('/bancos/movimientos')) return Promise.resolve(jsonResp(GMAIL))
    return Promise.resolve(jsonResp([]))
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

describe('TabConciliacion — transferencias del correo del banco', () => {
  it('muestra quién mandó la plata, a qué cuenta y a qué hora', async () => {
    comoAdmin()
    fetchGmail()
    render(conQuery(<MemoryRouter><TabConciliacion /></MemoryRouter>))

    expect(await screen.findByText('JHON JAIRO GARCIA MORALES')).toBeInTheDocument()
    expect(screen.getByText(/\*3891/)).toBeInTheDocument()
    expect(screen.getByText(/08:31/)).toBeInTheDocument()
  })

  it('"No es venta" está aunque no haya ningún candidato', async () => {
    comoAdmin()
    const fetchMock = fetchGmail()
    render(conQuery(<MemoryRouter><TabConciliacion /></MemoryRouter>))
    await screen.findByText('JHON JAIRO GARCIA MORALES')

    // Ese movimiento no tiene candidatos: el botón tiene que estar igual, porque a esas cuentas
    // también entra plata de la casa.
    fireEvent.click(screen.getByRole('button', { name: /Marcar que JHON JAIRO GARCIA MORALES no es una venta/ }))
    await screen.findByText('JHON JAIRO GARCIA MORALES')

    const llamada = fetchMock.mock.calls.find(c => String(c[0]).includes('/bancos/movimientos/20/descarte'))
    expect(llamada).toBeTruthy()
    expect(llamada[1].method).toBe('POST')
  })

  it('con dos candidatos no concilia nada solo: espera el click', async () => {
    comoAdmin()
    const fetchMock = fetchGmail()
    render(conQuery(<MemoryRouter><TabConciliacion /></MemoryRouter>))
    await screen.findByText('MARIA GOMEZ')

    expect(screen.getByText(/Varios candidatos/)).toBeInTheDocument()
    expect(fetchMock.mock.calls.some(c => String(c[0]).includes('/conciliar'))).toBe(false)
  })
})

// --- fase 2: el cruce se corre solo y el candidato dice de quién es ----------

const CON_CLIENTE = [
  {
    movimiento: { id: 30, referencia_bancaria: null, fecha: '2026-07-27', monto: '120000',
      naturaleza: 'credito', estado_conciliacion: 'no_conciliado', conciliado_con_tipo: null,
      conciliado_con_id: null, conciliado_en: null, remitente: 'PEDRO RAMIREZ',
      hora: '10:15', cuenta_destino: '*3891', tipo_transaccion: 'Transferencia',
      descartado_en: null, origen: 'gmail' },
    candidatos: [
      { tipo: 'venta', id: 60, monto: '120000', fecha: '2026-07-27',
        descripcion: 'parte por transferencia de la venta #60', cliente: 'PEDRO RAMIREZ' },
      { tipo: 'abono_fiado', id: 9, monto: '120000', fecha: '2026-07-27',
        descripcion: 'abono al fiado #3', cliente: 'ANA LOPEZ' },
    ],
  },
]

describe('TabConciliacion — match de mixtas y fiados', () => {
  it('el candidato dice de quién es el pago y qué es', async () => {
    comoAdmin()
    vi.stubGlobal('fetch', vi.fn((url) => Promise.resolve(
      String(url).includes('/bancos/movimientos') ? jsonResp(CON_CLIENTE) : jsonResp({ sugeridos: 0 })
    )))
    render(conQuery(<MemoryRouter><TabConciliacion /></MemoryRouter>))
    await screen.findByText('PEDRO RAMIREZ')

    expect(screen.getByText(/parte por transferencia de la venta #60.*PEDRO RAMIREZ/)).toBeInTheDocument()
    expect(screen.getByText(/abono al fiado #3.*ANA LOPEZ/)).toBeInTheDocument()
  })

  it('muestra cuánto entró, cuánto es del negocio y el desglose por cuenta', async () => {
    comoAdmin()
    vi.stubGlobal('fetch', vi.fn((url) => {
      const u = String(url)
      if (u.includes('/bancos/totales')) return Promise.resolve(jsonResp({
        desde: '2026-07-01', hasta: '2026-07-27',
        total: '430000.00', total_negocio: '350000.00', total_personal: '80000.00',
        sin_clasificar: 2,
        por_cuenta: [
          { cuenta: '*3891', alias: 'Andrés', movimientos: 2, total: '180000.00', total_negocio: '100000.00' },
          { cuenta: null, alias: null, movimientos: 1, total: '250000.00', total_negocio: '250000.00' },
        ],
      }))
      if (u.includes('/bancos/movimientos')) return Promise.resolve(jsonResp(GMAIL))
      return Promise.resolve(jsonResp({ sugeridos: 0 }))
    }))
    render(conQuery(<MemoryRouter><TabConciliacion /></MemoryRouter>))

    expect(await screen.findByText('Andrés')).toBeInTheDocument()
    expect(screen.getByText('Del negocio')).toBeInTheDocument()
    // La cuenta que el parser no pudo leer se muestra, no se esconde.
    expect(screen.getByText(/Cuenta sin identificar/)).toBeInTheDocument()
    expect(screen.getByText(/2 movimiento\(s\) sin resolver/)).toBeInTheDocument()
  })

  it('al abrir el tab corre el cruce solo, sin que nadie toque el botón', async () => {
    comoAdmin()
    const fetchMock = fetchGmail()
    render(conQuery(<MemoryRouter><TabConciliacion /></MemoryRouter>))
    await screen.findByText('JHON JAIRO GARCIA MORALES')

    // Si el pago llegó antes que la venta, deja de esperar sin intervención.
    expect(fetchMock.mock.calls.some(
      c => String(c[0]).includes('/bancos/sugerir') && c[1]?.method === 'POST',
    )).toBe(true)
  })
})
