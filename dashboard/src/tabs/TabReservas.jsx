/*
 * TabReservas — reservas por noches (hotel) sobre el motor de agenda. Gateada por 'pack_reservas'.
 * Recepción: elige check-in + noches, consulta habitaciones libres (GET /reservas/habitaciones) y
 * reserva una (POST /reservas) con los datos del huésped. El backend es idempotente y toma el lock por
 * recurso (anti-doble-reserva). Muestra el anticipo a cobrar si el negocio lo exige. Staff (vendedor+).
 */
import { useState } from 'react'
import { toast } from 'sonner'
import { BedDouble, CalendarClock } from 'lucide-react'
import { cop } from '@/components/shared.jsx'
import { hoyStrCO as hoyCO } from '@/lib/fechas'
import { useHabitaciones, useCrearReserva } from '@/lib/queries'
import { Card } from '@/components/ui/card.jsx'
import { Input } from '@/components/ui/input.jsx'
import { Button } from '@/components/ui/button.jsx'

const arr = (d) => (Array.isArray(d) ? d : [])

function FormReserva({ hab, checkin, noches, onHecho }) {
  const [nombre, setNombre] = useState('')
  const [telefono, setTelefono] = useState('')
  const [enviando, setEnviando] = useState(false)
  const crearM = useCrearReserva()

  async function reservar() {
    if (!nombre.trim() || !telefono.trim()) { toast.error('Indica nombre y teléfono del huésped'); return }
    setEnviando(true)
    try {
      const res = await crearM.mutateAsync({
        recurso_id: hab.recurso_id, checkin, noches: Number(noches),
        cliente_nombre: nombre.trim(), cliente_telefono: telefono.trim(),
      })
      if (res.ok) {
        const data = await res.json().catch(() => ({}))
        const anticipo = data?.anticipo
        toast.success(anticipo ? `Reserva creada · anticipo ${cop(anticipo)}` : 'Reserva creada')
        onHecho()
      } else if (res.status === 409) {
        toast.error('La habitación ya no está disponible en esas fechas')
        onHecho()
      } else {
        toast.error('No se pudo crear la reserva')
      }
    } catch { toast.error('Error de conexión') } finally { setEnviando(false) }
  }

  return (
    <div className="flex flex-wrap items-center gap-2 mt-2">
      <Input value={nombre} onChange={e => setNombre(e.target.value)} placeholder="Nombre del huésped"
        aria-label={`Nombre huésped ${hab.recurso_id}`} className="h-9 flex-1 min-w-[8rem]" />
      <Input value={telefono} onChange={e => setTelefono(e.target.value)} placeholder="Teléfono"
        aria-label={`Teléfono huésped ${hab.recurso_id}`} className="h-9 w-36" />
      <Button size="sm" disabled={enviando} onClick={reservar}
        aria-label={`Confirmar reserva ${hab.recurso_id}`}>
        {enviando ? 'Reservando…' : 'Confirmar'}
      </Button>
    </div>
  )
}

function Habitacion({ hab, checkin, noches }) {
  const [abierto, setAbierto] = useState(false)
  return (
    <li className="px-3.5 py-2.5 text-[13px]">
      <div className="flex items-center gap-3">
        <BedDouble className="size-4 text-muted-foreground shrink-0" />
        <div className="min-w-0 flex-1">
          <div className="font-medium truncate">{hab.nombre}</div>
          <div className="text-[11px] text-muted-foreground tabular-nums">
            {hab.precio_noche != null ? `${cop(hab.precio_noche)}/noche` : 'sin tarifa'}
          </div>
        </div>
        {hab.total != null && <span className="tabular-nums font-semibold shrink-0">{cop(hab.total)}</span>}
        <Button size="sm" variant={abierto ? 'ghost' : 'default'} className="shrink-0"
          onClick={() => setAbierto(v => !v)}>
          {abierto ? 'Cerrar' : 'Reservar'}
        </Button>
      </div>
      {abierto && <FormReserva hab={hab} checkin={checkin} noches={noches} onHecho={() => setAbierto(false)} />}
    </li>
  )
}

export default function TabReservas() {
  const [checkin, setCheckin] = useState(hoyCO())
  const [noches, setNoches] = useState(1)
  // Se dispara la búsqueda con un "tick" para no pedir en cada tecleo; y para poder refetch tras reservar.
  const [buscado, setBuscado] = useState(null)   // { checkin, noches }

  const habsQ = useHabitaciones(buscado)
  const habitaciones = arr(habsQ.data)

  function buscar() {
    const n = Number(noches)
    if (!checkin || !(n >= 1 && n <= 30)) { toast.error('Elige fecha y de 1 a 30 noches'); return }
    setBuscado({ checkin, noches: n })
  }

  return (
    <div className="space-y-3">
      <h1 className="text-base font-semibold inline-flex items-center gap-2">
        <BedDouble className="size-4.5 text-primary" /> Reservas
      </h1>

      <Card className="p-3">
        <div className="flex flex-wrap items-end gap-3">
          <label className="text-[11px] text-muted-foreground">
            Check-in
            <Input type="date" min={hoyCO()} value={checkin} onChange={e => setCheckin(e.target.value)}
              aria-label="Check-in" className="h-9 mt-1" />
          </label>
          <label className="text-[11px] text-muted-foreground">
            Noches
            <Input type="number" min="1" max="30" value={noches} onChange={e => setNoches(e.target.value)}
              aria-label="Noches" className="h-9 mt-1 w-24" />
          </label>
          <Button onClick={buscar} className="ml-auto inline-flex items-center gap-1.5">
            <CalendarClock className="size-4" /> Buscar disponibilidad
          </Button>
        </div>
      </Card>

      {!buscado ? (
        <Card className="p-10 text-center text-sm text-muted-foreground">
          Elige las fechas y busca las habitaciones disponibles.
        </Card>
      ) : (
        <Card className="p-0 overflow-hidden">
          <div className="px-3.5 py-2.5 border-b border-border-subtle">
            <h2 className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
              Habitaciones libres · check-in {buscado.checkin} · {buscado.noches} noche{buscado.noches === 1 ? '' : 's'}
            </h2>
          </div>
          {habsQ.isLoading ? (
            <p className="py-10 text-center text-sm text-muted-foreground">Buscando…</p>
          ) : habsQ.isError ? (
            <p className="py-10 text-center text-sm text-destructive">No se pudo consultar la disponibilidad.</p>
          ) : habitaciones.length === 0 ? (
            <p className="py-10 text-center text-sm text-muted-foreground">
              No hay habitaciones libres en esas fechas.
            </p>
          ) : (
            <ul className="divide-y divide-border-subtle">
              {habitaciones.map(h => (
                <Habitacion key={h.recurso_id} hab={h} checkin={buscado.checkin} noches={buscado.noches} />
              ))}
            </ul>
          )}
        </Card>
      )}
    </div>
  )
}
