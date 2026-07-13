/*
 * PageHeader.test.jsx — el encabezado compartido de página (F2.0): título como heading (font-display),
 * sublínea, slot de acciones (la toolbar) y children (fila de chips/KPIs).
 */
import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { HardHat } from 'lucide-react'

import PageHeader from './PageHeader.jsx'

describe('PageHeader', () => {
  it('renderiza título como heading, sublínea, acciones y children', () => {
    render(
      <PageHeader icono={HardHat} titulo="Obras" sublinea="Presupuesto vs real de cada obra."
        acciones={<button type="button">Nueva obra</button>}>
        <span>fila de chips</span>
      </PageHeader>,
    )
    expect(screen.getByRole('heading', { name: /Obras/ })).toBeInTheDocument()
    expect(screen.getByText('Presupuesto vs real de cada obra.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Nueva obra' })).toBeInTheDocument()
    expect(screen.getByText('fila de chips')).toBeInTheDocument()
  })

  it('sin sublínea ni acciones no deja huecos (solo el heading)', () => {
    const { container } = render(<PageHeader titulo="Nómina" />)
    expect(screen.getByRole('heading', { name: 'Nómina' })).toBeInTheDocument()
    expect(container.querySelectorAll('p').length).toBe(0)
  })
})
