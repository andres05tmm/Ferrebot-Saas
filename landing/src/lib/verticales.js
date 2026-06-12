/*
 * Verticales de la landing: la palabra del titular, la conversación del teléfono,
 * el acento de color (via data-vertical, ver marca/tokens.css) y la demo en vivo.
 * Los negocios son los ficticios de los tenants demo (mismos nombres que en el plan).
 * Cada guion refleja herramientas REALES del producto.
 */

export const DOMINIO_DEMO = 'melquiadez.com'

export const VERTICALES = {
  odontologia: {
    palabra: 'clínica',
    nombre: 'Clínica Dental Aurora',
    avatar: 'A',
    etiqueta: 'Clínica',
    pie: 'Agenda y reconfirma citas',
    demo: 'clinica-demo',
    foto: 'https://images.unsplash.com/photo-1629909613654-28e377c37b09?auto=format&fit=crop&w=1200&q=80',
    chat: [
      ['cliente', 'Hola, necesito una limpieza dental esta semana'],
      ['agente', 'Hola! Para <b>limpieza dental</b> (40 min) tengo:\njueves <b>10:00 am</b> · jueves <b>3:30 pm</b> · viernes <b>9:00 am</b>'],
      ['cliente', 'El jueves a las 10. Soy Laura Méndez'],
      ['agente', 'Quedó agendada, Laura ✅ Cita <b>#892</b> el jueves 10:00 am con la Dra. Pérez.\nTe escribo el miércoles para confirmar.'],
      ['cliente', '(miércoles) ¿Sigue en pie mi cita?'],
      ['agente', 'Sí! Mañana <b>10:00 am</b>. ¿Confirmas tu asistencia?'],
      ['cliente', 'Confirmo ✔️'],
      ['agente', 'Reconfirmada ✅ La doctora ya la ve en su calendario.'],
    ],
  },
  restaurante: {
    palabra: 'restaurante',
    nombre: 'Brasa',
    avatar: 'B',
    etiqueta: 'Restaurante',
    pie: 'Toma pedidos a domicilio',
    demo: 'restaurante-demo',
    foto: 'https://images.unsplash.com/photo-1568901346375-23c9450c58cd?auto=format&fit=crop&w=1200&q=80',
    chat: [
      ['cliente', 'Me mandas 2 hamburguesas dobles y una gaseosa 🍔'],
      ['agente', 'Va! 🛒 2× <b>Doble carne</b> $36.000 + 1× <b>Gaseosa</b> $5.000 = <b>$41.000</b>\n¿A qué dirección y barrio te lo llevamos?'],
      ['cliente', 'Cra 70 #45-12, barrio Manga'],
      ['agente', 'Pedido <b>#118</b> confirmado ✅ Domicilio a Manga: $4.000.\nTotal <b>$45.000</b> · llega en ~40 min\n👉 paga aquí: brasa.co/pagar/118'],
      ['cliente', '¿Cómo va mi pedido?'],
      ['agente', 'Ya va <b>en camino</b>! 🛵 Salió hace 5 minutos.'],
    ],
  },
  barberia: {
    palabra: 'barbería',
    nombre: 'Barbería El Patio',
    avatar: 'P',
    etiqueta: 'Barbería',
    pie: 'Turnos y recordatorios',
    demo: 'barberia-demo',
    foto: 'https://images.unsplash.com/photo-1585747860715-2ba37e788b70?auto=format&fit=crop&w=1200&q=80',
    chat: [
      ['cliente', '¿Tienen turno hoy por la tarde? 💈'],
      ['agente', 'Claro! Para corte clásico hoy tengo:\n<b>4:30 pm</b> · <b>5:15 pm</b> · <b>6:00 pm</b>\n¿Cuál te sirve?'],
      ['cliente', 'El de 5:15, a nombre de Jorge'],
      ['agente', 'Listo Jorge ✅ Cita <b>#214</b> hoy 5:15 pm con Maicol.'],
      ['cliente', '¿Hacen diseño de barba?'],
      ['agente', 'Sí señor: <b>perfilado de barba $18.000</b> (20 min). ¿Te lo agrego al mismo turno?'],
    ],
  },
  hotel: {
    palabra: 'hotel',
    nombre: 'Hotel Brisa',
    avatar: 'B',
    etiqueta: 'Hotel',
    pie: 'Reservas con anticipo',
    demo: 'hotel-demo',
    foto: 'https://images.unsplash.com/photo-1571896349842-33c89424de2d?auto=format&fit=crop&w=1200&q=80',
    chat: [
      ['cliente', 'Hola! ¿Tienen habitación del viernes al lunes? Somos 2 🌴'],
      ['agente', 'Bienvenidos! Para <b>3 noches</b> desde el viernes tengo:\n<b>Vista al mar</b> · $280.000/noche → $840.000\n<b>Estándar</b> · $190.000/noche → $570.000'],
      ['cliente', 'La de vista al mar 😍 a nombre de Camila Rojas'],
      ['agente', 'Excelente elección ✅ Reserva <b>#77</b>: check-in viernes 3:00 pm.\nAnticipo del 50% (<b>$420.000</b>) para confirmar:\n👉 pago seguro: brisa.co/pagar/77'],
      ['cliente', 'Pagado ✅'],
      ['agente', 'Anticipo recibido! Reserva <b>confirmada</b> 🥂 ¿Les separo traslado desde el aeropuerto?'],
    ],
  },
}

export const ORDEN = ['odontologia', 'restaurante', 'barberia', 'hotel']
export const ROTACION_MS = 14000

/** Índice del siguiente vertical en la rotación automática (cíclico). */
export function siguienteIndice(indice) {
  return (indice + 1) % ORDEN.length
}

/** Palabras del titular en el orden de rotación (para TextRotate). */
export const PALABRAS = ORDEN.map((v) => VERTICALES[v].palabra)

/** Retematiza la página: el acento por vertical vive en CSS (marca/tokens.css). */
export function aplicarVertical(vertical, raiz = document.documentElement) {
  if (!VERTICALES[vertical]) return false
  raiz.dataset.vertical = vertical
  return true
}

/** URL de la demo en vivo del vertical ({slug}-demo.melquiadez.com). */
export function urlDemo(vertical) {
  const datos = VERTICALES[vertical]
  return datos ? `https://${datos.demo}.${DOMINIO_DEMO}` : null
}
