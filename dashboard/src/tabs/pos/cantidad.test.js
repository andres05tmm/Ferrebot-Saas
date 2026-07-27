import { describe, expect, it } from 'vitest'
import {
  atajosKg, fraccionQueCasa, paqueteDe, previewMotor, subunidadesDesdePesos, tipoVenta,
} from './cantidad.js'

// Productos de muestra (los campos vienen de ProductoLeer + los nuevos fracciones/unidades_por_paquete).
const PINTURA = {
  id: 1, unidad_medida: 'Unidad', precio_venta: '65000', permite_fraccion: true,
  unidades_por_paquete: null,
  fracciones: [
    { fraccion: '3/4', decimal: '0.75', precio_total: '50000' },
    { fraccion: '1/2', decimal: '0.5', precio_total: '33000' },
    { fraccion: '1/4', decimal: '0.25', precio_total: '17000' },
  ],
}
// Tras 0072 `precio_venta` es el precio de UNA unidad de venta (el gramo, el cm, el kilo) y el del
// envase completo vive en `precio_paquete`. Antes el mismo campo significaba las dos cosas.
const PUNTILLA = {
  id: 2, unidad_medida: 'GRM', precio_venta: '10', unidades_por_paquete: '500',
  nombre_paquete: 'caja', precio_paquete: '5000', fracciones: [],
}
const LIJA = {
  id: 3, unidad_medida: 'Cms', precio_venta: '200', unidades_por_paquete: '100',
  nombre_paquete: 'rollo', precio_paquete: '20000', fracciones: [],
}
const TINTILLA = {
  id: 4, unidad_medida: 'MLT', precio_venta: '26', unidades_por_paquete: '1000',
  nombre_paquete: 'tarro', precio_paquete: '26000', fracciones: [],
}
const ARROZ_KG = {
  id: 5, unidad_medida: 'kg', precio_venta: '4000', unidades_por_paquete: null,
  fracciones: [{ fraccion: '1/2', decimal: '0.5', precio_total: '2500' }],
}
// El caso que rompió en producción: la bolsa vale $100.000 y el kilo suelto $2.500 — dos datos.
const CEMENTO = {
  id: 7, unidad_medida: 'Kg', precio_venta: '2500', unidades_por_paquete: '40',
  nombre_paquete: 'bolsa', precio_paquete: '100000',
  fracciones: [{ fraccion: '1/4', decimal: '0.25', precio_total: '700' }],
}
const NORMAL = { id: 6, unidad_medida: 'unidad', precio_venta: '1200', permite_fraccion: false, unidades_por_paquete: null, fracciones: [] }

describe('tipoVenta — discriminador por unidad_medida / fracciones', () => {
  it('mapea cada tipo por su unidad_medida (case-insensitive)', () => {
    expect(tipoVenta(PUNTILLA)).toBe('gramos')
    expect(tipoVenta(LIJA)).toBe('cm')
    expect(tipoVenta(TINTILLA)).toBe('ml')
    expect(tipoVenta({ ...TINTILLA, unidad_medida: 'ml' })).toBe('ml')
    expect(tipoVenta(ARROZ_KG)).toBe('kg')
    expect(tipoVenta({ ...ARROZ_KG, unidad_medida: 'KGM' })).toBe('kg')
  })
  it('pintura = permite_fraccion CON filas de fracción', () => {
    expect(tipoVenta(PINTURA)).toBe('fraccion')
    expect(tipoVenta({ ...PINTURA, fracciones: [] })).toBeNull()   // sin filas → agrega directo
  })
  it('producto normal → null (agrega directo)', () => {
    expect(tipoVenta(NORMAL)).toBeNull()
  })
})

describe('previewMotor — espeja el motor del backend (fracción → granel → simple)', () => {
  it('pintura: fracción exacta usa el precio bonito; entero cae a simple', () => {
    expect(previewMotor(PINTURA, 0.75)).toBe(50000)         // fila 3/4
    expect(previewMotor(PINTURA, 0.5)).toBe(33000)          // fila 1/2
    expect(previewMotor(PINTURA, 2)).toBe(130000)           // 2 galones simple = 65000*2
  })
  it('granel: precio_venta ya está por sub-unidad, sin dividir por nada', () => {
    expect(previewMotor(PUNTILLA, 500)).toBe(5000)          // caja completa
    expect(previewMotor(PUNTILLA, 250)).toBe(2500)          // media caja
    expect(previewMotor(LIJA, 30)).toBe(6000)               // 30 cm a $200/cm
    expect(previewMotor(TINTILLA, 250)).toBe(6500)          // ¼ tarro
  })
  it('empaque entero: cobra precio_paquete, no el precio del kilo multiplicado', () => {
    // El bug que vio el dueño: la bolsa aparecía a $2.500 (el precio del kilo) en vez de $100.000.
    expect(previewMotor(CEMENTO, 40, { porEmpaque: true })).toBe(100000)
    expect(previewMotor(CEMENTO, 80, { porEmpaque: true })).toBe(200000)   // 2 bolsas
    expect(previewMotor(CEMENTO, 40)).toBe(100000)          // 40 kg sueltos dan lo mismo aquí
    expect(previewMotor(CEMENTO, 3)).toBe(7500)             // 3 kg sueltos
  })
  it('sin precio_paquete no se inventa un precio de empaque', () => {
    const sinPrecio = { ...CEMENTO, precio_paquete: null }
    expect(previewMotor(sinPrecio, 40, { porEmpaque: true })).toBe(100000)  // cae a simple
  })
  it('kg: ½ con fila bonita; cantidad sin fila cae a simple (precio por kilo)', () => {
    expect(previewMotor(ARROZ_KG, 0.5)).toBe(2500)          // fila 1/2 (no 4000*0.5=2000)
    expect(previewMotor(ARROZ_KG, 2)).toBe(8000)            // simple 4000*2
    expect(previewMotor(ARROZ_KG, 1.5)).toBe(6000)          // 1.5 no casa fila → simple
  })
})

describe('subunidadesDesdePesos — modo "$ pesos" del granel', () => {
  it('convierte un monto a sub-unidades (redondeo a 1 decimal)', () => {
    // Puntilla $5.000/500 g = $10/g. $2000 → 200 g. El total round-trips: 200 g * $10 = $2000.
    expect(subunidadesDesdePesos(PUNTILLA, 2000)).toBe(200)
    expect(subunidadesDesdePesos(TINTILLA, 6500)).toBe(250)   // $26/ml → 250 ml
    expect(subunidadesDesdePesos(PUNTILLA, 0)).toBe(0)
  })
})

describe('helpers de datos', () => {
  it('paqueteDe lee unidades_por_paquete del backend', () => {
    expect(paqueteDe(PUNTILLA)).toBe(500)
    expect(paqueteDe(TINTILLA)).toBe(1000)
    expect(paqueteDe(NORMAL)).toBeNull()
  })
  it('fraccionQueCasa encuentra la fila por decimal con tolerancia', () => {
    expect(fraccionQueCasa(ARROZ_KG, 0.5).precio_total).toBe('2500')
    expect(fraccionQueCasa(ARROZ_KG, 2)).toBeNull()
  })
})

describe('atajosKg — los kilos rápidos salen del producto, no de una lista fija', () => {
  it('ofrece las fracciones que el dueño configuró y después kilos enteros', () => {
    // El amoniaco se vende por ¼ y ½ kilo: esos botones tienen que existir. Antes la lista era fija
    // [½, 1, 1½, 2, 2½, 3] y el ¼ no aparecía por ningún lado.
    const AMONIACO = {
      id: 8, unidad_medida: 'Kg', precio_venta: '13000',
      fracciones: [
        { fraccion: '1/2', decimal: '0.5', precio_total: '7000' },
        { fraccion: '1/4', decimal: '0.25', precio_total: '4000' },
      ],
    }
    expect(atajosKg(AMONIACO).map((a) => a.cantidad)).toEqual([0.25, 0.5, 1, 2, 3])
    expect(atajosKg(AMONIACO)[0].etiqueta).toBe('1/4 kg')
  })
  it('un producto por kilo sin fracciones cae a kilos enteros', () => {
    expect(atajosKg({ fracciones: [] }).map((a) => a.cantidad)).toEqual([1, 2, 3])
  })
})
