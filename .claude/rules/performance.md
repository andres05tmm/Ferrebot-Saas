# Performance

## Modelo de IA
- **Haiku** para tareas frecuentes/ligeras y agentes worker.
- **Sonnet** para desarrollo principal y orquestación.
- **Opus** para decisiones de arquitectura y razonamiento profundo.

## Datos y escala
- Una base por empresa: ir siempre por **PgBouncer**; cuidar el tope de conexiones de Postgres.
- Caché compartido en **Redis** (no en memoria del proceso) para multi-instancia.
- Jobs pesados (emisión DIAN, migraciones de todas las empresas) en background (Redis + ARQ), con reintentos.
- Sin paginación en listas pequeñas; agregarla si el volumen crece.
