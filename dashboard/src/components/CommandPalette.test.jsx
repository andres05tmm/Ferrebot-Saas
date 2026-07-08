import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { cleanup as rtlCleanup, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import CommandPalette from './CommandPalette.jsx'
import { FeaturesProvider } from '@/lib/features.jsx'

// cmdk hace scrollIntoView sobre el item seleccionado al montar; jsdom no lo trae.
beforeAll(() => {
  if (!Element.prototype.scrollIntoView) Element.prototype.scrollIntoView = () => {}
})

function renderPalette(features = []) {
  return render(
    <MemoryRouter>
      <FeaturesProvider features={features}>
        <CommandPalette open setOpen={() => {}} onRefresh={() => {}} />
      </FeaturesProvider>
    </MemoryRouter>,
  )
}

afterEach(() => { rtlCleanup(); vi.restoreAllMocks() })

describe('CommandPalette — acción "Nueva venta rápida" gateada por /ventas', () => {
  it('retail (pos): muestra "Nueva venta rápida"', () => {
    renderPalette(['pos'])
    expect(screen.getByText('Nueva venta rápida')).toBeInTheDocument()
    // Las demás acciones no dependen de /ventas y siguen.
    expect(screen.getByText('Registrar gasto')).toBeInTheDocument()
    expect(screen.getByText('Abrir / cerrar caja')).toBeInTheDocument()
  })

  it('construcción: OCULTA "Nueva venta rápida" (no vende por mostrador)', () => {
    renderPalette(['construccion', 'obras', 'pos', 'inventario', 'caja'])
    expect(screen.queryByText('Nueva venta rápida')).toBeNull()
    // El resto de acciones (gasto, caja) sigue disponible para la obra.
    expect(screen.getByText('Registrar gasto')).toBeInTheDocument()
    expect(screen.getByText('Abrir / cerrar caja')).toBeInTheDocument()
  })

  it('servicios (sin pos ni ventas): también OCULTA "Nueva venta rápida"', () => {
    renderPalette(['pack_agenda', 'canal_whatsapp'])
    expect(screen.queryByText('Nueva venta rápida')).toBeNull()
  })
})
