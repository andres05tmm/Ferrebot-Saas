/*
 * CargarDiasViejos — pegar de una vez los totales de los días anteriores al sistema.
 *
 * El caso real son meses acumulados en un cuaderno o en la tabla del dashboard viejo. Cargarlos día
 * por día en el calendario sería tan tedioso que no se haría nunca, así que la puerta principal es
 * un `textarea` donde se pega la lista tal cual, con `$` y puntos de miles incluidos.
 *
 * Muestra SIEMPRE una vista previa antes de guardar, y lista los renglones que no entendió con su
 * número de línea. Un parser que descarta en silencio le haría perder plata del reporte al dueño sin
 * que se entere; acá lo que no se entiende se ve.
 */
import { useMemo, useState } from 'react'
import { toast } from 'sonner'
import { api } from '@/lib/api'
import { cop } from '@/components/shared.jsx'
import { Card } from '@/components/ui/card.jsx'
import { parsearDiasPegados } from './pegarDias.js'

const EJEMPLO = `07/27\t$602.500
07/24\t$283.000
2026-07-23   282.000`

export default function CargarDiasViejos({ anio, onClose, onGuardado }) {
  const [texto, setTexto] = useState('')
  const [guardando, setGuardando] = useState(false)

  const { dias, errores } = useMemo(() => parsearDiasPegados(texto, anio), [texto, anio])
  const total = dias.reduce((a, d) => a + d.total, 0)

  async function guardar() {
    if (!dias.length) return
    setGuardando(true)
    try {
      const res = await api('/historico-ventas/cargar', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ dias }),
      })
      if (res.ok) {
        const data = await res.json().catch(() => ({}))
        toast.success(`${data.guardados ?? dias.length} día(s) guardado(s)`)
        onGuardado?.()
      } else if (res.status === 403) toast.error('Solo un administrador puede cargar días anteriores')
      else toast.error('No se pudieron guardar los días')
    } catch { toast.error('Error de conexión') } finally { setGuardando(false) }
  }

  return (
    <Card className="p-3.5 space-y-3">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold">Cargar días anteriores al sistema</h2>
          <p className="text-caption text-muted-foreground max-w-prose">
            Pega una línea por día, con la fecha y el total. Sirve tal cual como lo copies del
            dashboard viejo o de tu cuaderno. Solo se guarda el total: estos días no llevan gastos ni
            detalle de productos, y no entran a los reportes financieros.
          </p>
        </div>
        <button type="button" onClick={onClose}
          className="text-meta h-8 px-2.5 rounded-md border border-border hover:bg-surface-2 shrink-0">
          Cerrar
        </button>
      </div>

      <textarea
        value={texto}
        onChange={(e) => setTexto(e.target.value)}
        aria-label="Días y totales"
        placeholder={EJEMPLO}
        rows={8}
        className="w-full rounded-md border border-input bg-surface px-3 py-2 text-body-sm font-mono
                   placeholder:text-muted-foreground/60 focus:outline-none focus:ring-2 focus:ring-ring"
      />

      {texto.trim() !== '' && (
        <div className="space-y-2">
          {/* Vista previa: lo que se va a guardar, contado y sumado, ANTES de tocar la base. */}
          <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
            <span className="text-body-sm">
              <strong className="tabular">{dias.length}</strong> día(s) para guardar
            </span>
            <span className="text-body-sm tabular text-success font-semibold">{cop(total)}</span>
            {errores.length > 0 && (
              <span className="text-body-sm text-warning">
                {errores.length} línea(s) sin entender
              </span>
            )}
          </div>

          {errores.length > 0 && (
            <ul className="text-caption text-warning space-y-0.5 max-h-28 overflow-y-auto">
              {errores.map(e => (
                <li key={e.linea}>Línea {e.linea}: {e.motivo} — “{e.texto.slice(0, 40)}”</li>
              ))}
            </ul>
          )}

          {dias.length > 0 && (
            <div className="max-h-40 overflow-y-auto rounded-md border border-border-subtle">
              <table className="w-full text-caption">
                <tbody className="divide-y divide-border-subtle">
                  {dias.map(d => (
                    <tr key={d.fecha}>
                      <td className="px-3 py-1 tabular text-muted-foreground">{d.fecha}</td>
                      <td className="px-3 py-1 text-right tabular">{cop(d.total)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      <div className="flex items-center justify-end gap-2">
        <p className="text-caption text-muted-foreground mr-auto">
          Volver a pegar una fecha ya cargada la corrige; no se duplica.
        </p>
        <button type="button" onClick={guardar} disabled={guardando || dias.length === 0}
          className="text-meta h-9 px-3 rounded-md bg-primary text-primary-foreground hover:bg-primary-hover disabled:opacity-60">
          {guardando ? 'Guardando…' : `Guardar ${dias.length || ''} día(s)`}
        </button>
      </div>
    </Card>
  )
}
