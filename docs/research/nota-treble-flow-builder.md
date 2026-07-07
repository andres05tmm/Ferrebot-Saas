# Nota de diseño — Lecciones del constructor visual de Treble.ai

> Observaciones a partir del flow-builder de Treble (lienzo de nodos para armar conversaciones: saludo → menú → ramas → acciones/integraciones/handoff). Qué tomar y qué NO.

## Advertencia: paradigma distinto, no copiar tal cual

- **Treble = marketing/outbound:** campañas estructuradas donde un árbol de decisiones predefinido ("elige opción 1/2/3") tiene sentido.
- **FerreBot = operativo/inbound:** el ferretero dice "2 cemento gris efectivo" y se ejecuta. La apuesta (bypass + function calling) es *evitar* menús rígidos y entender lenguaje natural.
- Un constructor de flujos al estilo chatbot clásico **iría en contra** de la filosofía agéntica de FerreBot para la operación diaria. No portarlo para el flujo de venta.

## Lo que SÍ vale la pena

1. **Treble también es híbrido (validación externa).** Su nodo "Automatiza tareas complejas" tiene un campo de *Instrucciones* con contexto + base de conocimiento para un asistente de IA. Es decir: flujos deterministas para lo estructurado + nodo de IA para lo abierto = el mismo split bypass/LLM de FerreBot, confirmado desde otro producto.

2. **Superficie de configuración visual para no-técnicos (la idea fuerte).** El valor no es el árbol de conversación, sino que alguien sin código pueda configurar el comportamiento. Equivalente para FerreBot: un panel donde el dueño (o el operador SaaS al dar de alta un tenant) configure sin tocar código:
   - plantillas de mensajes,
   - las señales de la capa de misión proactiva (ver `nota-capa-mision-proactiva.md`),
   - umbrales de confirmación / límites de monto,
   - aliases del catálogo (typos → producto),
   - feature flags por empresa.

3. **Pulido de producto.** La sensación "profesional" (tipografía, espaciado, estados claros) es alcanzable en el dashboard React existente. Es diseño, no arquitectura.

## Dónde un "flow-builder lite" sí encajaría (futuro)

Para **outbound estructurado**, no para la operación de venta:
- autorear campañas / broadcasts,
- flujos de onboarding de un cliente nuevo.

Ese es el terreno natural de Treble y el único punto donde un constructor de flujos aportaría a FerreBot.
