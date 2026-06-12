/* Tema claro/oscuro (data-tema en <html>): localStorage > preferencia del sistema.
   El primer paint lo resuelve el script inline de index.html; esto maneja el toggle. */

const CLAVE = 'melquiadez-tema'

export function temaActual(raiz = document.documentElement) {
  return raiz.dataset.tema === 'oscuro' ? 'oscuro' : 'claro'
}

export function alternarTema(raiz = document.documentElement) {
  const nuevo = temaActual(raiz) === 'claro' ? 'oscuro' : 'claro'
  raiz.dataset.tema = nuevo
  try {
    localStorage.setItem(CLAVE, nuevo)
  } catch {
    /* almacenamiento bloqueado: el toggle igual funciona en la sesión */
  }
  return nuevo
}
