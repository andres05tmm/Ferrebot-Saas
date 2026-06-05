/*
 * KpiCard — tarjeta de métrica compacta, tokenizada y reutilizable.
 *
 * Props:
 *   tone        — 'success' | 'info' | 'warning' | 'danger' | 'primary' | 'muted' | 'default'
 *   label       — etiqueta superior (uppercase)
 *   value       — valor principal (string | number | ReactNode)
 *   sub         — texto secundario debajo del valor
 *   icon        — componente lucide-react
 *   deltaPct    — número opcional para mostrar tendencia ↑/↓ vs anterior
 *   spark       — array de puntos [{ total }, …] para sparkline (>=3)
 *   onClick     — vuelve la card clickeable + focusable + aria-button
 *   actionLabel — chip discreto en hover/focus indicando la acción
 *   loading     — atenúa el valor
 *   compact     — versión densa (p-2.5 en vez de p-3)
 *   topAccent   — barra sólida de 3px arriba con el color del tone
 *   iconStyle   — 'subtle' (default) | 'filled' (cuadrado sólido + ícono blanco)
 *   heroValue   — si true: cifra en text-3xl (hero number, sin color de tone — usa foreground)
 *   headerBand  — si true: banda superior sólida coloreada con label+ícono blancos.
 *                 Mutuamente excluyente con topAccent. Cuerpo blanco abajo, cifra foreground.
 *   coloredValue— si true: cifra usa el color del tone (resalta el valor).
 *                 Aplica tanto en variante headerBand como hero.
 */
import { TrendingUp, TrendingDown, ArrowRight } from 'lucide-react'
import { ResponsiveContainer, LineChart, Line } from 'recharts'
import { Card } from '@/components/ui/card.jsx'
import { cn } from '@/lib/utils'

const TONES = {
  success: { color: 'hsl(var(--success))',     bg: 'bg-success/[0.04]',     border: 'border-success/20     hover:border-success/40'     },
  info:    { color: 'hsl(var(--info))',        bg: 'bg-info/[0.04]',        border: 'border-info/20        hover:border-info/40'        },
  warning: { color: 'hsl(var(--warning))',     bg: 'bg-warning/[0.04]',     border: 'border-warning/20     hover:border-warning/40'     },
  danger:  { color: 'hsl(var(--danger))',      bg: 'bg-danger/[0.04]',      border: 'border-danger/20      hover:border-danger/40'      },
  primary: { color: 'hsl(var(--accent))',      bg: 'bg-accent-red/[0.04]',  border: 'border-accent-red/20  hover:border-accent-red/40'  },
  muted:   { color: 'hsl(var(--text-muted))',  bg: 'bg-muted/30',            border: 'border-border         hover:border-border'         },
  default: { color: 'hsl(var(--text-primary))','bg': 'bg-surface',           border: 'border-border         hover:border-border'         },
}

export default function KpiCard({
  tone = 'default',
  label,
  value,
  sub,
  icon: Icon,
  deltaPct,
  spark,
  onClick,
  actionLabel,
  loading,
  compact = false,
  topAccent = false,
  iconStyle = 'subtle',
  heroValue = false,
  headerBand = false,
  coloredValue = false,
}) {
  const t = TONES[tone] || TONES.default
  const clickable = typeof onClick === 'function'

  // ────────────────────────────────────────────────────────────
  // Variante headerBand: banda superior sólida + cuerpo blanco
  // ────────────────────────────────────────────────────────────
  if (headerBand) {
    return (
      <Card
        className={cn(
          'relative overflow-hidden p-0 transition-all duration-base ease-out-quad group',
          'bg-surface border-border',
          clickable
            ? 'cursor-pointer text-left w-full hover:-translate-y-0.5 hover:shadow-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40'
            : 'hover:-translate-y-px hover:shadow-sm',
        )}
        {...(clickable ? {
          onClick,
          role: 'button',
          tabIndex: 0,
          'aria-label': actionLabel || label,
          onKeyDown: (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onClick() } },
        } : {})}
      >
        {/* Banda superior sólida del tone */}
        <div
          className="flex items-center justify-between gap-2 px-3 py-1.5"
          style={{ background: t.color }}
        >
          <span className="text-[10.5px] font-semibold uppercase tracking-wider text-white truncate">
            {label}
          </span>
          {Icon && (
            <Icon className="size-3.5 text-white shrink-0" aria-hidden="true" />
          )}
        </div>

        {/* Cuerpo — blanco por default; tintado con el tone cuando coloredValue */}
        <div
          className="px-3 py-2"
          style={coloredValue
            ? { background: `color-mix(in srgb, ${t.color} 6%, transparent)` }
            : undefined}
        >
          <div className={cn(
            'text-xl font-semibold tracking-tight tabular leading-none',
            !coloredValue && 'text-foreground',
            loading && 'opacity-50',
          )}
            style={coloredValue ? { color: t.color } : undefined}
          >
            {value}
          </div>
          {sub && (
            <div
              className={cn(
                'mt-1 text-[10.5px] leading-snug truncate',
                !coloredValue && 'text-muted-foreground',
              )}
              style={coloredValue
                ? { color: `color-mix(in srgb, ${t.color} 75%, transparent)` }
                : undefined}
            >
              {sub}
            </div>
          )}
        </div>
      </Card>
    )
  }

  // ────────────────────────────────────────────────────────────
  // Variantes clásicas (topAccent / heroValue / compact)
  // ────────────────────────────────────────────────────────────
  return (
    <Card
      className={cn(
        'relative overflow-hidden transition-all duration-base ease-out-quad group',
        compact ? 'p-2.5' : 'p-2.5',
        t.bg, t.border,
        clickable
          ? 'cursor-pointer text-left w-full hover:-translate-y-0.5 hover:shadow-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40'
          : 'hover:-translate-y-px hover:shadow-sm',
      )}
      {...(clickable ? {
        onClick,
        role: 'button',
        tabIndex: 0,
        'aria-label': actionLabel || label,
        onKeyDown: (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onClick() } },
      } : {})}
    >
      {/* Top accent strip */}
      {topAccent && (
        <div
          className="absolute top-0 inset-x-0 h-[3px]"
          style={{ background: t.color }}
          aria-hidden="true"
        />
      )}

      <div className={cn('flex items-center justify-between gap-2', topAccent && 'mt-0.5')}>
        <span className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground truncate">
          {label}
        </span>
        <div className="flex items-center gap-1.5">
          {clickable && actionLabel && (
            <span
              className={cn(
                'hidden sm:inline-flex items-center gap-1 h-5 px-1.5 rounded-md',
                'text-[10px] font-medium tabular',
                'opacity-0 -translate-x-1 group-hover:opacity-100 group-hover:translate-x-0',
                'group-focus-visible:opacity-100 group-focus-visible:translate-x-0',
                'transition-all duration-base ease-out-quad',
              )}
              style={{
                background: `color-mix(in srgb, ${t.color} 14%, transparent)`,
                color: t.color,
              }}
            >
              {actionLabel}
              <ArrowRight className="size-2.5" aria-hidden="true" />
            </span>
          )}
          {Icon && iconStyle === 'filled' && (
            <span
              className="grid place-items-center rounded-md size-6 shrink-0"
              style={{ background: t.color }}
            >
              <Icon className="size-3 text-white" aria-hidden="true" />
            </span>
          )}
          {Icon && iconStyle === 'subtle' && (
            <span
              className="grid place-items-center rounded-md size-6 shrink-0"
              style={{ background: `color-mix(in srgb, ${t.color} 12%, transparent)`, color: t.color }}
            >
              <Icon className="size-3" aria-hidden="true" />
            </span>
          )}
        </div>
      </div>

      <div className={cn(
        'mt-1.5 font-semibold tracking-tight tabular leading-none',
        !coloredValue && 'text-foreground',
        heroValue ? 'text-2xl' : 'text-lg',
        loading && 'opacity-50',
      )}
        style={coloredValue ? { color: t.color } : undefined}
      >
        {value}
      </div>

      {(sub || (deltaPct !== null && deltaPct !== undefined) || spark) && (
        <div className="mt-1 flex items-end justify-between gap-2">
          <div className="text-[10.5px] text-muted-foreground leading-snug truncate">
            {deltaPct !== null && deltaPct !== undefined && Math.abs(deltaPct) > 0.5 && (
              <span className={cn(
                'inline-flex items-center gap-0.5 mr-1 font-semibold tabular',
                deltaPct >= 0 ? 'text-success' : 'text-danger',
              )}>
                {deltaPct >= 0
                  ? <TrendingUp className="size-2.5" aria-hidden="true" />
                  : <TrendingDown className="size-2.5" aria-hidden="true" />}
                {deltaPct >= 0 ? '+' : ''}{deltaPct.toFixed(1)}%
              </span>
            )}
            {sub}
          </div>
          {Array.isArray(spark) && spark.length >= 3 && (
            <div className="shrink-0 w-[56px] h-[20px] opacity-90" aria-hidden="true">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={spark.map(d => ({ v: Number(d?.total ?? d) || 0 }))}>
                  <Line type="monotone" dataKey="v" stroke={t.color} strokeWidth={1.5} dot={false} isAnimationActive={false} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          )}
        </div>
      )}
    </Card>
  )
}
