import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import Sidebar from './Sidebar.jsx'
import { FeaturesProvider } from '@/lib/features.jsx'
import { BrandingProvider } from '@/lib/branding.jsx'

function renderSidebar(features, branding = {}) {
  return render(
    <MemoryRouter>
      <FeaturesProvider features={features}>
        <BrandingProvider branding={branding}>
          <Sidebar
            collapsed={false}
            setCollapsed={() => {}}
            onOpenCommand={() => {}}
            colorScheme="light"
            onToggleColorScheme={() => {}}
          />
        </BrandingProvider>
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
    // Núcleo transversal (ADR 0008): Hoy, Clientes, Resultados financieros.
    expect(screen.getByText('Hoy')).toBeInTheDocument()
    expect(screen.getByText('Clientes')).toBeInTheDocument()
    expect(screen.getByText('Resultados financieros')).toBeInTheDocument()
    // El POS ya NO es núcleo: oculto sin la capacidad `pos`.
    expect(screen.queryByText('Ventas Rápidas')).toBeNull()
    expect(screen.queryByText('Inventario')).toBeNull()
  })

  it('oculta los tabs fiscales cuando su feature no está activa', () => {
    renderSidebar(['pos', 'clientes'])
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

describe('branding white-label del Sidebar', () => {
  it('muestra el logo + nombre comercial de la empresa (GET /config)', () => {
    renderSidebar([], { logo_url: 'https://cdn.test/pr/logo.png', nombre_comercial: 'Punto Rojo' })
    expect(screen.getByText('Punto Rojo')).toBeInTheDocument()
    const logo = screen.getByAltText('Punto Rojo')
    expect(logo).toBeInTheDocument()
    expect(logo.getAttribute('src')).toBe('https://cdn.test/pr/logo.png')
  })

  it('sin branding usa el fallback neutro "FerreBot" (no rompe)', () => {
    renderSidebar([])
    expect(screen.getByText('FerreBot')).toBeInTheDocument()
    expect(screen.queryByRole('img')).toBeNull()   // sin logo_url → cuadro tematizado, no <img>
  })

  it('si el logo no carga (onError) degrada al cuadro tematizado, no al img roto', () => {
    renderSidebar([], { logo_url: 'https://cdn.test/rota.png', nombre_comercial: 'Punto Rojo' })
    const logo = screen.getByAltText('Punto Rojo')
    fireEvent.error(logo)                              // la imagen falla al cargar
    expect(screen.queryByRole('img')).toBeNull()       // ya no se renderiza el <img> roto
    expect(screen.getByText('Punto Rojo')).toBeInTheDocument()   // el nombre + cuadro siguen
  })
})
