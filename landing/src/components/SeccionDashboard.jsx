import { ContainerScroll } from '@/components/ui/container-scroll-animation.jsx'

/* La sección del dashboard: el marco hace zoom-out al hacer scroll (container-scroll-animation). */
export default function SeccionDashboard() {
  return (
    <section className="overflow-hidden px-2 sm:px-6" aria-label="El dashboard de Melquiadez">
      <ContainerScroll
        titleComponent={
          <h2 className="mx-auto mb-6 max-w-[22ch] font-display text-4xl font-semibold leading-[1.05] tracking-tight md:text-5xl">
            Tú lo ves <em className="not-italic text-acento">todo</em>.
            <span className="mt-3 block text-base font-normal tracking-normal text-texto-2 sm:text-lg">
              Citas, pedidos y conversaciones, en vivo y con tu marca.
            </span>
          </h2>
        }
      >
        <img
          src="/dashboard.png"
          alt="Dashboard de Melquiadez: agenda del día, conversaciones del agente y casos que necesitan un humano"
          loading="lazy"
          className="h-full w-full rounded-2xl object-cover object-top"
        />
      </ContainerScroll>
    </section>
  )
}
