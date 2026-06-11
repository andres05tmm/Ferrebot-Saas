# Catálogo de oferta — FerreBot SaaS

> **Qué es este documento.** El inventario formal de **todo lo que le podemos ofrecer a un cliente** hoy.
> Sirve para vender con precisión: lo que está marcado **✅ Disponible** se puede prometer y entregar ya;
> lo **🟡 Parcial / Piloto** existe pero no está terminado; lo **🗺️ Roadmap** todavía no se vende.
> **Documento vivo:** cada vez que se incluya algo nuevo, se actualiza aquí (ver el registro al final).
>
> Última actualización: **10 jun 2026**.

---

## 1. Qué vendemos (en una frase)

Un **POS multiempresa con agente de IA y facturación DIAN**: el comercio vende, controla inventario y
caja, **queda legal con la DIAN sin cambiar su forma de vender**, y opera todo desde un **dashboard web**
y un **agente de IA por chat**. Cada cliente recibe una instancia aislada, con su marca y solo las
capacidades que necesita.

El diferenciador frente a Alegra/Siigo no es "otro software contable": es el **agente que vende + factura
en el mismo chat** y el **ajuste al negocio real** (ferretería con fracciones, precio por peso/longitud).

---

## 2. Cómo se entrega (el modelo)

| Elemento | Qué significa para el cliente |
|---|---|
| **Una empresa = una instancia aislada** | Sus datos viven en su propia base; nunca se cruzan con los de otro comercio. |
| **Capacidades activables** | Paga/usa solo lo que necesita; el resto ni lo ve. Se prenden/apagan por empresa. |
| **Dashboard web white-label** | Panel propio con su color/marca (tema por defecto rojo #C8200E, configurable). |
| **Agente de IA por chat** | Registra ventas, consulta inventario y responde en lenguaje natural. |
| **Tiempo real** | Lo que pasa en caja/ventas/inventario se refleja al instante en el panel. |
| **Roles** | `admin` (dueño) ve todo; `vendedor` ve lo suyo. Multi-vendedor opcional. |

---

## 3. Núcleo — lo que **todo** cliente recibe ✅

Activo siempre, sin costo adicional de capacidad:

- **Ventas** — registro de ventas de mostrador (rápida o con búsqueda de catálogo), incluida la **venta
  varia** (ítem sin catálogo, descripción + precio libre).
- **Inventario + Kardex** — stock por producto, movimientos, control de existencias, alerta de stock bajo.
- **Caja** — apertura/cierre, balance del día, cuadre.
- **Gastos** — registro de gastos del día.
- **Clientes** — directorio de clientes.
- **Proveedores** — directorio de proveedores y sus facturas.
- **Reportes** — resumen del día, evolución de ventas, top de productos, resultados.

---

## 4. Capacidades opcionales — el menú de add-ons

Se activan por empresa según lo que el negocio necesite.

| Capacidad | Qué recibe el cliente | Estado |
|---|---|---|
| **Facturación electrónica (DIAN)** | Emite **factura electrónica de venta** ante la DIAN vía MATIAS; campos fiscales de cliente; tab Facturación. | ✅ Disponible |
| **POS electrónico (documento equivalente)** | Cada venta de mostrador **cierra con el documento equivalente POS electrónico** ante la DIAN, automático, con CUDE. *(Lo obligatorio desde jun-2025.)* | ✅ Disponible |
| **Selección de documento por venta** | El cajero elige **POS o Factura** al cobrar; sin elección, sale el documento por defecto del negocio. | 🟡 En despliegue |
| **Estado fiscal en vivo** | Cada venta muestra su estado (POS/Factura · aceptada/pendiente/rechazada) y su CUDE/CUFE, actualizado al instante. | 🟡 En despliegue |
| **Documento soporte (DS-NO)** | Soporte de compras a **no obligados a facturar**, con su resolución propia. | 🟡 Parcial (estructura lista, emisión pendiente) |
| **Notas crédito/débito** | Ajustes/anulaciones de documentos ya emitidos. | 🟡 Parcial (estructura lista, emisión pendiente) |
| **Libro IVA** | Tab Libro IVA con saldos bimestrales. | ✅ Disponible |
| **Compras fiscal** | Compras con soporte tributario; tab Compras fiscal. | ✅ Disponible |
| **Honorarios** | Cuentas de cobro. | 🟡 Parcial |
| **Fiados** | Crédito a clientes y abonos. | ✅ Disponible |
| **Mayorista** | Precio mayorista por producto. | ✅ Disponible |
| **Multi-vendedor** | Más de un vendedor + filtros por vendedor en el panel. | ✅ Disponible |
| **Agente en Telegram** | Bot de ventas/consultas por chat. | ✅ Disponible |
| **Ventas por voz** | El vendedor manda una nota de voz y el bot registra la venta (transcripción Whisper). | ✅ Disponible |

> **Dependencias:** Notas electrónicas requiere Facturación electrónica; Libro IVA requiere Facturación
> electrónica o Compras fiscal; Ventas por voz requiere el bot de Telegram. Se validan al activar.

---

## 5. Fiscal DIAN — el detalle (es la cuña de venta)

El argumento más fuerte hoy es **"te pongo al día con la DIAN sin que cambies tu forma de vender"**: el
tiquete POS de papel ya no soporta costos/gastos desde el 1-jun-2025 para pequeños comercios.

- **Factura electrónica de venta** — ✅ emisión síncrona (la DIAN responde en la misma operación:
  aceptada/rechazada), con CUFE, XML y PDF.
- **POS electrónico (documento equivalente)** — ✅ se emite **automáticamente al cerrar cada venta de
  mostrador**, a consumidor final, con CUDE. La numeración la asigna la DIAN vía MATIAS (sin huecos ni
  colisiones).
- **Una venta = un documento** — si el cliente pide factura, se emite la factura y **no** el POS (sin
  doble documento sobre la misma venta).
- **Conciliación y respaldo** — webhook de MATIAS que confirma aceptación/rechazo/anulación, más un
  proceso de respaldo que recupera documentos estancados. Histórico fiscal con XML conservado.
- **Documento soporte, notas crédito/débito** — 🟡 estructura lista; emisión pendiente de terminar.

**Cómo se ofrece:** "te dejo emitiendo el documento electrónico que la DIAN exige, operando como tu caja
diaria — sin que cambies cómo vendes."

---

## 6. Agente de IA y canales

- **Agente en Telegram** — ✅ registra ventas, consulta inventario/precios, maneja fiados y caja, responde
  en lenguaje natural. ~60% de las ventas simples se resuelven sin llamar a la IA (rápido y barato); el
  resto las razona la IA. Entiende fracciones y unidades del negocio (ej. lija de tantos metros).
- **Ventas por voz** — ✅ nota de voz → venta registrada (Whisper).
- **Agente en WhatsApp (verticales de servicios)** — 🟡 Piloto vía Kapso (BSP) para negocios de
  agendamiento (clínica/spa/beach club): recordatorios de cita, captura de datos.
- **Portar el agente de ventas (POS) a WhatsApp** — 🗺️ Roadmap. Es el mayor cierre de brecha con el
  mercado (87% de mipymes venden por WhatsApp).

---

## 7. Verticales de servicios (no solo retail)

La plataforma no es solo POS de ferretería: el mismo runtime sirve negocios de **servicios** (clínica,
spa, beach club) con un set distinto de capacidades.

- **Agenda / citas** — 🟡 reservas y agenda.
- **Conversaciones** — 🟡 hilo de conversación por cliente.
- **Base de conocimiento / FAQ** — 🟡 respuestas del agente desde el catálogo de servicios.

*(Un negocio de servicios ve Agenda/Conversaciones; no ve Inventario/Kardex. Una ferretería, al revés.)*

---

## 8. Alta del cliente (onboarding)

- **Onboarding declarativo** — ✅ del manifiesto del negocio (servicios, precios, horarios) se aprovisiona
  la instancia completa de forma automática e idempotente.
- **Onboarding mágico** — ✅ el dueño manda una **foto de su lista de precios escrita a mano**, una **nota
  de voz** describiendo su negocio, o el **link de su Instagram**, y la IA **extrae y estructura** todo en
  un catálogo válido listo para aprovisionar. Es el "wow" que baja la fricción de dar de alta a un dueño
  no técnico — y lo que Alegra/Siigo no tienen.
- **Provisión en producción** — ✅ script de alta de empresa (crea base → migra → siembra → secretos →
  admin).

---

## 9. Plataforma y operación (lo que da confianza)

- **Aislamiento por empresa** — ✅ base de datos por cliente; secretos (credenciales DIAN, tokens) cifrados.
- **Panel super-admin** — ✅ alta de empresas, ajuste de capacidades por empresa, estado de provisioning.
- **Login real** — ✅ email/contraseña, recuperación por token; roles por usuario.
- **Tiempo real** — ✅ el panel se actualiza solo ante cada venta/caja/movimiento.
- **Respaldo y continuidad** — ✅ backups + recuperación; observabilidad (errores monitoreados).

---

## 10. En desarrollo / Roadmap (todavía **no** se vende como disponible)

| Capacidad | Qué será | Estado |
|---|---|---|
| **Cobro Bre-B (QR)** | Mostrar QR de cobro inmediato (gratis, 24/7) en el mostrador y conciliarlo solo en caja. | 🗺️ En investigación (ADR pendiente) |
| **Link de pago (Wompi/Bold)** | Cobro de tarjeta/Nequi/PSE por link, como complemento de Bre-B. | 🗺️ Roadmap |
| **Canal WhatsApp Business (retail)** | El agente de ventas/pedidos en WhatsApp, no solo Telegram. | 🗺️ Roadmap |
| **Billing / planes / cuotas** | Suscripciones, medición de uso, estado de pago — para cobrarle al comercio. | 🗺️ Roadmap |
| **Onboarding autoservicio** | El dueño se da de alta solo, de extremo a extremo. | 🟡 Parcial (script existe; panel por pulir) |
| **Nómina electrónica** | Para comercios con empleados (obligación DIAN). | 🗺️ Roadmap |
| **Modo offline (PWA)** | Vender sin internet y sincronizar al reconectar. | 🗺️ Diseño |
| **Adaptadores por vertical** | Restaurante (mesas/comandas), farmacia (lotes/vencimientos). | 🗺️ Roadmap |

> **Importante para vender:** hoy el método de pago es una **etiqueta** (efectivo/transferencia/…), **no
> cobra de verdad**. El cobro real (Bre-B/link) es roadmap. No prometer "cobro integrado" como disponible.

---

## 11. Cómo se posiciona el precio

- Quedar **al nivel o por debajo de Alegra** (su plan Pyme es **$149.900/mes**; Siigo arranca ~$146.000
  con compromiso anual).
- Diferenciar por lo que ellos **no** hacen bien: **agente de IA de ventas por chat**, **POS electrónico
  operando como caja diaria**, **onboarding mágico**, y **ajuste vertical** (fracciones/peso de ferretería).
- El paquete ancla a futuro: **"ponte al día"** = alta con onboarding mágico + POS electrónico operando el
  día 1 + (cuando esté) QR de cobro en mostrador y WhatsApp.

---

## 12. Resumen para un pitch de 30 segundos

> "Te doy un POS con tu marca donde vendes, controlas inventario y caja, y **cada venta queda legal ante la
> DIAN automáticamente** — el documento electrónico que ya es obligatorio. Tu vendedor puede registrar
> ventas **por chat o por voz**, y te doy de alta con solo una **foto de tu lista de precios**. Todo por
> menos de lo que cobra Alegra."

---

## Registro de cambios

| Fecha | Cambio |
|---|---|
| 10 jun 2026 | Versión inicial. POS electrónico marcado **Disponible** (switch-on de Punto Rojo hoy); selector POS/FE y badge de estado fiscal **en despliegue**. |
