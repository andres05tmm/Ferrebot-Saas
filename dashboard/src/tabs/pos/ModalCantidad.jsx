/*
 * ModalCantidad — al tocar un producto que se vende por fracción o sub-unidad, abre el modal de
 * captura (réplica del dashboard viejo): pintura por fracción de galón, lija por cm, puntilla por
 * gramos, tintilla por ml, producto por kilo. Determina la CANTIDAD decimal; el precio final de la
 * línea lo pone el servidor vía /precio, y el total de abajo a la derecha es EDITABLE (regatear un
 * monto) → si el cajero lo cambia, esa cifra viaja como precio de la línea (override).
 */
import { useState } from 'react'
import { cop } from '@/components/shared.jsx'
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '@/components/ui/dialog.jsx'
import { Input } from '@/components/ui/input.jsx'
import { Button } from '@/components/ui/button.jsx'
import { Label } from '@/components/ui/label.jsx'
import { Seg } from './piezas.jsx'
import {
  fraccionQueCasa, fraccionesOrdenadas, paqueteDe, precioSubunidad, previewMotor,
  subunidadesDesdePesos, tipoVenta,
} from './cantidad.js'

// Etiquetas del envase por tipo de granel con botones de paquete (gramos/ml).
const ENVASE = {
  gramos: { sub: 'g', nombre: 'caja', full: 'Caja completa', half: '½ caja', quarter: '¼ caja' },
  ml: { sub: 'ml', nombre: 'tarro', full: 'Tarro completo', half: '½ tarro', quarter: '¼ tarro' },
}
const KG_RAPIDOS = [['½ kg', 0.5], ['1 kg', 1], ['1½ kg', 1.5], ['2 kg', 2], ['2½ kg', 2.5], ['3 kg', 3]]

function kgDesc(n) {
  if (!n) return ''
  const ent = Math.floor(n)
  const medio = Math.abs(n - ent - 0.5) < 0.001
  if (medio && ent === 0) return '½ kg'
  if (medio) return `${ent}½ kg`
  return `${n} kg`
}

// Botón "KPI" (réplica del viejo): título grande + precio neutro debajo. Se usa para las fracciones
// de pintura y los accesos rápidos de granel/kg.
function BotonKpi({ activo, onClick, titulo, precio, sub }) {
  return (
    <button type="button" onClick={onClick} aria-pressed={activo}
      className={`flex flex-col items-center justify-center gap-1 h-14 rounded-md border px-2 text-center transition-colors ${
        activo ? 'border-primary bg-primary/10' : 'border-border bg-surface hover:bg-surface-2'}`}>
      <span className={`text-body-sm font-semibold leading-none ${activo ? 'text-primary' : 'text-foreground'}`}>{titulo}</span>
      {precio != null
        ? <span className="text-caption tabular leading-none text-muted-foreground">{cop(precio)}</span>
        : sub ? <span className="text-[11px] leading-none text-muted-foreground">{sub}</span> : null}
    </button>
  )
}

export default function ModalCantidad({ prod, onCerrar, onConfirmar }) {
  return (
    <Dialog open={prod != null} onOpenChange={(o) => { if (!o) onCerrar() }}>
      <DialogContent aria-describedby="cant-desc">
        {prod && (
          <FormCantidad key={prod.id} prod={prod} tipo={tipoVenta(prod)}
            onConfirmar={onConfirmar} onCancelar={onCerrar} />
        )}
      </DialogContent>
    </Dialog>
  )
}

function FormCantidad({ prod, tipo, onConfirmar, onCancelar }) {
  const pv = Number(prod.precio_venta) || 0
  const paquete = paqueteDe(prod)
  const precioSub = precioSubunidad(prod)

  // Estado (un solo hook por campo; el `key={prod.id}` del padre lo resetea entre productos).
  const [unidades, setUnidades] = useState(0)        // pintura: galones/unidades completas
  const [fracSel, setFracSel] = useState(null)       // pintura: fila de fracción elegida (o null)
  const [modo, setModo] = useState('sub')            // gramos/ml: 'sub' | 'pesos'
  const [valor, setValor] = useState('')             // gramos/ml: sub-unidades o pesos según `modo`
  const [cmVal, setCmVal] = useState('')             // cm
  const [kgVal, setKgVal] = useState('')             // kg
  // Total editable de abajo: `precio` es lo que se muestra/edita; `tocado` marca que el cajero lo
  // cambió a mano (regateo). Mientras no lo toque, sigue al total calculado.
  const [precio, setPrecio] = useState('')
  const [tocado, setTocado] = useState(false)

  const r = resolver()                               // { cantidad, precioManual (base), total, desc }
  // El override editable gana sobre el precioManual base (pesos / pintura mixta).
  const precioManual = tocado && Number(precio) > 0 ? Number(precio) : r.precioManual
  const totalMostrado = tocado ? precio : String(Math.round(r.total))
  const valido = r.cantidad > 0 && (precioManual == null || precioManual > 0)

  function resolver() {
    if (tipo === 'fraccion') {
      const cantidad = unidades + (fracSel ? Number(fracSel.decimal) : 0)
      const desc = [unidades > 0 ? `${unidades} u` : '', fracSel ? fracSel.fraccion : '']
        .filter(Boolean).join(' + ')
      // Unidades enteras + fracción a la vez: el motor no lo expresa en una cantidad; el modal ya
      // sabe el total exacto (unidades a precio lleno + la fracción a su precio bonito).
      if (unidades > 0 && fracSel) {
        const total = unidades * pv + Number(fracSel.precio_total)
        return { cantidad, precioManual: total, total, desc }
      }
      return { cantidad, precioManual: null, total: previewMotor(prod, cantidad), desc }
    }
    if (tipo === 'gramos' || tipo === 'ml') {
      const env = ENVASE[tipo]
      const valorNum = Number(valor) || 0
      if (modo === 'pesos') {
        const cantidad = subunidadesDesdePesos(prod, valorNum)
        return { cantidad, precioManual: valorNum, total: valorNum, desc: `${cantidad} ${env.sub}` }
      }
      const cantidad = valorNum
      const desc = paquete && cantidad >= paquete && cantidad % paquete === 0
        ? `${cantidad / paquete} ${env.nombre}(s)` : `${cantidad} ${env.sub}`
      return { cantidad, precioManual: null, total: previewMotor(prod, cantidad), desc }
    }
    if (tipo === 'cm') {
      const cantidad = Number(cmVal) || 0
      return { cantidad, precioManual: null, total: previewMotor(prod, cantidad), desc: `${cantidad} cm` }
    }
    // kg
    const cantidad = Number(kgVal) || 0
    return { cantidad, precioManual: null, total: previewMotor(prod, cantidad), desc: kgDesc(cantidad) }
  }

  function confirmar() {
    if (!valido) return
    onConfirmar({ cantidad: r.cantidad, precioManual, desc: r.desc })
  }

  return (
    <>
      <DialogHeader>
        <DialogTitle>{prod.nombre}</DialogTitle>
        <DialogDescription id="cant-desc">{subtitulo(tipo, prod, pv, precioSub, paquete)}</DialogDescription>
      </DialogHeader>

      <div className="space-y-3">
        {tipo === 'fraccion' && (
          <>
            <div>
              <Label className="text-caption uppercase tracking-wider text-muted-foreground">Unidades completas</Label>
              <div className="mt-1.5 flex items-center gap-2">
                <Button type="button" variant="outline" size="icon" aria-label="Menos"
                  onClick={() => setUnidades((n) => Math.max(0, n - 1))}>−</Button>
                <span className="w-12 text-center text-body font-semibold tabular">{unidades}</span>
                <Button type="button" variant="outline" size="icon" aria-label="Más"
                  onClick={() => setUnidades((n) => n + 1)}>+</Button>
                <span className="text-caption text-muted-foreground">× {cop(pv)}</span>
              </div>
            </div>
            <div>
              <Label className="text-caption uppercase tracking-wider text-muted-foreground">Fracción adicional</Label>
              <div className="mt-1.5 grid grid-cols-3 gap-1.5">
                <BotonKpi activo={fracSel == null} onClick={() => setFracSel(null)}
                  titulo="Ninguna" sub="sólo unidades" />
                {fraccionesOrdenadas(prod).map((f) => (
                  <BotonKpi key={f.fraccion} activo={fracSel?.fraccion === f.fraccion}
                    onClick={() => setFracSel(f)} titulo={f.fraccion} precio={Number(f.precio_total)} />
                ))}
              </div>
            </div>
          </>
        )}

        {(tipo === 'gramos' || tipo === 'ml') && (
          <>
            <div className="grid grid-cols-3 gap-1.5">
              {[[ENVASE[tipo].full, paquete], [ENVASE[tipo].half, paquete / 2], [ENVASE[tipo].quarter, paquete / 4]]
                .map(([et, q]) => (
                  <BotonKpi key={et} activo={modo === 'sub' && Number(valor) === q}
                    onClick={() => { setModo('sub'); setValor(String(q)) }} titulo={et} precio={previewMotor(prod, q)} />
                ))}
            </div>
            <div className="flex gap-1.5">
              <Seg activo={modo === 'pesos'} onClick={() => { setModo('pesos'); setValor('') }}>$ Pesos</Seg>
              <Seg activo={modo === 'sub'} onClick={() => { setModo('sub'); setValor('') }}>
                {ENVASE[tipo].sub} {tipo === 'gramos' ? 'Gramos' : 'Mililitros'}
              </Seg>
            </div>
            <Input type="number" min="0" step="any" value={valor} autoFocus
              onChange={(e) => setValor(e.target.value)}
              placeholder={modo === 'pesos' ? 'ej: 2000' : `${ENVASE[tipo].sub}`}
              aria-label={modo === 'pesos' ? 'Monto en pesos' : `Cantidad en ${ENVASE[tipo].sub}`} />
          </>
        )}

        {tipo === 'cm' && (
          <div>
            <Label className="text-caption uppercase tracking-wider text-muted-foreground">Cantidad en centímetros</Label>
            <div className="mt-1.5 flex items-center gap-2">
              <Input type="number" min="0" step="any" value={cmVal} autoFocus
                onChange={(e) => setCmVal(e.target.value)} aria-label="Cantidad en centímetros" />
              <span className="text-caption text-muted-foreground">cm</span>
            </div>
          </div>
        )}

        {tipo === 'kg' && (
          <>
            <div className="grid grid-cols-3 gap-1.5">
              {KG_RAPIDOS.map(([et, q]) => (
                <BotonKpi key={et} activo={Number(kgVal) === q} onClick={() => setKgVal(String(q))}
                  titulo={et} precio={previewMotor(prod, q)} />
              ))}
            </div>
            <Input type="number" min="0" step="0.5" value={kgVal}
              onChange={(e) => setKgVal(e.target.value)} placeholder="kg" aria-label="Cantidad en kilos" />
          </>
        )}

        {/* Total editable: se ve el precio calculado y el cajero puede sobreescribirlo (regatear). */}
        <div className="flex items-center justify-between border-t border-border pt-3">
          <span className="text-caption text-muted-foreground">{r.desc || '—'}</span>
          <div className="flex items-center gap-1">
            <span className="text-body font-semibold text-muted-foreground">$</span>
            <input type="number" min="0" step="any" value={totalMostrado}
              onChange={(e) => { const v = e.target.value; setPrecio(v); setTocado(v !== '') }}
              aria-label="Precio total (editable)"
              className="w-28 bg-transparent text-right text-xl font-bold tabular text-foreground outline-none border-b border-transparent focus:border-primary" />
          </div>
        </div>
      </div>

      <DialogFooter>
        <Button type="button" variant="outline" onClick={onCancelar}>Cancelar</Button>
        <Button type="button" disabled={!valido} onClick={confirmar}>Agregar al carrito</Button>
      </DialogFooter>
    </>
  )
}

function subtitulo(tipo, prod, pv, precioSub, paquete) {
  if (tipo === 'fraccion') return `Precio unidad: ${cop(pv)}`
  if (tipo === 'gramos') return `${cop(precioSub)}/g · ${cop(pv)} por caja (${paquete} g)`
  if (tipo === 'ml') return `${cop(precioSub)}/ml · ${cop(pv)} por tarro (${paquete} ml)`
  if (tipo === 'cm') return `Pliego: ${cop(pv)} · ${cop(precioSub)}/cm`
  if (tipo === 'kg') {
    const half = fraccionQueCasa(prod, 0.5)
    return `${cop(pv)}/kg${half ? ` · ½ kg ${cop(Number(half.precio_total))}` : ''}`
  }
  return ''
}
