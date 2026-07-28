/*
 * El libro de ventas en PDF (jspdf, ya instalado — sin dependencias nuevas ni archivos en el
 * servidor). Se arma en el navegador con las mismas filas que muestra la tabla.
 *
 * Por qué acá y no en el backend: generar PDF del lado del servidor pide `reportlab` o
 * `weasyprint`, que pesan y hay que mantener. Mientras el PDF nazca de un clic en una pantalla que
 * ya tiene los datos, el navegador alcanza. Si algún día hay que mandarlo por WhatsApp desde el
 * worker —sin navegador— ahí sí se agrega la dependencia.
 *
 * ⚠️ `totalPeriodo` se RECIBE, igual que en el Excel. Sumar los renglones daría distinto en centavos
 * cuando hay fracciones (el POS guarda `precio_unitario = total / cantidad`), y el PDF terminaría
 * contradiciendo al número grande de la misma pantalla.
 */
import { jsPDF } from 'jspdf'

const cop = (v) => '$' + Math.round(Number(v || 0)).toLocaleString('es-CO')
const dmy = (iso) => String(iso).slice(0, 10).split('-').reverse().join('/')
const HORA_CO = { hour: '2-digit', minute: '2-digit', timeZone: 'America/Bogota' }
const cant = (v) => Number(v).toLocaleString('es-CO', { maximumFractionDigits: 3 })

// 'mixto' no es un método de pago: se expande a los reales, como en la pantalla y en el Excel.
const metodoTexto = (f) =>
  f.metodo_pago === 'mixto' && f.pagos?.length
    ? f.pagos.map(p => p.metodo).join(' + ')
    : f.metodo_pago

// Se recorta con ellipsis en vez de dejar que jsPDF pise la columna siguiente: un PDF con texto
// encimado se lee peor que uno con un nombre cortado.
function recortar(doc, texto, ancho) {
  let t = String(texto ?? '')
  if (doc.getTextWidth(t) <= ancho) return t
  while (t.length > 1 && doc.getTextWidth(t + '…') > ancho) t = t.slice(0, -1)
  return t + '…'
}

export function descargarHistorialPdf({ filas, desde, hasta, totalPeriodo, numVentas, empresa }) {
  const doc = new jsPDF({ unit: 'pt', format: 'letter', orientation: 'landscape' })
  const margen = 32
  const ancho = 792 - margen * 2
  let y = margen

  doc.setFontSize(15)
  doc.text(empresa ? `${empresa} — Ventas` : 'Ventas', margen, y)
  y += 18
  doc.setFontSize(10)
  doc.text(`Del ${dmy(desde)} al ${dmy(hasta)}`, margen, y)
  y += 14
  doc.setFontSize(11)
  doc.setFont(undefined, 'bold')
  doc.text(`Total del período: ${cop(totalPeriodo)}`, margen, y)
  doc.setFont(undefined, 'normal')
  doc.setFontSize(9)
  doc.text(`${numVentas ?? 0} venta(s) · no incluye anuladas`, margen + 200, y)
  y += 16

  // (x, ancho útil, alineación). El orden es el de la pantalla: el que busca una columna la
  // encuentra donde la vio.
  const COLS = [
    ['Fecha', margen, 52, 'left'],
    ['Hora', margen + 56, 34, 'left'],
    ['Producto', margen + 94, 168, 'left'],
    ['Cliente', margen + 266, 120, 'left'],
    ['Cant.', margen + 404, 40, 'right'],
    ['V. unit.', margen + 460, 60, 'right'],
    ['Método', margen + 528, 96, 'left'],
    ['Vendedor', margen + 628, 70, 'left'],
    ['Total', margen + 728, 60, 'right'],
  ]
  const encabezado = () => {
    doc.setFont(undefined, 'bold')
    doc.setFontSize(8)
    for (const [titulo, x, w, alin] of COLS) {
      doc.text(titulo, alin === 'right' ? x + w : x, y, { align: alin })
    }
    doc.setFont(undefined, 'normal')
    y += 5
    doc.line(margen, y, margen + ancho, y)
    y += 11
  }
  encabezado()

  doc.setFontSize(8)
  for (const f of filas) {
    if (y > 545) { doc.addPage(); y = margen; encabezado(); doc.setFontSize(8) }
    const local = new Date(f.fecha)
    const valores = [
      local.toLocaleDateString('en-CA', { timeZone: 'America/Bogota' }).split('-').reverse().join('/'),
      local.toLocaleTimeString('es-CO', HORA_CO),
      f.producto,
      f.cliente,
      cant(f.cantidad),
      cop(f.precio_unitario),
      metodoTexto(f),
      f.vendedor,
      cop(f.total_linea),
    ]
    // Las anuladas se marcan en gris: siguen en el libro, pero no cuentan en el total de arriba.
    if (f.estado === 'anulada') doc.setTextColor(150)
    valores.forEach((valor, i) => {
      const [, x, w, alin] = COLS[i]
      const texto = recortar(doc, valor, w)
      doc.text(texto, alin === 'right' ? x + w : x, y, { align: alin })
    })
    if (f.estado === 'anulada') {
      doc.text('anulada', margen + 94 + 172, y)
      doc.setTextColor(0)
    }
    y += 12
  }

  doc.line(margen, y, margen + ancho, y)
  y += 13
  doc.setFont(undefined, 'bold')
  doc.setFontSize(9)
  doc.text('TOTAL DEL PERÍODO', margen + 528, y)
  doc.text(cop(totalPeriodo), margen + 728 + 60, y, { align: 'right' })

  doc.save(`ventas_${desde}_${hasta}.pdf`)
}
