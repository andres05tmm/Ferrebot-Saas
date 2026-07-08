import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import MobileNav from './MobileNav.jsx'
import { FeaturesProvider } from '@/lib/features.jsx'

function renderNav(features) {
  return render(
    <MemoryRouter>
      <FeaturesProvider features={features}>
        <MobileNav />
      </FeaturesProvider>
    </MemoryRouter>,
  )
}

afterEach(() => { cleanup() })

describe('MobileNav — bottom nav móvil', () => {
  it('muestra el grupo Construcción cuando el tenant tiene features de construcción (regresión: sin icono el grupo desaparecía)', () => {
    renderNav(['obras', 'maquinaria', 'construccion'])
    expect(screen.getByText('Construcción')).toBeInTheDocument()
  })

  it('NO muestra el grupo Construcción en un tenant retail (sin botón muerto)', () => {
    renderNav(['pos', 'ventas', 'clientes', 'inventario'])
    expect(screen.queryByText('Construcción')).toBeNull()
    // Un grupo con rutas habilitadas sí aparece.
    expect(screen.getByText('Operación')).toBeInTheDocument()
  })
})
