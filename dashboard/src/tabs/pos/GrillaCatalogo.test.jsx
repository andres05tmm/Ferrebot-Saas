import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { useState } from 'react'

import GrillaCatalogo from './GrillaCatalogo.jsx'
import { FAV_KEY, leerFavs, toggleFav } from './favoritos.js'

const PRODUCTOS = [
  { id: 1, nombre: 'Vinilo azul', categoria: 'Pinturas', precio_venta: '28000' },
  { id: 2, nombre: 'Martillo', categoria: 'Herramientas', precio_venta: '11900' },
  { id: 3, nombre: 'Estuco', categoria: 'Pinturas', precio_venta: '41000' },
  { id: 4, nombre: 'Brocha de 2"', categoria: '1 Articulos de Ferreteria', precio_venta: '4000' },
  { id: 5, nombre: 'Cerradura de Alcoba', categoria: '1 Articulos de Ferreteria', precio_venta: '14000' },
]

function Harness({ chipInicial = 'todo', frecuentesIds = new Set(), onTap = () => {} }) {
  const [favoritos, setFavoritos] = useState(() => leerFavs())
  const [chip, setChip] = useState(chipInicial)
  return (
    <GrillaCatalogo
      productos={PRODUCTOS} buscando={false} fuente="local"
      frecuentesIds={frecuentesIds}
      favoritos={favoritos} onToggleFav={(id) => setFavoritos(f => toggleFav(f, id))}
      cantidades={new Map([[2, 3]])}
      categorias={['1 Articulos de Ferreteria', 'Herramientas', 'Pinturas']}
      chip={chip} setChip={setChip}
      sel={0} onTap={onTap}
    />
  )
}

afterEach(() => { cleanup(); localStorage.clear() })

describe('GrillaCatalogo', () => {
  it('los chips de categoría filtran las cards', () => {
    render(<Harness />)
    expect(screen.getByLabelText('Agregar Martillo')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Pinturas' }))
    expect(screen.queryByLabelText('Agregar Martillo')).toBeNull()
    expect(screen.getByLabelText('Agregar Vinilo azul')).toBeInTheDocument()
    expect(screen.getByLabelText('Agregar Estuco')).toBeInTheDocument()
  })

  it('la estrella marca favorito, persiste en localStorage y alimenta el chip ★', () => {
    render(<Harness />)
    fireEvent.click(screen.getByLabelText('Marcar Martillo como favorito'))
    expect(JSON.parse(localStorage.getItem(FAV_KEY))).toEqual([2])

    fireEvent.click(screen.getByRole('button', { name: /Favoritos/ }))
    expect(screen.getByLabelText('Agregar Martillo')).toBeInTheDocument()
    expect(screen.queryByLabelText('Agregar Estuco')).toBeNull()

    // Quitar el favorito lo saca de la vista ★ y del storage.
    fireEvent.click(screen.getByLabelText('Quitar Martillo de favoritos'))
    expect(JSON.parse(localStorage.getItem(FAV_KEY))).toEqual([])
  })

  it('tap en la card agrega (onTap) y el badge muestra lo que ya va en el carrito', () => {
    const onTap = vi.fn()
    render(<Harness onTap={onTap} />)
    fireEvent.click(screen.getByLabelText('Agregar Vinilo azul'))
    expect(onTap).toHaveBeenCalledWith(expect.objectContaining({ id: 1 }))
    expect(screen.getByLabelText('3 en el carrito')).toBeInTheDocument()   // badge del Martillo
  })

  it('el chip Top productos filtra por los ids de frecuentes', () => {
    render(<Harness frecuentesIds={new Set([3])} />)
    fireEvent.click(screen.getByRole('button', { name: /Top productos/ }))
    expect(screen.getByLabelText('Agregar Estuco')).toBeInTheDocument()
    expect(screen.queryByLabelText('Agregar Martillo')).toBeNull()
  })

  it('elegir una categoría con reglas muestra la fila de SUBCATEGORÍAS y filtran (réplica del viejo)', () => {
    render(<Harness />)
    fireEvent.click(screen.getByRole('button', { name: /Articulos de Ferreteria/ }))
    // Segunda fila de chips: Todas + las subcategorías del viejo.
    const fila = screen.getByRole('group', { name: /Subcategorías de Articulos de Ferreteria/ })
    expect(fila).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Brochas \/ Rodillos/ })).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /Cerraduras/ }))
    expect(screen.getByLabelText('Agregar Cerradura de Alcoba')).toBeInTheDocument()
    expect(screen.queryByLabelText('Agregar Brocha de 2"')).toBeNull()

    // "Todas" vuelve a la categoría completa.
    fireEvent.click(screen.getByRole('button', { name: 'Todas' }))
    expect(screen.getByLabelText('Agregar Brocha de 2"')).toBeInTheDocument()
  })

  it('una categoría sin reglas de subcategoría no muestra la segunda fila', () => {
    render(<Harness chipInicial="Herramientas" />)
    expect(screen.getByLabelText('Agregar Martillo')).toBeInTheDocument()
    expect(screen.queryByRole('group', { name: /Subcategorías/ })).toBeNull()
  })

  it('en "Todos" los productos van agrupados por categoría con header y conteo', () => {
    render(<Harness frecuentesIds={new Set([1])} />)
    // Sección Top del mes (frecuentes) + una por categoría, cada una con su conteo.
    expect(screen.getByRole('heading', { name: 'Top productos del mes' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Herramientas' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Pinturas' })).toBeInTheDocument()
  })
})
