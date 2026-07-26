import { describe, expect, it } from 'vitest'
import { cn } from './utils.js'

describe('cn — merge de clases', () => {
  it('la escala semántica es TAMAÑO, no color: no se come el color del texto', () => {
    // Sin enseñarle la escala, tailwind-merge tomaba `text-caption` por un color y descartaba
    // `text-destructive` — el badge de factura rechazada se quedaba sin rojo.
    expect(cn('text-destructive', 'text-caption')).toContain('text-destructive')
    expect(cn('text-destructive', 'text-caption')).toContain('text-caption')
    for (const clase of ['text-micro', 'text-meta', 'text-body-sm']) {
      expect(cn('text-success', clase).split(' ').sort()).toEqual(['text-success', clase].sort())
    }
  })

  it('dos tamaños de la escala sí compiten entre sí (gana el último)', () => {
    expect(cn('text-caption', 'text-body-sm')).toBe('text-body-sm')
    expect(cn('text-body-sm', 'text-lg')).toBe('text-lg')
  })
})
