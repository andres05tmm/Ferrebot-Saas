"""Conciliación bancaria por empresa: movimientos del extracto ↔ movimientos internos.

Ingesta idempotente por referencia, match semi-automático (ventas por transferencia / gastos /
abonos) y confirmación explícita, sin tocar saldos. Ver ADR 0025 (ORM) y ADR 0028 (conciliación).
"""
