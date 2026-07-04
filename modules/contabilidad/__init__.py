"""Motor contable: ledger de doble partida + PUC (ADR 0030).

Capa DERIVADA sobre los eventos operativos (venta, gasto, fiado, compra, devolución,
retención): un proyector idempotente traduce cada evento a UN asiento inmutable. No alimenta
el arqueo híbrido de caja (que sigue leyendo `ventas` + `caja_movimientos`); se concilian por
reporte. Patrón: Odoo `account.move` + Modern Treasury (append-only, reversión en vez de edición).
"""
