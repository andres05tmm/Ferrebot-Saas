import { describe, expect, it } from 'vitest'
import { ventaVariaSchema, zodResolver } from './schemas'

describe('ventaVariaSchema', () => {
  it('coacciona strings de <input> a número y valida OK', () => {
    const r = ventaVariaSchema.safeParse({ descripcion: '  Tornillo  ', cantidad: '3', precio_unitario: '500' })
    expect(r.success).toBe(true)
    if (r.success) {
      expect(r.data).toEqual({ descripcion: 'Tornillo', cantidad: 3, precio_unitario: 500 })
    }
  })

  it('rechaza descripción vacía y cantidades/precios no positivos', () => {
    expect(ventaVariaSchema.safeParse({ descripcion: '', cantidad: '1', precio_unitario: '1' }).success).toBe(false)
    expect(ventaVariaSchema.safeParse({ descripcion: 'x', cantidad: '0', precio_unitario: '1' }).success).toBe(false)
    expect(ventaVariaSchema.safeParse({ descripcion: 'x', cantidad: '1', precio_unitario: '-5' }).success).toBe(false)
  })
})

describe('zodResolver (adaptador zod → react-hook-form)', () => {
  it('devuelve values coaccionados y sin errores cuando valida', async () => {
    const resolver = zodResolver(ventaVariaSchema)
    const out = await resolver({ descripcion: 'Cinta', cantidad: '2', precio_unitario: '1200' }, undefined, {})
    expect(out.errors).toEqual({})
    expect(out.values).toEqual({ descripcion: 'Cinta', cantidad: 2, precio_unitario: 1200 })
  })

  it('mapea los issues de zod a errors por campo cuando falla', async () => {
    const resolver = zodResolver(ventaVariaSchema)
    const out = await resolver({ descripcion: '', cantidad: '0', precio_unitario: '1' }, undefined, {})
    expect(out.values).toEqual({})
    expect(out.errors.descripcion?.message).toBe('La descripción es obligatoria')
    expect(out.errors.cantidad?.message).toBe('La cantidad debe ser mayor a 0')
  })
})
