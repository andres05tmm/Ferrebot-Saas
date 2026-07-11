/*
 * estadoMaquina — mapa único estado de máquina → tono del Semáforo + etiqueta, para las pantallas de
 * operación en vivo. (El mismo mapa vive, por ahora, en TabMaquinas/FichaMaquina/panel EstadoMaquinas;
 * este módulo lo centraliza para la familia de operación y es el candidato natural a unificarlos.)
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
