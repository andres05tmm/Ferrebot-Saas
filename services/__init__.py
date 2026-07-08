"""Capa de servicios de dominio del vertical construcción (Construcciones PIM).

Paquete NUEVO (plan PIM §4). Alberga la lógica de negocio que no vive en un `modules/*`
concreto porque la comparten varios (cotización, obra, nómina). El subpaquete
`calculations/` son funciones PURAS de dinero: una fórmula = una fuente de verdad,
`Decimal` end-to-end, redondeo solo al final con `core.money.cuantizar` (skill money-safe).
"""
