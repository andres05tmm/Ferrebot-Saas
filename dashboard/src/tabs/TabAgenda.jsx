/*
 * TabAgenda — pestaña del pack Agenda (oculta sin la feature 'pack_agenda'; ver routes/features).
 * Dos secciones: "Citas" (lista funcional + tiempo real, para todo el staff) y "Configuración"
 * (CRUD de catálogo/reglas, solo admin). La vista de calendario diseñada llega después.
 */
import { useState } from 'react'
import { CalendarClock, Settings } from 'lucide-react'
import { useAuth } from '@/hooks/useAuth.js'
import SeccionCitas from './agenda/SeccionCitas.jsx'
import SeccionConfig from './agenda/SeccionConfig.jsx'

export default function TabAgenda() {
  const [seccion, setSeccion] = useState('citas')
  const admin = useAuth().isAdmin()

  return (
    <div className="space-y-3">
      <div className="inline-flex items-center gap-1 rounded-md bg-surface-2 p-1">
        <SubTab activa={seccion === 'citas'} onClick={() => setSeccion('citas')} icon={CalendarClock}>Citas</SubTab>
        <SubTab activa={seccion === 'config'} onClick={() => setSeccion('config')} icon={Settings}>Configuración</SubTab>
      </div>

      {seccion === 'citas' ? <SeccionCitas /> : <SeccionConfig admin={admin} />}
    </div>
  )
}

function SubTab({ activa, onClick, icon: Icon, children }) {
  return (
    <button onClick={onClick} aria-pressed={activa}
      className={`inline-flex items-center gap-1.5 rounded-sm px-3 h-8 text-sm font-medium transition-colors ${activa ? 'bg-surface text-foreground shadow-xs' : 'text-muted-foreground hover:text-foreground'}`}>
      <Icon className="size-4" /> {children}
    </button>
  )
}
