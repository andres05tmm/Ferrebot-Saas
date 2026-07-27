/*
 * ModalCantidad — los dos bugs que reportó el dueño al vender polvos (0072).
 *
 * 1. La bolsa completa mostraba el precio del KILO ($2.500 para una bolsa de $100.000). El modal
 *    seguía con el motor viejo, que dividía `precio_venta` por el tamaño del empaque; desde 0072 el
 *    precio del bulto es un dato aparte (`precio_paquete`) y no se deduce del kilo.
 * 2. El amoniaco se vende por ¼ de kilo y ese botón no existía: los accesos rápidos eran una lista
 *    fija [½, 1, 1½ …] que ignoraba las fracciones configuradas del producto.
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'

import ModalCantidad from './ModalCantidad.jsx'

const CEMENTO = {
  id: 7, nombre: 'Cemento Blanco Argos', unidad_medida: 'Kg', precio_venta: '2500',
  unidades_por_paquete: '40', nombre_paquete: 'bolsa', precio_paquete: '100000',
  permite_fraccion: false, fracciones: [],
}
const AMONIACO = {
  id: 8, nombre: 'Amoniaco sin Olor (Base)', unidad_medida: 'Kg', precio_venta: '13000',
  unidades_por_paquete: null, nombre_paquete: null, precio_paquete: null,
  permite_fraccion: true,
  fracciones: [
    { fraccion: '1/2', decimal: '0.5', precio_total: '7000' },
    { fraccion: '1/4', decimal: '0.25', precio_total: '4000' },
  ],
}

afterEach(cleanup)

describe('ModalCantidad — empaque entero', () => {
  it('la bolsa completa muestra SU precio, no el del kilo', () => {
    render(<ModalCantidad prod={CEMENTO} onCerrar={() => {}} onConfirmar={() => {}} />)
    const boton = screen.getByRole('button', { name: /bolsa completa \(40 kg\)/i })
    expect(boton.textContent).toContain('100.000')
    expect(boton.textContent).not.toContain('2.500')
  })

  it('el subtítulo dice el precio del kilo Y el de la bolsa', () => {
    render(<ModalCantidad prod={CEMENTO} onCerrar={() => {}} onConfirmar={() => {}} />)
    const desc = document.getElementById('cant-desc').textContent
    expect(desc).toContain('2.500')       // el kilo suelto
    expect(desc).toContain('100.000')     // la bolsa
  })

  it('confirmar la bolsa manda porEmpaque y la cantidad en kilos', () => {
    const onConfirmar = vi.fn()
    render(<ModalCantidad prod={CEMENTO} onCerrar={() => {}} onConfirmar={onConfirmar} />)
    fireEvent.click(screen.getByRole('button', { name: /bolsa completa/i }))
    fireEvent.click(screen.getByRole('button', { name: 'Agregar al carrito' }))
    expect(onConfirmar).toHaveBeenCalledWith(
      expect.objectContaining({ cantidad: 40, porEmpaque: true }),
    )
  })

  it('tocar la bolsa dos veces son dos bolsas', () => {
    const onConfirmar = vi.fn()
    render(<ModalCantidad prod={CEMENTO} onCerrar={() => {}} onConfirmar={onConfirmar} />)
    const boton = screen.getByRole('button', { name: /bolsa completa/i })
    fireEvent.click(boton)
    fireEvent.click(boton)
    fireEvent.click(screen.getByRole('button', { name: 'Agregar al carrito' }))
    expect(onConfirmar).toHaveBeenCalledWith(expect.objectContaining({ cantidad: 80, porEmpaque: true }))
  })

  it('escribir kilos a mano apaga el precio de bolsa', () => {
    const onConfirmar = vi.fn()
    render(<ModalCantidad prod={CEMENTO} onCerrar={() => {}} onConfirmar={onConfirmar} />)
    fireEvent.click(screen.getByRole('button', { name: /bolsa completa/i }))
    fireEvent.change(screen.getByLabelText('Cantidad en kilos'), { target: { value: '3' } })
    fireEvent.click(screen.getByRole('button', { name: 'Agregar al carrito' }))
    expect(onConfirmar).toHaveBeenCalledWith(expect.objectContaining({ cantidad: 3, porEmpaque: false }))
  })
})

describe('ModalCantidad — fracciones de kilo', () => {
  it('ofrece el ¼ de kilo que el dueño configuró, con su precio', () => {
    render(<ModalCantidad prod={AMONIACO} onCerrar={() => {}} onConfirmar={() => {}} />)
    const cuarto = screen.getByRole('button', { name: /1\/4 kg/ })
    expect(cuarto.textContent).toContain('4.000')
  })

  it('el ¼ entra al carrito como 0.25 kg', () => {
    const onConfirmar = vi.fn()
    render(<ModalCantidad prod={AMONIACO} onCerrar={() => {}} onConfirmar={onConfirmar} />)
    fireEvent.click(screen.getByRole('button', { name: /1\/4 kg/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Agregar al carrito' }))
    expect(onConfirmar).toHaveBeenCalledWith(
      expect.objectContaining({ cantidad: 0.25, porEmpaque: false }),
    )
  })

  it('un producto sin empaque no ofrece botón de bolsa', () => {
    render(<ModalCantidad prod={AMONIACO} onCerrar={() => {}} onConfirmar={() => {}} />)
    expect(screen.queryByRole('button', { name: /completa/i })).toBeNull()
  })
})
