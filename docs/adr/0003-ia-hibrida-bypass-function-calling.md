# ADR 0003 — IA híbrida: bypass + function calling

- Estado: Aceptada
- Fecha: 2026-06

## Contexto
Procesar lenguaje natural con un modelo en cada operación es lento y costoso. FerreBot resuelve ~60% de ventas en Python puro.

## Decisión
**Híbrido:** `bypass` Python para operaciones simples/determinísticas; **function calling** solo cuando hay que interpretar una instrucción ambigua. Proveedor agnóstico (OpenAI o Claude); la capa de herramientas no depende del modelo.

## Consecuencias
- (+) Rápido y barato; el modelo nunca toca la base (solo decide qué herramienta llamar).
- (-) Dos caminos que mantener (bypass y herramientas); requiere buenas pruebas de ambos.
