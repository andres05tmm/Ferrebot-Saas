/*
 * LineaCarrito — el selector bulto ⇄ suelto (0072).
 *
 * El cemento se vende de dos formas contra el mismo inventario: el bulto de 50 kg a precio fijo o
 * el kilo menudeado. La cantidad de la línea SIEMPRE va en la unidad de venta (50 kg, no "1 bulto"),
 * así que los multiplicadores tienen que moverse de a bultos cuando se está vendiendo por bulto —
 * si setearan 2 kg el backend rechazaría la línea por no ser múltiplo del empaque.
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'

import LineaCarrito from './LineaCarrito.jsx'

const CEMENTO = {
  key: 'k1', producto_id: 1, nombre: 'Cemento Gris', cantidad: 50, varia: false,
  precio_normal: 1500, unidad_medida: 'Kg',
  contenido_paquete: 50, precio_paquete: 28000, nombre_paquete: 'bulto', por_empaque: true,
}
const MARTILLO = {
  key: 'k2', producto_id: 2, nombre: 'Martillo', cantidad: 1, varia: false,
  precio_normal: 11900, unidad_medida: 'Unidad',
}

function pintar(it, props = {}) {
  return render(
    <LineaCarrito
      it={it} precio={{ total: 28000, precio_unitario: 560, regla: 'empaque' }}
      onCantidad={() => {}} onQuitar={() => {}} onEspecial={() => {}} onEmpaque={() => {}}
      {...props}
    />,
  )
}

afterEach(cleanup)

describe('LineaCarrito — empaque', () => {
  it('ofrece bulto y suelto cuando el producto tiene tamaño y precio de empaque', () => {
    pintar(CEMENTO)
    expect(screen.getByText('bulto de 50')).toBeTruthy()
    expect(screen.getByText(/Suelto/)).toBeTruthy()
    expect(screen.getByText(/1 × \$\s?28[.,]000/)).toBeTruthy()   // 1 bulto a su precio
  })

  it('no ofrece nada de eso en un producto que solo se vende por unidad', () => {
    pintar(MARTILLO)
    expect(screen.queryByLabelText(/Presentación/)).toBeNull()
  })

  it('los multiplicadores mueven BULTOS cuando se vende por bulto', () => {
    const onCantidad = vi.fn()
    pintar(CEMENTO, { onCantidad })
    fireEvent.click(screen.getByLabelText('×2 de Cemento Gris'))
    expect(onCantidad).toHaveBeenCalledWith('100')                // 2 bultos = 100 kg, no 2 kg
  })

  it('vendiendo suelto los multiplicadores vuelven a ser unidades sueltas', () => {
    const onCantidad = vi.fn()
    pintar({ ...CEMENTO, por_empaque: false, cantidad: 3 }, { onCantidad })
    fireEvent.click(screen.getByLabelText('×2 de Cemento Gris'))
    expect(onCantidad).toHaveBeenCalledWith('2')
  })
})
