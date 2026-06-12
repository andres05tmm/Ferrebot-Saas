import { describe, expect, it } from 'vitest'
import {
  ORDEN,
  PALABRAS,
  VERTICALES,
  aplicarVertical,
  siguienteIndice,
  urlDemo,
} from './verticales.js'

describe('rotación de verticales', () => {
  it('recorre los 4 verticales en ciclo', () => {
    let i = 0
    const visitados = [ORDEN[i]]
    for (let paso = 0; paso < ORDEN.length; paso++) {
      i = siguienteIndice(i)
      visitados.push(ORDEN[i])
    }
    expect(visitados).toEqual([...ORDEN, ORDEN[0]])
  })

  it('las palabras del titular siguen el orden de rotación', () => {
    expect(PALABRAS).toEqual(ORDEN.map((v) => VERTICALES[v].palabra))
    expect(PALABRAS).toContain('clínica')
    expect(PALABRAS).toContain('barbería')
  })
})

describe('retematización (data-vertical)', () => {
  it('aplica el vertical en la raíz', () => {
    const raiz = document.createElement('html')
    expect(aplicarVertical('barberia', raiz)).toBe(true)
    expect(raiz.dataset.vertical).toBe('barberia')
  })

  it('rechaza un vertical desconocido sin tocar la raíz', () => {
    const raiz = document.createElement('html')
    raiz.dataset.vertical = 'hotel'
    expect(aplicarVertical('panaderia', raiz)).toBe(false)
    expect(raiz.dataset.vertical).toBe('hotel')
  })
})

describe('demos en vivo', () => {
  it('cada vertical linkea a su subdominio {slug}-demo.melquiadez.com', () => {
    expect(urlDemo('odontologia')).toBe('https://clinica-demo.melquiadez.com')
    expect(urlDemo('hotel')).toBe('https://hotel-demo.melquiadez.com')
    expect(urlDemo('nope')).toBeNull()
  })

  it('cada guion arranca con el cliente y responde el agente', () => {
    for (const v of ORDEN) {
      const chat = VERTICALES[v].chat
      expect(chat[0][0]).toBe('cliente')
      expect(chat.some(([quien]) => quien === 'agente')).toBe(true)
    }
  })
})
