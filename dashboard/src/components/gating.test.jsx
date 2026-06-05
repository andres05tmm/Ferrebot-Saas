import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import Sidebar from './Sidebar.jsx'
import { FeaturesProvider } from '@/lib/features.jsx'

function renderSidebar(features) {
  return render(
    <MemoryRouter>
      <FeaturesProvider features={features}>
        <Sidebar
          collapsed={false}
          setCollapsed={() => {}}
          onOpenCommand={() => {}}
          colorScheme="light"
          onToggleColorScheme={() => {}}
        />
      </FeaturesProvider>
    </MemoryRouter>,
  )
}

beforeEach(() => {
  // Expandir todos los grupos (Fiscal viene colapsado por defecto) para poder afirmar el gating.
  localStorage.setItem(
    'ferrebot_sidebar_groups',
    JSON.stringify({ operacion: true, gestion: true, reportes: true, fiscal: true }),
  )
})

afterEach(() => {
  cleanup()
  localStorage.clear()
})

describe('gating del Sidebar', () => {
  it('muestra los tabs núcleo siempre (sin importar features)', () => {
    renderSidebar([])
    expect(screen.getByText('Ventas Rápidas')).toBeInTheDocument()
    expect(screen.getByText('Clientes')).toBeInTheDocument()
    expect(screen.getByText('Inventario')).toBeInTheDocument()
  })

  it('oculta los tabs fiscales cuando su feature no está activa', () => {
    renderSidebar(['ventas', 'clientes'])
    expect(screen.queryByText('Facturación')).toBeNull()
    expect(screen.queryByText('Facturas recibidas')).toBeNull()
    expect(screen.queryByText('Libro IVA')).toBeNull()
  })

  it('muestra un tab fiscal cuando su feature está presente', () => {
    renderSidebar(['facturacion_electronica'])
    expect(screen.getByText('Facturación')).toBeInTheDocument()
    expect(screen.getByText('Facturas recibidas')).toBeInTheDocument()
    // Libro IVA sigue oculto: requiere su propia capacidad (libro_iva).
    expect(screen.queryByText('Libro IVA')).toBeNull()
  })
})
