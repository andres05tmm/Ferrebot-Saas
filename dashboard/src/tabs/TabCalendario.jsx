/*
 * TabCalendario — punto de montaje de la página /calendario (vertical construcción). Wrapper fino, como
 * el resto de TabX: delega todo en CalendarioObra (contenedor con estado, fetch y realtime).
 */
import CalendarioObra from './construccion/calendario/CalendarioObra.jsx'

export default function TabCalendario() {
  return <CalendarioObra />
}
