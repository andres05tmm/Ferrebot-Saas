/*
 * ModalCantidad — al tocar un producto que se vende por fracción o sub-unidad, abre el modal de
 * captura (réplica del dashboard viejo): pintura por fracción de galón, lija por cm, puntilla por
 * gramos, tintilla por ml, producto por kilo. Determina la CANTIDAD decimal (y, si el cajero regatea
 * un monto o entra en modo "pesos", un `precioManual` = total explícito de la línea). El precio final
 * de la línea lo pone igual el servidor vía /precio; el preview de aquí sale de los mismos datos.
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
  const [manual, setManual] = useState('')           // override de $ (pintura/cm/kg)

  const manualNum = Number(manual) || 0

  // Resuelve { cantidad, precioManual, total, desc } según el tipo.
  const r = resolver()
  const valido = r.cantidad > 0 && (r.precioManual == null || r.precioManual > 0)

  function resolver() {
    if (tipo === 'fraccion') {
      const cantidad = unidades + (fracSel ? Number(fracSel.decimal) : 0)
      const desc = [unidades > 0 ? `${unidades} u` : '', fracSel ? fracSel.fraccion : '']
        .filter(Boolean).join(' + ')
      if (manualNum > 0) return { cantidad, precioManual: manualNum, total: manualNum, desc }
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
      const precioManual = manualNum > 0 ? manualNum : null
      return { cantidad, precioManual, total: precioManual ?? previewMotor(prod, cantidad), desc: `${cantidad} cm` }
    }
    // kg
    const cantidad = Number(kgVal) || 0
    const precioManual = manualNum > 0 ? manualNum : null
    return { cantidad, precioManual, total: precioManual ?? previewMotor(prod, cantidad), desc: kgDesc(cantidad) }
  }

  function confirmar() {
    if (!valido) return
    onConfirmar({ cantidad: r.cantidad, precioManual: r.precioManual, desc: r.desc })
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
                <Seg activo={fracSel == null} onClick={() => setFracSel(null)}>Ninguna</Seg>
                {fraccionesOrdenadas(prod).map((f) => (
                  <Seg key={f.fraccion} activo={fracSel?.fraccion === f.fraccion} onClick={() => setFracSel(f)}>
                    <span className="block">{f.fraccion}</span>
                    <span className="block text-[10px] text-success">{cop(Number(f.precio_total))}</span>
                  </Seg>
                ))}
              </div>
            </div>
            <OverrideDolar valor={manual} onChange={setManual} />
          </>
        )}

        {(tipo === 'gramos' || tipo === 'ml') && (
          <>
            <div className="grid grid-cols-3 gap-1.5">
              {[[ENVASE[tipo].full, paquete], [ENVASE[tipo].half, paquete / 2], [ENVASE[tipo].quarter, paquete / 4]]
                .map(([et, q]) => (
                  <Seg key={et} activo={modo === 'sub' && Number(valor) === q}
                    onClick={() => { setModo('sub'); setValor(String(q)) }}>
                    <span className="block">{et}</span>
                    <span className="block text-[10px] text-muted-foreground">{cop(previewMotor(prod, q))}</span>
                  </Seg>
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
          <>
            <div>
              <Label className="text-caption uppercase tracking-wider text-muted-foreground">Cantidad en centímetros</Label>
              <div className="mt-1.5 flex items-center gap-2">
                <Input type="number" min="0" step="any" value={cmVal} autoFocus
                  onChange={(e) => setCmVal(e.target.value)} aria-label="Cantidad en centímetros" />
                <span className="text-caption text-muted-foreground">cm</span>
              </div>
            </div>
            <OverrideDolar valor={manual} onChange={setManual} />
          </>
        )}

        {tipo === 'kg' && (
          <>
            <div className="grid grid-cols-3 gap-1.5">
              {KG_RAPIDOS.map(([et, q]) => (
                <Seg key={et} activo={Number(kgVal) === q} onClick={() => setKgVal(String(q))}>
                  <span className="block">{et}</span>
                  <span className="block text-[10px] text-muted-foreground">{cop(previewMotor(prod, q))}</span>
                </Seg>
              ))}
            </div>
            <Input type="number" min="0" step="0.5" value={kgVal}
              onChange={(e) => setKgVal(e.target.value)} placeholder="kg" aria-label="Cantidad en kilos" />
            <OverrideDolar valor={manual} onChange={setManual} />
          </>
        )}

        <div className="flex items-center justify-between border-t border-border pt-3">
          <span className="text-caption text-muted-foreground">{r.desc || '—'}</span>
          <span className="text-body font-semibold tabular">{cop(r.total)}</span>
        </div>
      </div>

      <DialogFooter>
        <Button type="button" variant="outline" onClick={onCancelar}>Cancelar</Button>
        <Button type="button" disabled={!valido} onClick={confirmar}>Agregar al carrito</Button>
      </DialogFooter>
    </>
  )
}

function OverrideDolar({ valor, onChange }) {
  return (
    <div>
      <Label htmlFor="cant-manual" className="text-caption text-muted-foreground">Precio a mano (opcional)</Label>
      <div className="mt-1.5 flex items-center gap-2">
        <span className="text-muted-foreground">$</span>
        <Input id="cant-manual" type="number" min="0" step="any" value={valor}
          onChange={(e) => onChange(e.target.value)} placeholder="dejar vacío = precio calculado"
          aria-label="Precio a mano" />
      </div>
    </div>
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
