/*
 * BadgeFiscal — estado fiscal de una venta (documento + estado DIAN) para el dashboard.
 * El backend compone `venta.fiscal` (modules/facturacion → EstadoFiscalVenta) solo si el tenant tiene
 * capacidad fiscal; si la venta no tiene documento, `fiscal` es null y este componente NO renderiza nada.
 */
import { Badge } from '@/components/ui/badge.jsx'

// estado DIAN → variante visual del Badge (badge.jsx): aceptada=verde, pendiente=ámbar,
// rechazada/error=rojo, anulada=gris/neutral.
const VARIANTE_ESTADO = {
  aceptada: 'success',
  pendiente: 'warning',
  enviada: 'warning',
  rechazada: 'danger',
  error: 'danger',
  anulada: 'default',
}

// tipo de documento (enum fe_tipo) → etiqueta corta.
const ETIQUETA_TIPO = {
  pos: 'POS',
  factura: 'Factura',
  nota_credito: 'N. Crédito',
  nota_debito: 'N. Débito',
  documento_soporte: 'Doc. Soporte',
}

export function etiquetaTipo(tipo) {
  return ETIQUETA_TIPO[tipo] || (tipo ? String(tipo) : 'Documento')
}

// El identificador único es CUDE para el POS y CUFE para la factura/notas (mismo campo en BD: `cufe`).
export function etiquetaIdentificador(tipo) {
  return tipo === 'pos' ? 'CUDE' : 'CUFE'
}

export default function BadgeFiscal({ fiscal, className }) {
  if (!fiscal) return null
  const variante = VARIANTE_ESTADO[fiscal.estado] || 'default'
  const doc = etiquetaTipo(fiscal.tipo)
  return (
    <Badge variant={variante} className={className} title={`${doc} · ${fiscal.estado}${fiscal.cufe ? ` · ${fiscal.cufe}` : ''}`}>
      {doc} · {fiscal.estado}
    </Badge>
  )
}
