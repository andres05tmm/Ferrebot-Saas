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
  it('la familia construcción ve los grupos por flujo (F2.1) — todos con icono (regresión: sin icono el grupo desaparecía)', () => {
    // PIM real: vertical + pos arrastrado + cobranza → los 5 grupos de obra tienen rutas.
    renderNav(['construccion', 'obras', 'maquinaria', 'nomina', 'cotizaciones_aiu', 'resbalos',
      'pos', 'caja', 'inventario', 'pack_cobranza'])
    for (const grupo of ['Obra', 'Comercial', 'Recursos', 'Materiales', 'Plata']) {
      expect(screen.getByText(grupo)).toBeInTheDocument()
    }
    // Los grupos viejos no aparecen para construcción.
    expect(screen.queryByText('Construcción')).toBeNull()
    expect(screen.queryByText('Gestión')).toBeNull()
  })

  it('un tenant retail conserva sus grupos de siempre (sin grupos de obra ni botón muerto)', () => {
    renderNav(['pos', 'ventas', 'clientes', 'inventario'])
    expect(screen.queryByText('Construcción')).toBeNull()
    expect(screen.queryByText('Obra')).toBeNull()
    expect(screen.queryByText('Plata')).toBeNull()
    // Un grupo con rutas habilitadas sí aparece.
    expect(screen.getByText('Operación')).toBeInTheDocument()
  })
})
