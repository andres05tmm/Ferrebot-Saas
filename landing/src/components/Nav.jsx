import { Link } from 'react-router-dom'
import { Moon, Sun } from 'lucide-react'
import { useState } from 'react'
import Sello from './Sello.jsx'
import { alternarTema, temaActual } from '@/lib/tema.js'

export default function Nav() {
  const [tema, setTema] = useState(() =>
    typeof document === 'undefined' ? 'claro' : temaActual(),
  )

  return (
    <nav className="fixed inset-x-0 top-0 z-50 flex items-center justify-between gap-3 border-b border-linea/70 bg-fondo/85 px-5 py-3 backdrop-blur-md sm:px-10">
      <Link to="/" className="flex items-center gap-2.5" aria-label="Melquiadez — inicio">
        <Sello className="size-9" />
        <span className="font-display text-[19px] font-semibold tracking-tight">Melquiadez</span>
      </Link>
      <div className="flex items-center gap-2 sm:gap-3">
        <button
          type="button"
          onClick={() => setTema(alternarTema())}
          aria-label="Cambiar entre modo claro y oscuro"
          className="grid size-10 place-items-center rounded-full border border-linea bg-fondo-2 text-texto-2 transition-transform duration-300 ease-marca hover:rotate-12 hover:scale-105 hover:text-texto"
        >
          {tema === 'claro' ? <Moon className="size-[18px]" /> : <Sun className="size-[18px]" />}
        </button>
        <Link
          to="/login"
          className="rounded-full px-4 py-2 text-sm font-semibold text-texto-2 transition-colors hover:text-texto"
        >
          Entrar
        </Link>
        <Link
          to="/demo"
          className="rounded-full bg-acento px-5 py-2.5 text-sm font-semibold text-acento-sobre shadow-sm transition-all duration-300 ease-marca hover:-translate-y-px hover:shadow-md"
        >
          Ver una demo
        </Link>
      </div>
    </nav>
  )
}
