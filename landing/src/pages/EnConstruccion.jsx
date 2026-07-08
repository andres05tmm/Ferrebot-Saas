import { useEffect } from 'react'
import { Link } from 'react-router-dom'
import Sello from '../components/Sello.jsx'
import Obrero from '../components/Obrero.jsx'

// Portada temporal mientras la landing definitiva está en obra:
// el obrerito martillando el letrero + un mensaje corto. La landing
// completa sigue viva en /preview; /login y /demo no cambian.
export default function EnConstruccion() {
  useEffect(() => {
    document.title = 'Melquiadez · En obra'
  }, [])

  return (
    <main className="min-h-dvh flex flex-col">
      <header className="flex items-center justify-between px-6 py-5 sm:px-10">
        <div className="flex items-center gap-2.5">
          <Sello className="h-9 w-9" />
          <span className="font-display font-semibold text-lg tracking-tight">Melquiadez</span>
        </div>
        <Link
          to="/login"
          className="text-sm text-texto-2 hover:text-texto transition-colors"
        >
          Entrar
        </Link>
      </header>

      <section className="flex-1 w-full max-w-6xl mx-auto px-6 sm:px-10 grid items-center gap-10 lg:gap-6 lg:grid-cols-[1fr_1.15fr] py-8">
        <div className="max-w-xl order-2 lg:order-1">
          <h1 className="font-display font-semibold text-4xl sm:text-5xl leading-[1.08] tracking-tight text-balance">
            Aquí se construye una maravilla.
          </h1>
          <p className="mt-5 text-lg text-texto-2 max-w-[46ch]">
            Estamos en obra, martillando los últimos detalles. Vuelve pronto.
          </p>
        </div>

        <div className="order-1 lg:order-2 w-full max-w-xl mx-auto lg:max-w-none">
          <Obrero />
        </div>
      </section>

      <footer className="px-6 sm:px-10 py-5 text-sm text-texto-3">
        © 2026 Melquiadez · Hecho con oficio en Colombia
      </footer>
    </main>
  )
}
