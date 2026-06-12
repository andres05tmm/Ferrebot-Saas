import { describe, expect, it } from 'vitest'
import { alternarTema, temaActual } from './tema.js'

describe('tema claro/oscuro', () => {
  it('alterna y persiste en localStorage', () => {
    const raiz = document.createElement('html')
    raiz.dataset.tema = 'claro'
    expect(alternarTema(raiz)).toBe('oscuro')
    expect(raiz.dataset.tema).toBe('oscuro')
    expect(localStorage.getItem('melquiadez-tema')).toBe('oscuro')
    expect(alternarTema(raiz)).toBe('claro')
    expect(temaActual(raiz)).toBe('claro')
  })
})
