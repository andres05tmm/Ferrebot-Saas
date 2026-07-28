import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
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
  it('muestra quién mandó la plata y a qué hora', async () => {
    comoAdmin()
    fetchGmail()
    render(conQuery(<MemoryRouter><TabConciliacion /></MemoryRouter>))

    // La cuenta destino salió de la fila (la dice la lente); el remitente y la hora se quedan.
    expect(await screen.findByText('JHON JAIRO GARCIA MORALES')).toBeInTheDocument()
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
        descripcion: 'parte por transferencia de la venta #60', cliente: 'PEDRO RAMIREZ',
        detalle: '2 Cemento gris 50kg, 1.5 Varilla 1/2' },
      { tipo: 'abono_fiado', id: 9, monto: '120000', fecha: '2026-07-27',
        descripcion: 'abono al fiado #3', cliente: 'ANA LOPEZ', detalle: null },
    ],
  },
]

describe('TabConciliacion — match de mixtas y fiados', () => {
  it('el candidato lidera con los productos y el consecutivo baja a rastro', async () => {
    comoAdmin()
    vi.stubGlobal('fetch', vi.fn((url) => Promise.resolve(
      String(url).includes('/bancos/movimientos') ? jsonResp(CON_CLIENTE) : jsonResp({ sugeridos: 0 })
    )))
    render(conQuery(<MemoryRouter><TabConciliacion /></MemoryRouter>))
    await screen.findByText('PEDRO RAMIREZ')

    // Lo que el dueño reconoce va adelante y solo: qué se vendió.
    expect(screen.getByText('2 Cemento gris 50kg, 1.5 Varilla 1/2')).toBeInTheDocument()
    // El consecutivo sigue estando, pero acompañando al cliente, no como única señal.
    expect(screen.getByText(/PEDRO RAMIREZ.*parte por transferencia de la venta #60/)).toBeInTheDocument()
    // Sin productos (un abono de fiado) la descripción vuelve a ser el título de la línea.
    expect(screen.getByText('abono al fiado #3')).toBeInTheDocument()
    expect(screen.getByText(/ANA LOPEZ/)).toBeInTheDocument()
  })

  it('la cuenta destino ya no se repite en cada movimiento: la dice la lente', async () => {
    comoAdmin()
    vi.stubGlobal('fetch', vi.fn((url) => Promise.resolve(
      String(url).includes('/bancos/movimientos') ? jsonResp(CON_CLIENTE) : jsonResp({ sugeridos: 0 })
    )))
    render(conQuery(<MemoryRouter><TabConciliacion /></MemoryRouter>))
    await screen.findByText('PEDRO RAMIREZ')

    // El remitente SÍ se queda: es quién mandó la plata, el dato más útil de la fila.
    expect(screen.getByText(/10:15.*Transferencia/)).toBeInTheDocument()
    expect(screen.queryByText(/\*3891/)).not.toBeInTheDocument()
  })

  const TOTALES = {
    desde: '2026-07-01', hasta: '2026-07-27',
    total: '430000.00', total_negocio: '350000.00', total_personal: '80000.00',
    sin_clasificar: 2,
    por_cuenta: [
      { cuenta: '*3891', alias: 'Andrés', movimientos: 2, total: '180000.00',
        total_negocio: '100000.00', sin_clasificar: 1 },
      { cuenta: null, alias: null, movimientos: 1, total: '250000.00',
        total_negocio: '250000.00', sin_clasificar: 1 },
    ],
  }

  const fetchConTotales = (extra = () => null) => vi.fn((url) => {
    const u = String(url)
    const propio = extra(u)
    if (propio) return propio
    if (u.includes('/bancos/totales')) return Promise.resolve(jsonResp(TOTALES))
    if (u.includes('/bancos/movimientos')) return Promise.resolve(jsonResp(GMAIL))
    return Promise.resolve(jsonResp({ sugeridos: 0 }))
  })

  it('muestra una sola cifra —lo cobrado del negocio— y no lo personal', async () => {
    comoAdmin()
    vi.stubGlobal('fetch', fetchConTotales())
    render(conQuery(<MemoryRouter><TabConciliacion /></MemoryRouter>))

    expect(await screen.findByText('Cobrado por transferencia')).toBeInTheDocument()
    expect(screen.getByText('$350.000')).toBeInTheDocument()
    // Lo que entró en bruto y lo personal salieron: no son asunto del negocio.
    expect(screen.queryByText('Entró')).not.toBeInTheDocument()
    expect(screen.queryByText('Personal')).not.toBeInTheDocument()
    // El bruto ($430.000) no aparece en ningún lado: era el KPI "Entró".
    expect(screen.queryByText('$430.000')).not.toBeInTheDocument()
    // Lo sin resolver se queda: sin eso la cifra se lee más firme de lo que es.
    expect(screen.getByText(/2 sin resolver/)).toBeInTheDocument()
  })

  it('elegir una cuenta acota la cifra y vuelve a pedir la lista filtrada', async () => {
    comoAdmin()
    const fetchMock = fetchConTotales()
    vi.stubGlobal('fetch', fetchMock)
    render(conQuery(<MemoryRouter><TabConciliacion /></MemoryRouter>))
    await screen.findByText('$350.000')

    fireEvent.click(screen.getByRole('tab', { name: 'Andrés' }))

    // La cifra pasa a ser la de esa cuenta, con su propio pendiente.
    expect(await screen.findByText('$100.000')).toBeInTheDocument()
    expect(screen.getByText('Cobrado por transferencia · Andrés')).toBeInTheDocument()
    expect(screen.getByText(/1 sin resolver/)).toBeInTheDocument()
    // Y la bandeja se vuelve a pedir acotada: la lente es del panel entero, no solo del número.
    await waitFor(() => expect(fetchMock.mock.calls.some(
      // `URLSearchParams` no escapa el asterisco: viaja tal cual en `cuenta=*3891`.
      c => String(c[0]).includes('/bancos/movimientos') && String(c[0]).includes('cuenta=*3891')
    )).toBe(true))
  })

  it('la cuenta que el parser no pudo leer es una lente más, no un agujero', async () => {
    comoAdmin()
    const fetchMock = fetchConTotales()
    vi.stubGlobal('fetch', fetchMock)
    render(conQuery(<MemoryRouter><TabConciliacion /></MemoryRouter>))
    await screen.findByText('$350.000')

    fireEvent.click(await screen.findByRole('tab', { name: 'Sin identificar' }))

    await waitFor(() => expect(fetchMock.mock.calls.some(
      c => String(c[0]).includes('/bancos/movimientos') && String(c[0]).includes('cuenta=sin_cuenta')
    )).toBe(true))
  })

  it('con una sola cuenta no aparece el selector: elegir entre una no es elegir', async () => {
    comoAdmin()
    vi.stubGlobal('fetch', fetchConTotales(u => (u.includes('/bancos/totales')
      ? Promise.resolve(jsonResp({ ...TOTALES, por_cuenta: [TOTALES.por_cuenta[0]] }))
      : null)))
    render(conQuery(<MemoryRouter><TabConciliacion /></MemoryRouter>))
    await screen.findByText('Cobrado por transferencia')

    expect(screen.queryByRole('tablist', { name: /cuenta bancaria/ })).not.toBeInTheDocument()
  })

  it('sin el filtro "No son ventas", los descartados siguen alcanzables en "Todos"', async () => {
    comoAdmin()
    const fetchMock = fetchConTotales()
    vi.stubGlobal('fetch', fetchMock)
    render(conQuery(<MemoryRouter><TabConciliacion /></MemoryRouter>))
    await screen.findByText('Cobrado por transferencia')

    // El filtro se fue de la barra de estados…
    expect(screen.queryByRole('button', { name: 'No son ventas' })).not.toBeInTheDocument()
    // …pero "Todos" los pide igual, que es donde vive el botón de deshacer.
    await waitFor(() => expect(fetchMock.mock.calls.some(
      c => String(c[0]).includes('/bancos/movimientos') && String(c[0]).includes('incluir_descartados=true')
    )).toBe(true))
  })

  it('"Quién repite" está colapsado y no se pide hasta abrirlo', async () => {
    comoAdmin()
    const fetchMock = vi.fn((url) => {
      const u = String(url)
      if (u.includes('/bancos/remitentes')) return Promise.resolve(jsonResp([
        { nombre: 'LUCIA TORRES', veces: 4, total: '320000.00', primera: '2026-07-01',
          ultima: '2026-07-25', conciliados: 3 },
      ]))
      if (u.includes('/bancos/movimientos')) return Promise.resolve(jsonResp(GMAIL))
      return Promise.resolve(jsonResp({ sugeridos: 0 }))
    })
    vi.stubGlobal('fetch', fetchMock)
    render(conQuery(<MemoryRouter><TabConciliacion /></MemoryRouter>))
    await screen.findByText('JHON JAIRO GARCIA MORALES')

    // Es el objetivo terciario: no se paga la consulta al abrir el tab.
    expect(fetchMock.mock.calls.some(c => String(c[0]).includes('/bancos/remitentes'))).toBe(false)

    fireEvent.click(screen.getByRole('button', { name: /Quién repite/ }))
    expect(await screen.findByText('LUCIA TORRES')).toBeInTheDocument()
    expect(screen.getByText(/4 transferencias/)).toBeInTheDocument()
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

describe('TabConciliacion — el tab se pone al día al volver a la ventana', () => {
  it('vuelve a pedir los movimientos cuando la ventana recupera el foco', async () => {
    comoAdmin()
    const fetchMock = vi.fn((url) => Promise.resolve(
      String(url).includes('/bancos/movimientos') ? jsonResp(GMAIL) : jsonResp({ sugeridos: 0 })
    ))
    vi.stubGlobal('fetch', fetchMock)
    // `staleTime: 0` reproduce el caso real que importa: estuviste fuera un rato, los datos ya
    // envejecieron. Con datos frescos React Query no refetchea, y eso también es lo correcto.
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 0 } } })
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter><TabConciliacion /></MemoryRouter>
      </QueryClientProvider>
    )
    await screen.findByText('JHON JAIRO GARCIA MORALES')

    const antes = fetchMock.mock.calls.filter(c => String(c[0]).includes('/bancos/movimientos')).length

    // Lo que hace el navegador al volver a la pestaña. Es la red de seguridad del `pg_notify`:
    // si la transferencia entró con la ventana en segundo plano, el evento se perdió y sin esto
    // la fila solo aparecía al recargar a mano.
    // Va en `window`, no en `document`: ahí es donde React Query engancha el listener (v5,
    // query-core/focusManager). En `document` el evento no llega y el test pasa a verde en falso.
    act(() => { window.dispatchEvent(new Event('visibilitychange')) })

    await waitFor(() => expect(
      fetchMock.mock.calls.filter(c => String(c[0]).includes('/bancos/movimientos')).length
    ).toBeGreaterThan(antes))
  })
})
