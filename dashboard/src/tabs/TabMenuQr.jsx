/*
 * TabMenuQr — QR del menú público (F5 Pack Restaurante, ADR 0032 D6). Gateado por 'menu_qr'.
 * Muestra la URL pública del menú del tenant y su QR (SVG generado en el backend) listo para
 * imprimir y pegar en la mesa. Read-only.
 */
import { QrCode } from 'lucide-react'
import { useFetch } from '@/components/shared.jsx'
import { Card } from '@/components/ui/card.jsx'

export default function TabMenuQr() {
  const qrQ = useFetch('/menu-qr')
  const data = qrQ.data

  return (
    <div className="flex flex-col gap-3 max-w-lg">
      <h1 className="text-base font-semibold inline-flex items-center gap-2">
        <QrCode className="size-4.5 text-primary" /> Menú QR
      </h1>
      <Card className="p-4 space-y-3 text-center">
        {data ? (
          <>
            {/* SVG generado por el backend (segno) — sin dependencias en el front. */}
            <div className="flex justify-center" dangerouslySetInnerHTML={{ __html: data.svg }} />
            <a className="text-sm text-primary underline break-all" href={data.url}
              target="_blank" rel="noreferrer">{data.url}</a>
            <p className="text-[12px] text-muted-foreground">
              Imprime este QR y pégalo en las mesas: abre el menú público con botón de pedido por WhatsApp.
            </p>
          </>
        ) : (
          <p className="text-[13px] text-muted-foreground">Cargando…</p>
        )}
      </Card>
    </div>
  )
}
