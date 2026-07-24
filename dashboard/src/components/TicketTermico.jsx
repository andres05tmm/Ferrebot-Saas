/*
 * TicketTermico — fallback de impresión por NAVEGADOR (ADR 0033 D1.c).
 *
 * Renderiza el payload determinista de un trabajo de impresión (R1: comanda | precuenta |
 * comprobante) como ticket térmico en HTML, con ancho fijo de 80mm o 58mm y tipografía mono.
 * Para quien no ha instalado el agente local: `imprimirTicket()` abre la vista y llama
 * window.print() — el diálogo del navegador es el precio del fallback.
 *
 * Propina Ley 1935/2018 en la precuenta: SUGERIDA (10%), voluntaria, JAMÁS sumada al total.
 */

const cop = (v) => '$' + Math.round(Number(v)).toLocaleString('es-CO')

const ANCHOS = { 80: '80mm', 58: '58mm' }

function Comanda({ payload }) {
  return (
    <>
      <div className="tt-titulo">{(payload.zona || 'COCINA').toUpperCase()}</div>
      <div className="tt-sep" />
      <div className="tt-negrita">{payload.cliente || (payload.origen || '').toUpperCase()}</div>
      {payload.cliente && payload.origen ? <div>({payload.origen.toUpperCase()})</div> : null}
      {payload.pedido_id || payload.hora
        ? <div>{[payload.pedido_id ? `Pedido #${payload.pedido_id}` : '', payload.hora || ''].filter(Boolean).join(' · ')}</div>
        : null}
      <div className="tt-sep" />
      {(payload.items || []).map((it, i) => (
        <div key={i}>
          <div className="tt-item">{it.cantidad} x {it.nombre}</div>
          {(it.modificadores || []).map((m, j) => (
            <div key={j} className="tt-mod">&gt;&gt; {m.opcion.toUpperCase()}</div>
          ))}
        </div>
      ))}
      {payload.notas ? <div className="tt-negrita">NOTA: {payload.notas}</div> : null}
    </>
  )
}

function Filas({ items }) {
  return (items || []).map((it, i) => (
    <div key={i}>
      <div className="tt-fila">
        <span>{it.cantidad} x {it.nombre}</span>
        <span>{cop(it.subtotal)}</span>
      </div>
      {(it.modificadores || []).map((m, j) => (
        <div key={j} className="tt-mod-chico">- {m.opcion}</div>
      ))}
    </div>
  ))
}

function Precuenta({ payload, negocio }) {
  const total = Number(payload.total)
  return (
    <>
      <div className="tt-titulo">{negocio || 'PRECUENTA'}</div>
      {payload.cliente ? <div className="tt-centro">{payload.cliente}</div> : null}
      <div className="tt-sep" />
      <Filas items={payload.items} />
      <div className="tt-sep" />
      <div className="tt-fila tt-negrita"><span>TOTAL</span><span>{cop(total)}</span></div>
      {payload.con_inc ? <div>Precios incluyen INC 8%</div> : null}
      <div className="tt-sep" />
      <div>Propina sugerida (10%): {cop(total * 0.1)}</div>
      <div>Es VOLUNTARIA: usted decide si la paga, la aumenta o la elimina.</div>
      <div className="tt-centro">* Documento no fiscal *</div>
    </>
  )
}

function Comprobante({ payload, negocio }) {
  return (
    <>
      <div className="tt-titulo">{negocio || 'COMPROBANTE'}</div>
      <div className="tt-centro">Venta #{payload.consecutivo}</div>
      <div>Fecha: {payload.fecha}</div>
      <div className="tt-sep" />
      <Filas items={payload.items} />
      <div className="tt-sep" />
      <div className="tt-fila tt-negrita"><span>TOTAL</span><span>{cop(payload.total)}</span></div>
      {payload.metodo_pago ? <div>Pago: {payload.metodo_pago}</div> : null}
      <div className="tt-centro">* Documento no fiscal *</div>
    </>
  )
}

const CUERPOS = { comanda: Comanda, precuenta: Precuenta, comprobante: Comprobante }

// Estilos inline del ticket (autocontenidos: viajan con el HTML a la ventana de impresión).
export const CSS_TICKET = `
  .tt-ticket { font-family: 'Courier New', monospace; font-size: 12px; color: #000;
               background: #fff; padding: 4mm; }
  .tt-titulo { text-align: center; font-weight: bold; font-size: 16px; }
  .tt-centro { text-align: center; }
  .tt-negrita { font-weight: bold; }
  .tt-item { font-weight: bold; font-size: 14px; }
  .tt-mod { font-weight: bold; font-size: 15px; }
  .tt-mod-chico { padding-left: 4mm; }
  .tt-fila { display: flex; justify-content: space-between; gap: 4px; }
  .tt-sep { border-top: 1px dashed #000; margin: 2mm 0; }
  @media print { body { margin: 0; } .tt-ticket { padding: 0; } }
`

export default function TicketTermico({ payload, ancho = 80, negocio = null }) {
  const Cuerpo = CUERPOS[payload?.tipo] || Comanda
  return (
    <div className="tt-ticket" style={{ width: ANCHOS[ancho] || ANCHOS[80] }} data-ancho={ancho}>
      <Cuerpo payload={payload} negocio={negocio} />
    </div>
  )
}

/* Abre la vista térmica en una ventana y dispara el diálogo de impresión del navegador. */
export function imprimirTicket(html) {
  const w = window.open('', '_blank', 'width=380,height=600')
  if (!w) return
  w.document.write(`<!doctype html><html><head><style>${CSS_TICKET}</style></head><body>${html}</body></html>`)
  w.document.close()
  w.focus()
  w.print()
}
