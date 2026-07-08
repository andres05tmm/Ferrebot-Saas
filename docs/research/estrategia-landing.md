# Estrategia de landing — Melquiadez

> Síntesis de tres investigaciones (jun 2026): landings de SaaS comparables, mejores prácticas de mensaje/conversión, y estudio del personaje Melquíades para los guiños de marca. La landing actual (`landing/`) ya tiene base Melquíades (estética oro-sobre-tinta, el agente se llama "Melquiadez", sede Cartagena). Esto es para **afilarla**, no rehacerla.
>
> Producto: "empleados digitales por WhatsApp" (agentes IA que atienden, agendan, venden y cobran) para PYMES colombianas (ferreterías, clínicas, restaurantes, hoteles).

## 0. Posicionamiento (hacer ESTO antes de escribir el hero)

Método de April Dunford: el mensaje se construye sobre el posicionamiento, no al revés.

- **Compite contra el statu quo, no contra otros software.** La alternativa real de la PYME no es "otro bot": es **contestar el WhatsApp a mano, perder mensajes, o contratar a alguien**. Ese es el villano de la landing.
- **Categoría que ya entienden:** "atención y ventas automáticas por WhatsApp" — NO "plataforma de agentes IA conversacionales" (jerga que no le dice nada a un ferretero). El 90% de las tech exitosas se posicionan en categorías existentes, no inventan una.
- **Atributo único de Melquiadez:** responde *con los datos reales del negocio* (catálogo, precios, stock, caja) y *ejecuta* (toma el pedido, registra), no solo charla. Ese es el "y qué": vende de verdad, no es un FAQ bonito.

## 1. El hero (la regla de los 5 segundos)

Un visitante debe entender **qué es, para quién y por qué importa en ≤5 s**. Liderar con **resultado**, no con features. El "single-stat hero" (un número grande como titular) fue el patrón más fuerte en un estudio A/B de 2.000 páginas (**+18% vs. hero estándar**). ([Digital Applied](https://www.digitalapplied.com/blog/landing-page-conversion-study-2000-pages-tested-2026))

Reglas: titular ≤10 palabras; nombrar WhatsApp; cerrar con "sin contratar / sin programar"; **cero jerga** (nada de "API de WhatsApp", "LLM", "agente conversacional" en el hero — eso va en el FAQ).

Opciones de hero (elegir/testear una):

- **Outcome + dolor:** *"Deja de perder ventas por no contestar a tiempo en WhatsApp."* / sub: *"Melquiadez atiende, cotiza y toma pedidos por ti, 24/7, con los precios y el stock reales de tu negocio. Sin contratar a nadie, sin programar nada."*
- **Rol/persona (ángulo "empleado digital", validado por Yalo/Landbot/Siigo):** *"Un empleado digital que vende por tu WhatsApp, 24/7."* / sub similar.
- **Single-stat (cuando haya dato del piloto):** *"Punto Rojo responde el 100% de sus chats de WhatsApp sin contratar a nadie."*

CTA del hero: **uno solo dominante** (multi-CTA en el hero = **−8%**). Para Colombia: botón principal *"Pruébalo gratis"* + un *"Hablar por WhatsApp"* secundario discreto (coherente con el producto y con cómo compra la PYME local). Reductores de riesgo bajo el botón: *"Sin tarjeta · Sin permanencia · Listo en 1 día"*.

Mostrar producto en el hero: una **conversación de WhatsApp real** del agente vendiendo/cotizando (el `Telefono.jsx` que ya existe). Las demos interactivas convierten ~2× mejor que screenshots. **Evitar video-hero autoplay (−7%** por penalización de carga).

## 2. Estructura recomendada (orden de alta conversión)

Principio: revelar en orden — **problema → solución → diferenciación → prueba → CTA**. Poner features/screenshots antes del contexto causa abandono.

| # | Sección | Qué dice / cómo se entrega |
|---|---|---|
| 1 | **Hero** | Outcome + WhatsApp + "sin fricción" + 1 CTA + chat de muestra |
| 2 | **Prueba social temprana** | Justo bajo el hero. Para PYME, **nombres de comercios locales reales** valen más que logos genéricos (clientes nombrados con contexto = **+22%**, el patrón de prueba social más alto). Empezar con Punto Rojo + pilotos |
| 3 | **Dolor (PAS)** | Nombrar y agitar la pérdida, **cuantificada en pesos**. (Chatea PRO lo hace brutal: "68% de carritos se abandonan", "cada pedido fallido te cuesta $15.000 COP".) Ej.: *"Cada chat sin responder en 5 minutos es una venta que se va con el de al lado."* |
| 4 | **Cómo funciona (3 pasos)** | Patrón universal. Ej.: *1) Conectas tu WhatsApp · 2) Subes tu catálogo (foto/Excel) · 3) Melquiadez empieza a atender y vender.* "Listo en 1 día." |
| 5 | **Beneficios (no features)** | Beneficio liderando, feature como prueba. *"IA con tu catálogo"* → *"Cotiza el precio correcto sin que tú intervengas."* **No** una grilla de 12-20 features (mata claridad) |
| 6 | **Con vs Sin** | Tabla "Con Melquiadez vs Sin Melquiadez" (Chatea PRO / Manychat la usan): clarísima para PYME no técnica |
| 7 | **Verticales** | El `AcordeonVerticales.jsx` ya existe: ferretería, clínica, restaurante, hotel — un mensaje por vertical |
| 8 | **Testimonios (BAB)** | Before-After-Bridge. **Siempre número + nombre + cargo + negocio** (testimonio único con foto convierte mejor que un muro). Evitar quotes vagas tipo "muy buena experiencia" |
| 9 | **Objeciones / FAQ** | Atacar objeciones REALES (ver §4), no genéricas |
| 10 | **Pricing** | Planes nombrados, **en COP** (Alegra/Siigo dan más confianza local que USD). Si se cobra por uso/conversación, explicarlo claro y temprano |
| 11 | **Reversión de riesgo + CTA final** | Garantía / prueba gratis / "cancela cuando quieras" + repetir el CTA único |

**Móvil (crítico — la PYME entra por celular):** **64% no pasa del primer viewport**; CTA alcanzable con el pulgar al cargar. **Sticky-bottom CTA = +11%.** Formulario **≤3 campos** (idealmente solo WhatsApp/email; cada campo tras el 4º ~corta a la mitad la conversión). **LCP <2s** (la conversión se desploma pasados 4s).

## 3. Patrones a copiar de los competidores (lo más importante)

Confianza ALTA (texto literal de las landings). El más cercano y replicable es **Chatea PRO** (Colombia, WhatsApp, PYME, IA, "sin programar"); para tono local y pricing/FAQ, **Alegra y Siigo**.

1. **Hero = resultado + canal + "sin fricción"** (Chatea PRO, Alegra, Treble).
2. **Número grande de negocios** como prueba social ("+3.000 tiendas"). Empezar con lo que haya, honesto.
3. **Sección de dolor cuantificada en COP** antes de la solución (Chatea PRO).
4. **"Cómo funciona" en 3 pasos** (todos lo usan).
5. **Demo/chat visible** (Treble GIFs, Landbot builder, Botmaker "1.247 conversaciones activas ahora").
6. **Tabla Con/Sin** (Chatea PRO, Manychat).
7. **Reductores de riesgo repetidos en cada CTA** + garantía (Tidio: "o te devolvemos el dinero").
8. **CTA doble + WhatsApp** (self-service + demo + hablar por WhatsApp).
9. **Voz/personaje local de marca** — Siigo lo hace con "Rigo" (acento paisa, *"¡Mijito!"*). **Aquí es donde entra Melquíades** (§5): el personaje ES el diferenciador de marca.
10. **FAQ que ataca objeciones reales:** "¿reemplaza a mi vendedor?", "¿es difícil de instalar?", "¿se equivoca con precios/stock?", "¿usa mi número actual?", "¿suena a robot?", "¿y la DIAN?".

**Anti-patrones (evitar):** hero abstracto sin resultado (Gupshup/Botmaker: "plataforma de agentes IA"); solo "Contáctanos" sin probar ni ver precio (Yalo/Gupshup — mata la conversión PYME); jerga técnica en el hero (BSP/API/LLM); pricing oculto o solo en USD; modelo de créditos escondido hasta el final; testimonios sin cifra ni cargo; sobrecarga de logos/menús; **multi-CTA en el hero; video-hero autoplay; >3 campos de formulario.**

## 4. FAQ — objeciones reales de una ferretería/PYME

- *"¿Reemplaza a mi vendedor?"* → No: lo libera de contestar lo repetitivo; lo importante sigue pasando a un humano (handoff).
- *"¿Es difícil de instalar?"* → Listo en 1 día; subes el catálogo por foto/Excel.
- *"¿Se equivoca con precios o stock?"* → No inventa: responde con tus datos reales; nunca da un precio que no esté en tu catálogo.
- *"¿Usa mi número de WhatsApp actual?"* / *"¿suena a robot?"* / *"¿qué pasa con la DIAN?"* (factura) — responder directo y sin jerga.

## 5. La capa Melquíades (el alma, no la propuesta de valor)

> Regla de oro: el guiño es **condimento, no plato**. Si alguien que no leyó *Cien años de soledad* necesita conocerlo para entender qué vendes, te pasaste. La promesa (IA que ordena ventas, inventario y caja por WhatsApp) debe quedar clara sola; Melquíades es el alma encima.

**Idea-madre:** Melquiadez es *el sabio que trae el prodigio del mundo a tu negocio de pueblo* — como quien te deja "tocar el hielo" por primera vez. Asombro + conocimiento que aparece cuando se necesita + puente con el mundo. Nunca cita literal; siempre nivel temático.

**Motivos visuales** (traducidos a tech limpio, NO folclor):

- **Imán** = atracción/automatización (líneas de campo finas que "atraen" pedidos/clientes a un punto). El motivo más versátil; sirve de logo abstracto.
- **Hielo = claridad:** un acento azul-hielo cristalino sobre la base cálida; lo complejo vuelto transparente. Sin dibujar cubos de hielo: como *sensación* de claridad.
- **Manuscrito/pergamino = el dashboard** (donde se "descifra" el negocio). Marginalia geométrica fina, glifos abstractos (sin sánscrito real). La estética oro-sobre-tinta actual ya va por aquí.
- **Alquimia/instrumentos** (brújula, catalejo, astrolabio, estrella) como sistema de iconos con el mismo grosor de línea.
- Dirección: base limpia y blanca + un hilo fino dorado/ámbar ("el saber") + acento azul-hielo para los momentos de revelación.

**Tono de voz:** el guía cálido que trae la magia del futuro a tu negocio de barrio. Promete **revelar, no reemplazar**. Ejemplos originales (NO del libro):
- *"La tecnología que ya cambia el mundo, ahora en tu mostrador."*
- *"Tu negocio ya tiene todas las respuestas. Melquiadez las hace aparecer cuando las necesitas."*
- Tagline: *"El saber del mundo, en tu negocio."* o *"Prodigios que sí sirven todos los días."* (guiño cómplice: en Macondo los inventos no servían para la vida diaria — aquí, sí).

**Nombres de features (sutiles):** el agente = "Melquiadez"; **Pergamino/Manuscrito** = reportes; **Catalejo/Lente** = analítica que "acerca lo lejano"; **Brújula** = recomendaciones; **Imán** = captación/automatización; **Caravana** = la red de comercios / onboarding.

**Easter eggs (solo los nota quien leyó el libro):** empty state *"Pronto vas a ver tu negocio como nunca lo habías visto"*; releases con nombre de instrumento (Imán, Catalejo, Hielo) en vez de números; microcopy de carga *"Descifrando los números…"*; un glifo de marginalia en el footer/404; un "1967" discreto en los créditos.

**Qué EVITAR (importante):**
- No reproducir texto del libro (la apertura "Muchos años después…", el párrafo del hielo, los manuscritos) — derechos vigentes (GGM, 2014). Todo el copy original.
- No usar nombres propios de la obra como producto (Macondo, Buendía, Aureliano, Úrsula) — cruza a apropiación obvia. Usar objetos/conceptos, no la familia.
- Nada de **estereotipos del gitano** (bola de cristal, aros, cartomancia, acentos): caduco e irrespetuoso. Quédate con "el sabio que llega de lejos con algo bueno".
- Evitar el eje **muerte/peste/esoterismo/apocalipsis** (Macondo arrasado, "cien años de soledad", la estirpe condenada) — es lo opuesto a lo que le prometes a una PYME (claridad y crecimiento). Si usas un 404 tipo "se lo llevó el viento", que sea el único toque y muy leve.
- No vender "magia" como humo: el gancho es *prodigio que SÍ funciona a diario* — la inversión irónica de los inventos inútiles de Macondo.

## 6. Sobre la landing actual y SEO

- Ya existe la base (`Telefono`, `ComoFunciona`, `AcordeonVerticales`, `SeccionDashboard`, `CierreYPie`, estética `AuroraOro`/`Sello`). El trabajo es afilar **mensaje** (hero con resultado, dolor en COP, prueba social nombrada, FAQ de objeciones) y **conversión** (1 CTA, sticky móvil, ≤3 campos, LCP<2s).
- **SEO (la mejora estructural real):** la landing es una SPA de Vite (render en cliente) → débil para Google y previews de WhatsApp/redes. Si rankear/compartir importa, **pre-renderizar/SSG** (vite-plugin prerender o reescribir en Astro), **sin cambiar de hosting** (sigue en Cloudflare). Esto mueve más la aguja que cualquier cambio de plataforma. (Ver discusión de infra: la landing está bien en Cloudflare; no mover a Railway/Vercel.)

## Fuentes principales
- Competidores (texto literal): Chatea PRO, Alegra, Siigo, Yalo, Treble, Leadsales, Landbot, Manychat, Tidio, Intercom, Wati, Botmaker, Gupshup.
- Conversión (estudio A/B 2.000 páginas): [Digital Applied](https://www.digitalapplied.com/blog/landing-page-conversion-study-2000-pages-tested-2026).
- Posicionamiento: [April Dunford](https://www.aprildunford.com/post/a-quickstart-guide-to-positioning).
- Mensaje/estructura: [SaaS Hero](https://www.saashero.net/strategy/b2b-saas-value-prop-messaging/), [Replo](https://www.replo.app/blog/anatomy-of-a-landing-page), [Omniscient (PAS)](https://beomniscient.com/blog/pas-copywriting/).
- Melquíades: [LitCharts](https://www.litcharts.com/lit/one-hundred-years-of-solitude/characters/melquiades), [SparkNotes](https://www.sparknotes.com/lit/solitude/character/melquiades/), [Centro Gabo](https://centrogabo.org/blogs/tres-apuntes-del-natural-para-un-retrato-de-melquiades).
