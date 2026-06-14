/*
 * Gating de /historial transversal (ADR 0018): el wrapper HistorialPorFamilia elige el componente por
 * familia — historial de ventas (POS) vs. historial por vertical (servicios). Los dos hijos se mockean
 * para aislar la decisión de familia de su contenido real.
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'

vi.mock('@/tabs/TabHistorial.jsx', () => ({ default: () => <div>HISTORIAL POS</div> }))
vi.mock('@/tabs/TabHistorialServicios.jsx', () => ({ default: () => <div>HISTORIAL SERVICIOS</div> }))

import { HistorialPorFamilia } from '@/App.jsx'
import { FeaturesProvider } from '@/lib/features.jsx'

function renderWrapper(features) {
  return render(
    <FeaturesProvider features={features}>
      <HistorialPorFamilia />
    </FeaturesProvider>,
  )
}

afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('HistorialPorFamilia — elige el componente por familia', () => {
  it('familia POS (ferretería) → historial de ventas', () => {
    renderWrapper(['pos'])
    expect(screen.getByText('HISTORIAL POS')).toBeInTheDocument()
    expect(screen.queryByText('HISTORIAL SERVICIOS')).toBeNull()
  })

  it('restaurante (pack_pedidos) → historial de servicios', () => {
    renderWrapper(['pos', 'pack_pedidos'])
    expect(screen.getByText('HISTORIAL SERVICIOS')).toBeInTheDocument()
    expect(screen.queryByText('HISTORIAL POS')).toBeNull()
  })

  it('barbería (pack_agenda) → historial de servicios', () => {
    renderWrapper(['pack_agenda'])
    expect(screen.getByText('HISTORIAL SERVICIOS')).toBeInTheDocument()
  })

  it('hotel (pack_agenda + pack_reservas) → historial de servicios', () => {
    renderWrapper(['pack_agenda', 'pack_reservas'])
    expect(screen.getByText('HISTORIAL SERVICIOS')).toBeInTheDocument()
  })
})
