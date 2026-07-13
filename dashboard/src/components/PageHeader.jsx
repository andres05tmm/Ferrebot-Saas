/*
 * PageHeader — encabezado de página compartido (F2.0). Unifica el header que cada tab armaba inline
 * (título/spacing/toolbars divergían entre tabs): icono + título (font-display vía h1) + sublínea,
 * slot `acciones` a la derecha —ese slot ES la toolbar de la página, no hay componente Toolbar aparte—
 * y `children` opcional debajo (fila de KPIs/chips/filtros). Presentación pura, tokens del tema.
 * En móvil las acciones colapsan a su propia fila bajo el título.
 */
import { cn } from '@/lib/utils'

export default function PageHeader({ icono: Icono, titulo, sublinea, acciones, children, className }) {
  return (
    <header className={cn('mb-4', className)}>
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <h1 className="flex items-center gap-2 text-lg font-semibold text-foreground">
            {Icono && <Icono className="size-5 shrink-0 text-primary" aria-hidden="true" />}
            <span className="truncate">{titulo}</span>
          </h1>
          {sublinea && <p className="mt-0.5 text-body-sm text-muted-foreground">{sublinea}</p>}
        </div>
        {acciones && <div className="flex shrink-0 flex-wrap items-center gap-2">{acciones}</div>}
      </div>
      {children && <div className="mt-3">{children}</div>}
    </header>
  )
}
