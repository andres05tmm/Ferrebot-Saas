/*
 * TenantsTable — lista de empresas del panel super-admin (GET /admin/tenants): slug, nombre, estado,
 * features/packs, número de WhatsApp. Cada fila se puede seleccionar para gestionarla (TenantManage).
 */
import { Badge } from '@/components/ui/badge.jsx'
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '@/components/ui/table.jsx'

const ESTADO_VARIANT = { activa: 'success', provisionando: 'warning', inactiva: 'danger' }

export default function TenantsTable({ tenants, onSelect, seleccionado }) {
  if (!tenants.length) {
    return <p className="py-8 text-center text-sm text-muted-foreground">Sin empresas todavía.</p>
  }
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Slug</TableHead>
          <TableHead>Nombre</TableHead>
          <TableHead>Estado</TableHead>
          <TableHead>Features</TableHead>
          <TableHead>WhatsApp</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {tenants.map((t) => (
          <TableRow
            key={t.slug} onClick={() => onSelect?.(t)}
            aria-label={`tenant ${t.slug}`}
            data-selected={seleccionado === t.slug || undefined}
            className={`cursor-pointer ${seleccionado === t.slug ? 'bg-surface-2' : ''}`}
          >
            <TableCell className="font-medium">{t.slug}</TableCell>
            <TableCell>{t.nombre}</TableCell>
            <TableCell>
              <Badge variant={ESTADO_VARIANT[t.estado] || 'default'}>{t.estado}</Badge>
            </TableCell>
            <TableCell>
              <div className="flex flex-wrap gap-1">
                {(t.features || []).length
                  ? t.features.map((f) => <Badge key={f} variant="outline">{f}</Badge>)
                  : <span className="text-muted-foreground">—</span>}
              </div>
            </TableCell>
            <TableCell className="text-muted-foreground">{t.wa_numero || '—'}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}
