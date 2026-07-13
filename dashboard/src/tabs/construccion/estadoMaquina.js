/*
 * estadoMaquina — ÚNICA fuente del estado de máquina → tono del Semáforo + etiqueta (F2.8).
 * Antes el mapa vivía cuadruplicado (TabMaquinas, FichaMaquina, panel/EstadoMaquinas y operación):
 * cuatro copias que podían divergir en label/tono. OCUPADA se rotula "En obra" (más claro para el
 * operador); el VALOR sigue siendo el enum del ORM.
 */
export const ESTADO_MAQUINA = {
  DISPONIBLE:    { tono: 'verde', label: 'Disponible' },
  OCUPADA:       { tono: 'azul',  label: 'En obra' },
  MANTENIMIENTO: { tono: 'ambar', label: 'Mantenimiento' },
  DAÑADA:        { tono: 'rojo',  label: 'Dañada' },
  BAJA:          { tono: 'gris',  label: 'De baja' },
}

export function estadoMaquina(estado) {
  return ESTADO_MAQUINA[estado] || { tono: 'gris', label: estado || '—' }
}

// Pares [valor, label] para selects de estado (la ficha de máquina los consume).
export const ESTADOS_MAQUINA = Object.entries(ESTADO_MAQUINA).map(([v, m]) => [v, m.label])
