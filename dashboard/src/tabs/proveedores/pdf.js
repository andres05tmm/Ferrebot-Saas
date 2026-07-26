/*
 * Estado de cuenta del proveedor en PDF (jspdf, ya instalado — sin dependencias nuevas ni archivos
 * guardados en el servidor).
 *
 * La pantalla muestra 6 meses; este PDF se lleva TODO el histórico, que es lo que el dueño quiere
 * archivar cuando los movimientos pasan esa ventana. Se arma en el navegador y se descarga.
 */
import { jsPDF } from 'jspdf'

const cop = (v) => '$' + Math.round(Number(v || 0)).toLocaleString('es-CO')
const fecha = (iso) => (iso ? String(iso).slice(0, 10).split('-').reverse().join('/') : '')

const MEDIO = {
  caja: 'efectivo de caja',
  efectivo_externo: 'efectivo guardado',
  banco: 'transferencia',
  mixto: 'pago mixto',
}

export function descargarEstadoCuenta(cuenta) {
  const doc = new jsPDF({ unit: 'pt', format: 'letter' })
  const margen = 40
  let y = margen

  doc.setFontSize(16)
  doc.text('Estado de cuenta', margen, y)
  y += 20
  doc.setFontSize(11)
  doc.text(cuenta.proveedor_nombre || '', margen, y)
  y += 14
  doc.setFontSize(9)
  doc.text(`Periodo: ${fecha(cuenta.desde)} a ${fecha(cuenta.hasta)}`, margen, y)
  y += 12
  doc.text(
    `Saldo pendiente: ${cop(cuenta.saldo_pendiente)}   ·   Vencido: ${cop(cuenta.vencido)}`,
    margen, y,
  )
  y += 18

  const cols = [margen, margen + 70, margen + 300, margen + 380, margen + 460]
  const encabezado = () => {
    doc.setFont(undefined, 'bold')
    doc.text('Fecha', cols[0], y)
    doc.text('Concepto', cols[1], y)
    doc.text('Debe', cols[2], y, { align: 'right' })
    doc.text('Pagado', cols[3], y, { align: 'right' })
    doc.text('Saldo', cols[4], y, { align: 'right' })
    doc.setFont(undefined, 'normal')
    y += 6
    doc.line(margen, y, margen + 480, y)
    y += 12
  }
  encabezado()

  doc.text(fecha(cuenta.desde), cols[0], y)
  doc.text('Saldo anterior', cols[1], y)
  doc.text(cop(cuenta.saldo_anterior), cols[4], y, { align: 'right' })
  y += 14

  for (const m of cuenta.movimientos || []) {
    if (y > 720) {                       // salto de página conservando el encabezado
      doc.addPage()
      y = margen
      encabezado()
    }
    const concepto = `${m.tipo === 'factura' ? 'Factura' : 'Abono'} ${m.referencia}`
      + (m.medio ? ` (${MEDIO[m.medio] || m.medio})` : '')
    doc.text(fecha(m.fecha), cols[0], y)
    doc.text(concepto.slice(0, 45), cols[1], y)
    if (Number(m.cargo) > 0) doc.text(cop(m.cargo), cols[2], y, { align: 'right' })
    if (Number(m.abono) > 0) doc.text(cop(m.abono), cols[3], y, { align: 'right' })
    doc.text(cop(m.saldo), cols[4], y, { align: 'right' })
    y += 14
  }

  const nombre = (cuenta.proveedor_nombre || 'proveedor').replace(/[^\w-]+/g, '-').toLowerCase()
  doc.save(`estado-cuenta-${nombre}-${fecha(cuenta.hasta).replace(/\//g, '-')}.pdf`)
}
