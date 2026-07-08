"""Funciones puras de cálculo (plan PIM §4) — la ÚNICA verdad de cada fórmula de dinero.

Regla money-safe: nada de `float`, `Decimal` end-to-end, redondeo SOLO al final con
`core.money.cuantizar` (nunca en pasos intermedios), y una función por fórmula que
consumen UI, Excel, PDF y bot por igual (jamás se reimplementa el cálculo inline).

Nota de precisión: la spec del cliente pide almacenar dinero en `Decimal(18,4)` (MONEY4,
por márgenes de 3–4%). El tipo de columna MONEY4 se introduce en Fase 1; aquí el redondeo
de salida se hace con `cuantizar` (2 decimales, centavos), la única verdad de redondeo hoy
disponible en `core.money`. Los porcentajes se cuantizan a 2 decimales igual.
"""
