/*
 * usePosCatalogo — el catálogo COMPLETO del tenant en memoria del navegador (reforma grilla híbrida).
 *
 * Carga paginada de GET /productos?activo=true (limite máx del backend = 200) hasta la página
 * incompleta, con TOPE de seguridad: un tenant white-label con un catálogo enorme no debe tumbar el
 * navegador — al alcanzarlo se marca `parcial: true` y el POS degrada la búsqueda a server-first.
 *
 * Frescura: recarga ante `inventario_actualizado` (alta/edición/borrado de producto, incluye precios)
 * y `reconnected`, con debounce (~2s) para colapsar ráfagas. NO se suscribe a `venta_registrada`: la
 * grilla no muestra stock y cada venta dispararía una recarga inútil. Red de seguridad: aunque el
 * catálogo quede viejo, el precio real de cada línea viene del servidor al agregar (server-authoritative)
 * y el POST /ventas recalcula — un catálogo stale no puede corromper una venta.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { apiJson } from '@/lib/api'
import { useRealtimeEvent } from '@/components/RealtimeProvider.jsx'

const PAGINA = 200          // límite máximo del backend (modules/inventario/router.py)
const TOPE = 3000           // tope de seguridad client-side (white-label)
const DEBOUNCE_RECARGA_MS = 2000

export default function usePosCatalogo() {
  const [productos, setProductos] = useState([])
  const [categorias, setCategorias] = useState([])
  const [cargando, setCargando] = useState(true)
  const [parcial, setParcial] = useState(false)
  const timerRef = useRef(null)
  const cargandoRef = useRef(false)

  const cargar = useCallback(async () => {
    if (cargandoRef.current) return
    cargandoRef.current = true
    setCargando(true)
    try {
      const todos = []
      let offset = 0
      for (;;) {
        const pagina = await apiJson(`/productos?activo=true&limite=${PAGINA}&offset=${offset}`)
        const lista = Array.isArray(pagina) ? pagina : []
        todos.push(...lista)
        if (lista.length < PAGINA || todos.length >= TOPE) {
          setParcial(lista.length === PAGINA && todos.length >= TOPE)
          break
        }
        offset += PAGINA
      }
      setProductos(todos)
      setCategorias([...new Set(todos.map(p => p.categoria).filter(Boolean))].sort())
    } catch {
      // Sin catálogo local (red caída al montar): el POS degrada a la búsqueda del servidor.
      setParcial(true)
    } finally {
      cargandoRef.current = false
      setCargando(false)
    }
  }, [])

  useEffect(() => { cargar() }, [cargar])

  // Recarga con debounce ante cambios de catálogo (patrón TabInventario).
  useRealtimeEvent(['inventario_actualizado', 'reconnected'], () => {
    if (timerRef.current) clearTimeout(timerRef.current)
    timerRef.current = setTimeout(cargar, DEBOUNCE_RECARGA_MS)
  })
  useEffect(() => () => { if (timerRef.current) clearTimeout(timerRef.current) }, [])

  return { productos, categorias, cargando, parcial, recargar: cargar }
}
