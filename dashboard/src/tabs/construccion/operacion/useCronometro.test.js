import { describe, it, expect } from 'vitest'
import { formatearElapsed } from './useCronometro.js'
import { estadoMaquina } from '../estadoMaquina.js'   // única fuente desde F2.8

describe('formatearElapsed', () => {
  it('formatea ms como H:MM:SS con cero-padding', () => {
    expect(formatearElapsed(0)).toBe('0:00:00')
    expect(formatearElapsed(65 * 1000)).toBe('0:01:05')
    expect(formatearElapsed((3600 + 125) * 1000)).toBe('1:02:05')   // 1h 2m 5s
  })

  it('nunca es negativo (reloj hacia atrás → 0)', () => {
    expect(formatearElapsed(-5000)).toBe('0:00:00')
    expect(formatearElapsed(null)).toBe('0:00:00')
  })
})

describe('estadoMaquina', () => {
  it('mapea los estados conocidos a tono + etiqueta', () => {
    expect(estadoMaquina('OCUPADA')).toEqual({ tono: 'azul', label: 'En obra' })
    expect(estadoMaquina('DISPONIBLE').tono).toBe('verde')
    expect(estadoMaquina('MANTENIMIENTO').tono).toBe('ambar')
  })

  it('cae a gris con la etiqueta cruda para un estado desconocido', () => {
    expect(estadoMaquina('RARO')).toEqual({ tono: 'gris', label: 'RARO' })
    expect(estadoMaquina(null).tono).toBe('gris')
  })
})
