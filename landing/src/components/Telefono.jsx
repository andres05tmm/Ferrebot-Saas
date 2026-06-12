import { useEffect, useRef, useState } from 'react'
import { VERTICALES } from '@/lib/verticales.js'
import './telefono.css'

/*
 * El teléfono del hero: reproduce la conversación del vertical activo con tiempos
 * de chat reales ("escribiendo…" antes de cada respuesta del agente). Al cambiar
 * el vertical, la conversación arranca de cero. Con prefers-reduced-motion se
 * muestra completa, sin animación.
 */

function horaLocal(extraMin) {
  return new Date(Date.now() + extraMin * 60000).toLocaleTimeString('es-CO', {
    hour: '2-digit',
    minute: '2-digit',
  })
}

export default function Telefono({ vertical }) {
  const datos = VERTICALES[vertical]
  const [mensajes, setMensajes] = useState([])
  const [escribiendo, setEscribiendo] = useState(false)
  const timeouts = useRef([])

  useEffect(() => {
    const reducido =
      typeof matchMedia !== 'undefined' &&
      matchMedia('(prefers-reduced-motion: reduce)').matches

    timeouts.current.forEach(clearTimeout)
    timeouts.current = []
    const guion = datos?.chat ?? []

    if (reducido) {
      setMensajes(guion.map(([quien, texto], i) => ({ quien, texto, hora: horaLocal(i) })))
      setEscribiendo(false)
      return undefined
    }

    setMensajes([])
    setEscribiendo(false)
    let t = 500
    guion.forEach(([quien, texto], i) => {
      if (quien === 'agente') {
        timeouts.current.push(setTimeout(() => setEscribiendo(true), t))
        t += 1000
      }
      timeouts.current.push(
        setTimeout(() => {
          setEscribiendo(false)
          setMensajes((previos) => [...previos, { quien, texto, hora: horaLocal(i) }])
        }, t),
      )
      t += quien === 'cliente' ? 1300 : 1900
    })
    return () => timeouts.current.forEach(clearTimeout)
  }, [vertical, datos])

  if (!datos) return null

  return (
    <div className="telefono" aria-label="Demostración de una conversación de WhatsApp atendida por Melquiadez">
      <div className="chat-top">
        <div className="chat-avatar">{datos.avatar}</div>
        <div className="chat-quien">
          <b>{datos.nombre}</b>
          <span>en línea · responde en segundos</span>
        </div>
      </div>
      <div className="chat-cuerpo">
        {mensajes.map((m, i) => (
          <div key={`${vertical}-${i}`} className={`msj ${m.quien}`}>
            {/* contenido propio y estático (lib/verticales.js), no entrada de usuario */}
            <span dangerouslySetInnerHTML={{ __html: m.texto }} />
            <span className="hora">{m.hora}</span>
          </div>
        ))}
        {escribiendo && (
          <div className="escribiendo" aria-hidden="true"><i /><i /><i /></div>
        )}
      </div>
      <div className="chat-pie">
        <div className="campo">Escribe un mensaje…</div>
        <div className="enviar" aria-hidden="true">
          <svg viewBox="0 0 24 24"><path d="M2 21l21-9L2 3v7l15 2-15 2v7z" /></svg>
        </div>
      </div>
    </div>
  )
}
