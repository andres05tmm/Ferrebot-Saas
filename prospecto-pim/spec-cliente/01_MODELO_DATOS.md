# 01 — Full Data Model (Prisma / PostgreSQL)

> This is the definitive schema. Implemented in phases (see `16_BUILD_ORDER`), but
> designed complete from the start to avoid destructive migrations.
> All `id` are `cuid()`. Everything has `creadoEn` (createdAt) and `actualizadoEn`
> (updatedAt). Soft delete via `eliminadoEn: DateTime?` where applicable.
>
> NOTE: field/model identifiers are kept in Spanish on purpose — they mirror the
> client's domain language and stay consistent across every spec file. Do not rename
> them. If using a Python/SQLAlchemy stack, keep the same identifier names.

## Configuración y parámetros

```prisma
model ConfiguracionEmpresa {
  id                  String   @id @default(cuid())
  nombre              String
  nit                 String
  regimen             String?  // "Responsable de IVA", etc. [DEFINIR con el amigo]
  direccion           String?
  ciudad              String?
  telefono            String?
  email               String?
  logoUrl             String?
  // Config MATIAS API
  matiasApiKey        String?  // encriptado en env, no en DB — ver 15_FACTURACION
  matiasResolucion    String?  // resolución DIAN vigente
  actualizadoEn       DateTime @updatedAt
}

model ParametrosLegales {
  id                    String   @id @default(cuid())
  vigenteDesde          DateTime // ej. 2026-01-01
  vigenteHasta          DateTime? // null = vigente actual
  smmlv                 Decimal  // 2026: 1750905
  auxilioTransporte     Decimal  // 2026: 249095
  auxilioTransporteTopeSmmlv Int @default(2)
  saludEmpleadoPct      Decimal  // ej. 0.04
  saludEmpleadorPct     Decimal  // ej. 0.085 [DEFINIR con contador]
  pensionEmpleadoPct    Decimal  // ej. 0.04
  pensionEmpleadorPct   Decimal  // ej. 0.12 [DEFINIR con contador]
  arlPct                Decimal? // varía por clase de riesgo [DEFINIR]
  cajaCompensacionPct   Decimal  @default(0.04)
  senaPct               Decimal  @default(0.02)
  icbfPct               Decimal  @default(0.03)
  cesantiasPct          Decimal  @default(0.0833)
  interesesCesantiasPct Decimal  @default(0.01)
  primaPct              Decimal  @default(0.0833)
  vacacionesPct         Decimal  @default(0.0417)
  ivaGeneral            Decimal  @default(0.19)
  notas                 String?
}
```

## CRM: Clientes

```prisma
model Cliente {
  id                String   @id @default(cuid())
  nombre            String
  nit               String?
  tipoIdentificacion String?  // "NIT", "CC", "CE" [DEFINIR opciones]
  estatus           EstatusCliente @default(PROSPECTO)
  contactoNombre    String?
  contactoCargo     String?
  contactoTelefono  String?
  contactoEmail     String?
  direccion         String?
  ciudad            String?
  acuerdoComercial  String?  // texto libre: condiciones de pago, descuentos, etc.
  notas             String?
  creadoEn          DateTime @default(now())
  actualizadoEn     DateTime @updatedAt
  eliminadoEn       DateTime?

  cotizaciones      Cotizacion[]
  obras             Obra[]
  facturas          Factura[]
}

enum EstatusCliente {
  PROSPECTO
  ACTIVO
  RECURRENTE
  INACTIVO
  MOROSO
}
```

## Cotizaciones

```prisma
model Cotizacion {
  id                  String   @id @default(cuid())
  numero              String   @unique
  clienteId           String
  cliente             Cliente  @relation(fields: [clienteId], references: [id])
  nombreObra          String
  ubicacion           String?
  fechaEmision        DateTime @default(now())
  vigenciaDias        Int      @default(15)
  administracionPct   Decimal  @default(0)
  imprevistosPct      Decimal  @default(0)
  utilidadPct         Decimal  @default(0)
  ivaSobreUtilidadPct Decimal  @default(0.19)
  estado              EstadoCotizacion @default(BORRADOR)
  condiciones         String?
  items               ItemCotizacion[]
  obra                Obra?    // se crea al ganar
  creadoEn            DateTime @default(now())
  actualizadoEn       DateTime @updatedAt
}

enum EstadoCotizacion { BORRADOR ENVIADA GANADA PERDIDA VENCIDA }

model ItemCotizacion {
  id               String   @id @default(cuid())
  cotizacionId     String
  cotizacion       Cotizacion @relation(fields: [cotizacionId], references: [id], onDelete: Cascade)
  orden            Int
  descripcion      String
  unidad           String
  cantidad         Decimal
  valorUnitario    Decimal
  costoMaterialEst Decimal?
  costoManoObraEst Decimal?
  costoEquipoEst   Decimal?
}
```

## Obras (nacen de una cotización ganada)

```prisma
model Obra {
  id             String   @id @default(cuid())
  cotizacionId   String   @unique
  cotizacion     Cotizacion @relation(fields: [cotizacionId], references: [id])
  clienteId      String
  cliente        Cliente  @relation(fields: [clienteId], references: [id])
  nombre         String
  ubicacion      String?
  fechaInicio    DateTime?
  fechaFinEstimada DateTime?
  fechaFinReal   DateTime?
  estado         EstadoObra @default(PLANIFICADA)
  notas          String?

  asignacionesMaquina AsignacionMaquinaObra[]
  asignacionesTrabajador AsignacionTrabajadorObra[]
  consumosInventario ConsumoInventario[]
  gastos         Gasto[]
  compras        Compra[]
  facturas       Factura[]
  reportesDiarios ReporteDiarioObra[]

  creadoEn       DateTime @default(now())
  actualizadoEn  DateTime @updatedAt
}

enum EstadoObra { PLANIFICADA EN_EJECUCION SUSPENDIDA FINALIZADA LIQUIDADA }
```

## Inventario — Máquinas

```prisma
model Maquina {
  id                 String   @id @default(cuid())
  codigo             String   @unique  // ej. "M-001"
  nombre             String   // ej. "Vibrocompactador CAT CS533E"
  tipo               String   // "vibrocompactador", "minicargador", "hidrofresadora" [DEFINIR catálogo]
  placa              String?
  serial             String?
  anioFabricacion    Int?
  estado             EstadoMaquina @default(DISPONIBLE)
  precioHoraDefault  Decimal  // valor sugerido de facturación por hora
  minimoHorasFactura Int      @default(1) // mínimo de horas facturables por servicio
  operadorAsignadoId String?
  operadorAsignado   Trabajador? @relation("MaquinaOperador", fields: [operadorAsignadoId], references: [id])
  fotoUrl            String?
  notas              String?

  asignaciones       AsignacionMaquinaObra[]
  mantenimientos     Mantenimiento[]
  horasTrabajadas    RegistroHorasMaquina[]

  creadoEn           DateTime @default(now())
  actualizadoEn      DateTime @updatedAt
  eliminadoEn        DateTime?
}

enum EstadoMaquina {
  DISPONIBLE
  OCUPADA
  MANTENIMIENTO
  DAÑADA
  BAJA
}

model AsignacionMaquinaObra {
  id             String   @id @default(cuid())
  maquinaId      String
  maquina        Maquina  @relation(fields: [maquinaId], references: [id])
  obraId         String
  obra           Obra     @relation(fields: [obraId], references: [id])
  fechaInicio    DateTime
  fechaFin       DateTime?
  precioHora     Decimal  // puede diferir del default de la máquina
  minimoHoras    Int
  operadorId     String?
  operador       Trabajador? @relation(fields: [operadorId], references: [id])
  activa         Boolean  @default(true)
}

model RegistroHorasMaquina {
  id             String   @id @default(cuid())
  maquinaId      String
  maquina        Maquina  @relation(fields: [maquinaId], references: [id])
  obraId         String
  fecha          DateTime
  horasTrabajadas Decimal
  horasFacturables Decimal  // aplica el mínimo si aplica
  operadorId     String?
  observaciones  String?
  origenRegistro OrigenRegistro @default(MANUAL)
  creadoEn       DateTime @default(now())
}

enum OrigenRegistro { MANUAL TELEGRAM_BOT IMPORTACION }

model Mantenimiento {
  id             String   @id @default(cuid())
  maquinaId      String
  maquina        Maquina  @relation(fields: [maquinaId], references: [id])
  tipo           TipoMantenimiento
  fecha          DateTime
  horasMaquina   Decimal? // horómetro al momento del mantenimiento
  descripcion    String
  costo          Decimal
  proveedorId    String?
  proveedor      Proveedor? @relation(fields: [proveedorId], references: [id])
  proximoEnHoras Decimal? // para preventivos: cada X horas
  proximoEnFecha DateTime?
  facturaUrl     String?
  creadoEn       DateTime @default(now())
}

enum TipoMantenimiento { PREVENTIVO CORRECTIVO INSPECCION }
```

## Inventario — Herramientas

```prisma
model Herramienta {
  id           String   @id @default(cuid())
  codigo       String   @unique
  nombre       String
  categoria    String?  // [DEFINIR catálogo]
  cantidad     Int      @default(1)
  ubicacionActual String? // obra o bodega
  estado       EstadoHerramienta @default(DISPONIBLE)
  valorReposicion Decimal?
  notas        String?
  creadoEn     DateTime @default(now())
  actualizadoEn DateTime @updatedAt
  eliminadoEn  DateTime?
}

enum EstadoHerramienta { DISPONIBLE EN_OBRA MANTENIMIENTO PERDIDA BAJA }
```

## Empleados / Trabajadores

```prisma
model Trabajador {
  id             String   @id @default(cuid())
  tipoVinculacion TipoVinculacion
  documento      String   @unique
  tipoDocumento  String   @default("CC")
  nombres        String
  apellidos      String
  telefono       String?
  email          String?
  direccion      String?
  cargo          String   // ej. "Operador vibrocompactador"
  fechaIngreso   DateTime?
  fechaRetiro    DateTime?
  activo         Boolean  @default(true)

  // Directos
  salarioBase        Decimal? // para directos
  aplicaAuxTransporte Boolean @default(true)
  eps            String?
  fondoPension   String?
  arl            String?
  cajaCompensacion String?
  cuentaBancaria String?
  bancoNombre    String?

  // Patacaliente (por hora)
  tarifaHora     Decimal?

  maquinasOperadas Maquina[] @relation("MaquinaOperador")
  asignacionesObra AsignacionTrabajadorObra[]
  asignacionesMaquinaObra AsignacionMaquinaObra[]
  registroAsistencia RegistroAsistencia[]
  liquidaciones  DetalleLiquidacion[]

  creadoEn       DateTime @default(now())
  actualizadoEn  DateTime @updatedAt
}

enum TipoVinculacion { DIRECTO PATACALIENTE }

model AsignacionTrabajadorObra {
  id             String   @id @default(cuid())
  trabajadorId   String
  trabajador     Trabajador @relation(fields: [trabajadorId], references: [id])
  obraId         String
  obra           Obra     @relation(fields: [obraId], references: [id])
  fechaInicio    DateTime
  fechaFin       DateTime?
  activa         Boolean  @default(true)
}

model RegistroAsistencia {
  id             String   @id @default(cuid())
  trabajadorId   String
  trabajador     Trabajador @relation(fields: [trabajadorId], references: [id])
  fecha          DateTime
  obraId         String?  // null si es día administrativo/no productivo
  horasTrabajadas Decimal @default(8)
  horasExtraDiurnas Decimal @default(0)
  horasExtraNocturnas Decimal @default(0)
  horasDominicalFestivo Decimal @default(0)
  ausencia       TipoAusencia?
  observaciones  String?
  origenRegistro OrigenRegistro @default(MANUAL)
  creadoEn       DateTime @default(now())
}

enum TipoAusencia { INCAPACIDAD LICENCIA_REMUNERADA LICENCIA_NO_REMUNERADA VACACIONES FALTA_INJUSTIFICADA }
```

## Nómina (quincenas y liquidaciones)

```prisma
model PeriodoNomina {
  id             String   @id @default(cuid())
  fechaInicio    DateTime
  fechaFin       DateTime
  tipo           TipoPeriodoNomina
  estado         EstadoPeriodoNomina @default(ABIERTO)
  detalles       DetalleLiquidacion[]
  cerradoEn      DateTime?
  creadoEn       DateTime @default(now())
}

enum TipoPeriodoNomina { QUINCENAL MENSUAL SEMANAL PATACALIENTE }
enum EstadoPeriodoNomina { ABIERTO CERRADO PAGADO }

model DetalleLiquidacion {
  id                    String   @id @default(cuid())
  periodoNominaId       String
  periodoNomina         PeriodoNomina @relation(fields: [periodoNominaId], references: [id])
  trabajadorId          String
  trabajador            Trabajador @relation(fields: [trabajadorId], references: [id])

  // Devengados
  salarioBase           Decimal @default(0)
  diasTrabajados        Decimal @default(0)
  auxilioTransporte     Decimal @default(0)
  horasExtraDiurnas     Decimal @default(0)
  valorHorasExtraDiurnas Decimal @default(0)
  horasExtraNocturnas   Decimal @default(0)
  valorHorasExtraNocturnas Decimal @default(0)
  dominicalesFestivos   Decimal @default(0)
  valorDominicalesFestivos Decimal @default(0)
  otrosDevengados       Decimal @default(0)
  totalDevengado        Decimal @default(0)

  // Deducciones
  saludEmpleado         Decimal @default(0)
  pensionEmpleado       Decimal @default(0)
  otrasDeducciones      Decimal @default(0)
  totalDeducciones      Decimal @default(0)

  // Neto
  netoPagar             Decimal @default(0)

  // Aportes empleador (para costeo real, no van a la liquidación del trabajador)
  saludEmpleador        Decimal @default(0)
  pensionEmpleador      Decimal @default(0)
  arl                   Decimal @default(0)
  cajaCompensacion      Decimal @default(0)
  sena                  Decimal @default(0)
  icbf                  Decimal @default(0)
  provisionCesantias    Decimal @default(0)
  provisionInteresesCesantias Decimal @default(0)
  provisionPrima        Decimal @default(0)
  provisionVacaciones   Decimal @default(0)

  // Prorrateo por obra
  prorrateo             ProrrateoNominaObra[]

  // DIAN
  cuneDian              String?  // Código único nómina electrónica
  fechaTransmisionDian  DateTime?

  creadoEn              DateTime @default(now())
}

model ProrrateoNominaObra {
  id                    String   @id @default(cuid())
  detalleLiquidacionId  String
  detalleLiquidacion    DetalleLiquidacion @relation(fields: [detalleLiquidacionId], references: [id])
  obraId                String?  // null = nómina general (días no imputables a obra)
  obra                  Obra?    @relation(fields: [obraId], references: [id])
  diasImputados         Decimal
  costoImputado         Decimal  // incluye prestaciones prorrateadas, no solo salario
}
```

## Proveedores y compras

```prisma
model Proveedor {
  id           String   @id @default(cuid())
  nombre       String
  nit          String?
  tipo         TipoProveedor
  contactoNombre String?
  contactoTelefono String?
  contactoEmail String?
  direccion    String?
  ciudad       String?
  notas        String?
  creadoEn     DateTime @default(now())
  actualizadoEn DateTime @updatedAt
  eliminadoEn  DateTime?

  compras      Compra[]
  mantenimientos Mantenimiento[]
}

enum TipoProveedor {
  PLANTA_ASFALTO
  CANTERA_ARENA
  REPUESTOS
  COMBUSTIBLE
  TRANSPORTE
  SERVICIOS
  OTRO
}

model Compra {
  id               String   @id @default(cuid())
  proveedorId      String
  proveedor        Proveedor @relation(fields: [proveedorId], references: [id])
  obraId           String?
  obra             Obra?    @relation(fields: [obraId], references: [id])
  fecha            DateTime
  concepto         String
  categoria        CategoriaCompra
  // Para viajes de material (asfalto/arena) donde se calcula resbalo
  esViajeMaterial  Boolean  @default(false)
  cantidad         Decimal?
  unidad           String?  // "m3", "viaje"
  costoUnitarioCompra Decimal? // lo que le cobra el proveedor
  costoTotalCompra Decimal    // total pagado al proveedor
  precioVentaCliente Decimal? // lo que le cobra al cliente por ese mismo viaje/material
  resbalo          Decimal?   // calculado: precioVentaCliente - costoTotalCompra
  numeroFactura    String?
  facturaUrl       String?
  notas            String?
  creadoEn         DateTime @default(now())
}

enum CategoriaCompra {
  MEZCLA_ASFALTICA
  EMULSION_ASFALTICA
  ARENA_AGREGADO
  REPUESTO
  COMBUSTIBLE_GENERAL
  TRANSPORTE
  SERVICIO_MANTENIMIENTO
  OTRO
}
```

## Gastos y caja menor

```prisma
model Gasto {
  id             String   @id @default(cuid())
  fecha          DateTime
  categoria      CategoriaGasto
  descripcion    String
  monto          Decimal
  obraId         String?  // opcional: imputar a obra específica
  obra           Obra?    @relation(fields: [obraId], references: [id])
  maquinaId      String?  // opcional: imputar a máquina
  responsable    String?  // quién hizo el gasto
  metodoPago     MetodoPago @default(TRANSFERENCIA_BANCOLOMBIA)
  numeroReferencia String? // número de comprobante Bancolombia
  comprobanteUrl String?  // captura almacenada
  origenRegistro OrigenRegistro @default(MANUAL)
  // Metadatos si vino del bot
  telegramMessageId String?
  telegramUserId String?
  requiereRevision Boolean @default(false) // si el bot no pudo extraer con confianza
  creadoEn       DateTime @default(now())
  actualizadoEn  DateTime @updatedAt
}

enum CategoriaGasto {
  REPUESTOS
  MANTENIMIENTO_MAQUINA
  ALMUERZOS
  TRANSPORTE_PERSONAL
  COMBUSTIBLE
  PAPELERIA
  SERVICIOS_PUBLICOS
  ARRIENDO
  IMPUESTOS
  OTRO
}

enum MetodoPago {
  EFECTIVO
  TRANSFERENCIA_BANCOLOMBIA
  TRANSFERENCIA_OTRO_BANCO
  TARJETA_CREDITO
  TARJETA_DEBITO
  CHEQUE
}
```

## Inventario consumible (materiales imputados a obra)

```prisma
model ItemInventario {
  id           String   @id @default(cuid())
  codigo       String   @unique
  nombre       String
  unidad       String
  stockActual  Decimal  @default(0)
  stockMinimo  Decimal  @default(0)
  costoPromedio Decimal @default(0)
  ubicacion    String?
  creadoEn     DateTime @default(now())
  actualizadoEn DateTime @updatedAt

  consumos     ConsumoInventario[]
}

model ConsumoInventario {
  id             String   @id @default(cuid())
  itemInventarioId String
  itemInventario ItemInventario @relation(fields: [itemInventarioId], references: [id])
  obraId         String
  obra           Obra     @relation(fields: [obraId], references: [id])
  fecha          DateTime
  cantidad       Decimal
  costoUnitario  Decimal
  responsable    String?
  observaciones  String?
  creadoEn       DateTime @default(now())
}
```

## Facturación electrónica (integración MATIAS API — DIAN)

```prisma
model Factura {
  id                 String   @id @default(cuid())
  numero             String   @unique
  clienteId          String
  cliente            Cliente  @relation(fields: [clienteId], references: [id])
  obraId             String?
  obra               Obra?    @relation(fields: [obraId], references: [id])
  fechaEmision       DateTime @default(now())
  fechaVencimiento   DateTime?
  subtotal           Decimal
  iva                Decimal
  total              Decimal
  estado             EstadoFactura @default(BORRADOR)
  // Integración DIAN vía MATIAS API
  cufeDian           String?  // Código único factura electrónica
  fechaTransmisionDian DateTime?
  respuestaDianJson  String?  // JSON con respuesta completa de MATIAS/DIAN
  xmlUrl             String?
  pdfUrl             String?
  items              ItemFactura[]
  creadoEn           DateTime @default(now())
  actualizadoEn      DateTime @updatedAt
}

enum EstadoFactura { BORRADOR EMITIDA ACEPTADA_DIAN RECHAZADA_DIAN PAGADA ANULADA }

model ItemFactura {
  id           String   @id @default(cuid())
  facturaId    String
  factura      Factura  @relation(fields: [facturaId], references: [id], onDelete: Cascade)
  orden        Int
  descripcion  String
  unidad       String
  cantidad     Decimal
  valorUnitario Decimal
  ivaPct       Decimal @default(0.19)
  subtotal     Decimal
}
```

## Reportes de campo (Telegram bot)

```prisma
model ReporteDiarioObra {
  id             String   @id @default(cuid())
  obraId         String
  obra           Obra     @relation(fields: [obraId], references: [id])
  fecha          DateTime
  reportadoPor   String?  // trabajador o supervisor
  telegramUserId String?
  avanceDescripcion String?
  m2Ejecutados   Decimal? // opcional según tipo de obra
  m3Ejecutados   Decimal?
  incidentes     String?
  fotoUrls       String[] // array de URLs
  origenRegistro OrigenRegistro @default(TELEGRAM_BOT)
  creadoEn       DateTime @default(now())
}
```

## Autenticación

```prisma
model Usuario {
  id           String   @id @default(cuid())
  email        String   @unique
  passwordHash String
  nombre       String
  rol          RolUsuario @default(OPERADOR)
  telegramUserId String? @unique // para vincular con el bot
  activo       Boolean  @default(true)
  ultimoAcceso DateTime?
  creadoEn     DateTime @default(now())
}

enum RolUsuario { ADMIN CONTADOR SUPERVISOR OPERADOR SOLO_LECTURA }
```

## Notas para Claude Code
- Todos los `Decimal` deben usar `@db.Decimal(18, 4)` para evitar problemas de precisión
  monetaria.
- Todas las fechas son `DateTime` con zona horaria de Bogotá (UTC-5) manejada en app.
- Los índices son responsabilidad de Prisma; agregar `@@index([campo])` en tablas con
  búsquedas frecuentes (ej. `obraId` en Gasto, Compra, DetalleLiquidacion).
- Enums pueden extenderse; agregar valores nuevos requiere migración.
