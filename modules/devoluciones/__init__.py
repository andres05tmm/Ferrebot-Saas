"""Devoluciones de venta (ADR 0026, Fase 3 Contable B).

Una devolución re-ingresa mercancía al inventario (movimiento DEVOLUCION con el costo del snapshot de
la SALIDA original, no el promedio del día) y su contrapartida de dinero (egreso de caja si la venta
fue en efectivo, abono al fiado si fue a crédito), vinculada a la nota crédito cuando la venta fue
facturada. Idempotente por `idempotency_key`.
"""
