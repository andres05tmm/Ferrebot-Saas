/*
 * Fallback navegador (R2, ADR 0033 D1.c): los 3 tickets renderizan con CSS de ancho térmico.
 * La precuenta cumple Ley 1935: propina sugerida y voluntaria, JAMÁS sumada al total.
 */
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import TicketTermico from './TicketTermico.jsx'

const COMANDA = {
  tipo: 'comanda', zona: 'parrilla', origen: 'mesa', cliente: 'Mesa 4', notas: 'afán',
  items: [{ nombre: 'Hamburguesa', cantidad: '2', modificadores: [{ opcion: 'sin cebolla' }] }],
}

const PRECUENTA = {
  tipo: 'precuenta', cliente: 'Mesa 1', total: '52000', subtotal: '52000',
  items: [{ nombre: 'Churrasco', cantidad: '1', subtotal: '52000', modificadores: [] }],
}

const COMPROBANTE = {
  tipo: 'comprobante', consecutivo: 42, fecha: '2026-07-24', metodo_pago: 'efectivo',
  total: '60000', items: [{ nombre: 'Bandeja paisa', cantidad: '2', subtotal: '60000' }],
}

describe('TicketTermico (fallback navegador)', () => {
  it('comanda: 80mm, modificador destacado en mayúsculas', () => {
    const { container } = render(<TicketTermico payload={COMANDA} ancho={80} />)
    expect(container.firstChild.style.width).toBe('80mm')
    expect(screen.getByText('PARRILLA')).toBeInTheDocument()
    expect(screen.getByText(/2 x Hamburguesa/)).toBeInTheDocument()
    expect(screen.getByText(/SIN CEBOLLA/)).toBeInTheDocument()
    expect(screen.getByText(/NOTA: afán/)).toBeInTheDocument()
  })

  it('comanda: 58mm también', () => {
    const { container } = render(<TicketTermico payload={COMANDA} ancho={58} />)
    expect(container.firstChild.style.width).toBe('58mm')
  })

  it('precuenta: leyenda INC + propina Ley 1935 sin sumarla al total', () => {
    render(<TicketTermico payload={PRECUENTA} ancho={80} negocio="Brasa" />)
    expect(screen.getByText('Brasa')).toBeInTheDocument()
    expect(screen.getAllByText('$52.000').length).toBeGreaterThan(0)
    expect(screen.getByText(/Precios incluyen INC 8%/)).toBeInTheDocument()
    expect(screen.getByText(/Propina sugerida \(10%\): \$5\.200/)).toBeInTheDocument()
    expect(screen.getByText(/VOLUNTARIA/)).toBeInTheDocument()
    // total + propina ($57.200) NO existe en el ticket: la propina jamás se suma por defecto.
    expect(screen.queryByText(/57\.200/)).toBeNull()
    expect(screen.getByText(/no fiscal/)).toBeInTheDocument()
  })

  it('comprobante: venta, método de pago y no fiscal', () => {
    render(<TicketTermico payload={COMPROBANTE} ancho={80} negocio="Brasa" />)
    expect(screen.getByText('Venta #42')).toBeInTheDocument()
    expect(screen.getAllByText('$60.000').length).toBeGreaterThan(0)
    expect(screen.getByText(/Pago: efectivo/)).toBeInTheDocument()
    expect(screen.getByText(/no fiscal/)).toBeInTheDocument()
  })
})
