# Diagramas

> Vistas en Mermaid: arquitectura/topología, modelo entidad-relación (control DB y app DB) y secuencias de los flujos críticos. Fuentes: `architecture.md`, `tenancy.md`, `infra-railway.md`, `schema.md`, `facturacion-dian.md`, `offline-sync.md`, `ai-tools.md`.

---

## 1. Arquitectura general

```mermaid
flowchart TB
  subgraph Clientes
    TG["Telegram (1 bot por empresa)"]
    WEB["Dashboard PWA (white-label, offline)<br/>empresa.app.dominio"]
  end

  subgraph Railway
    API["Servicio api (FastAPI)<br/>N réplicas"]
    BOT["Servicio bot<br/>webhook /tg/{slug}"]
    WORKER["Worker ARQ<br/>DIAN · provisioning · migraciones"]
    PGB["PgBouncer (transaction)"]
    REDIS["Redis (cola ARQ + caché)"]
    CTRL[("Control DB")]
    TEN[("App DB por empresa<br/>1..N")]
  end

  subgraph Externos
    MATIAS["MATIAS / DIAN"]
    CLOUD["Cloudinary"]
    SENTRY["Sentry"]
    UPTIME["Monitor de uptime"]
  end

  WEB -->|HTTPS + JWT| API
  TG -->|webhook| BOT
  BOT --> API

  API -->|CRUD| PGB
  BOT -->|CRUD| PGB
  WORKER -->|CRUD| PGB
  PGB --> CTRL
  PGB --> TEN

  API -.->|LISTEN directo SSE| TEN
  API <--> REDIS
  WORKER <--> REDIS

  WORKER -->|emisión async| MATIAS
  API -->|imágenes| CLOUD
  API -.-> SENTRY
  UPTIME -.->|/health| API
```

---

## 2. Resolución de tenant (middleware)

```mermaid
flowchart TD
  R["Request entra"] --> SRC{"¿Origen?"}
  SRC -->|"Dashboard/API"| SUB["subdominio → slug<br/>(o claim tenant_id del JWT)"]
  SRC -->|"Bot"| PATH["ruta /tg/{slug}"]
  SRC -->|"Job/cron"| EXPL["tenant_id explícito"]

  SUB --> CACHE
  PATH --> CACHE
  EXPL --> CACHE["control_cache.get(slug)<br/>TTL corto"]

  CACHE --> OK{"¿empresa activa?"}
  OK -->|No| DENY["403 / 404<br/>NO se toca ninguna base"]
  OK -->|Sí| BIND["request.state.tenant = empresa<br/>engine_cache → Session (vía PgBouncer)"]
  BIND --> AUTH{"JWT.tenant_id == empresa?"}
  AUTH -->|No| DENY
  AUTH -->|Sí| GO["Servicio de dominio<br/>(logs con tenant_id + request_id)"]
```

---

## 3. ER — Control DB (plano de control)

```mermaid
erDiagram
  EMPRESAS ||--|| TENANT_DATABASES : "tiene"
  EMPRESAS ||--o{ SUSCRIPCIONES : "tiene"
  EMPRESAS ||--o{ SECRETOS_EMPRESA : "guarda"
  EMPRESAS ||--|| BRANDING : "tiene"
  EMPRESAS ||--o{ EMPRESA_FEATURES : "override"
  PLANES ||--o{ EMPRESAS : "clasifica"
  PLANES ||--o{ SUSCRIPCIONES : "define"

  EMPRESAS {
    bigserial id PK
    text nombre
    text nit UK
    text slug UK
    tenant_estado estado
    bigint plan_id FK
  }
  TENANT_DATABASES {
    bigint empresa_id PK
    text db_name
    text host
    bytea connection_url_cifrada
    text region
  }
  PLANES {
    bigserial id PK
    text nombre
    jsonb limites
    numeric precio_mensual
  }
  SUSCRIPCIONES {
    bigserial id PK
    bigint empresa_id FK
    bigint plan_id FK
    suscripcion_estado estado
    date periodo_inicio
    date periodo_fin
  }
  SECRETOS_EMPRESA {
    bigserial id PK
    bigint empresa_id FK
    text clave
    bytea valor_cifrado
    bytea nonce
  }
  BRANDING {
    bigint empresa_id PK
    text logo_url
    text color_primario
    text nombre_comercial
    text dominio
  }
  EMPRESA_FEATURES {
    bigint empresa_id FK
    text feature
    boolean habilitada
  }
  SUPER_ADMINS {
    bigserial id PK
    text email UK
    text nombre
    text password_hash
  }
```

---

## 4. ER — App DB por empresa (esquema de negocio)

> Sin columna `empresa_id`: la base ES la frontera del tenant.

```mermaid
erDiagram
  PRODUCTOS ||--|| INVENTARIO : "tiene"
  PRODUCTOS ||--o{ MOVIMIENTOS_INVENTARIO : "kardex"
  PRODUCTOS ||--o{ VENTAS_DETALLE : "se vende"
  PRODUCTOS ||--o{ COMPRAS_DETALLE : "se compra"

  USUARIOS ||--o{ VENTAS : "vende"
  USUARIOS ||--o{ MOVIMIENTOS_INVENTARIO : "registra"
  USUARIOS ||--o{ CAJA : "opera"
  USUARIOS ||--o{ GASTOS : "registra"

  CLIENTES ||--o{ VENTAS : "compra"
  CLIENTES ||--o{ FIADOS : "debe"
  CLIENTES ||--o{ CUENTAS_COBRO : "honorarios"

  VENTAS ||--o{ VENTAS_DETALLE : "contiene"
  VENTAS ||--o{ FIADOS : "origina"
  VENTAS ||--o| FACTURAS_ELECTRONICAS : "factura"

  PROVEEDORES ||--o{ COMPRAS : "provee"
  COMPRAS ||--o{ COMPRAS_DETALLE : "contiene"
  COMPRAS ||--o| COMPRAS_FISCAL : "soporte"

  CAJA ||--o{ CAJA_MOVIMIENTOS : "registra"
  CAJA ||--o{ GASTOS : "egresa"

  FIADOS ||--o{ FIADOS_MOVIMIENTOS : "cargo/abono"

  FACTURAS_ELECTRONICAS ||--o{ NOTAS_ELECTRONICAS : "ajusta"
  FACTURAS_ELECTRONICAS ||--o{ EVENTOS_DIAN : "traza"

  PRODUCTOS {
    bigserial id PK
    text codigo UK
    text nombre
    numeric precio_venta
    numeric precio_mayorista
    smallint iva
    boolean permite_fraccion
    boolean activo
  }
  INVENTARIO {
    bigint producto_id PK
    numeric stock_actual
    numeric stock_minimo
  }
  MOVIMIENTOS_INVENTARIO {
    bigserial id PK
    bigint producto_id FK
    mov_inventario_tipo tipo
    numeric cantidad
    numeric costo_unitario
    text referencia
    bigint usuario_id FK
  }
  VENTAS {
    bigserial id PK
    bigint consecutivo UK
    bigint cliente_id FK
    bigint vendedor_id FK
    numeric subtotal
    numeric impuestos
    numeric total
    metodo_pago metodo_pago
    venta_estado estado
    venta_origen origen
    text idempotency_key UK
  }
  VENTAS_DETALLE {
    bigserial id PK
    bigint venta_id FK
    bigint producto_id FK
    text descripcion
    numeric cantidad
    numeric precio_unitario
    smallint iva
  }
  CLIENTES {
    bigserial id PK
    text nombre
    text tipo_documento
    text documento
    text ciudad_dane
    text regimen
    numeric saldo_fiado
  }
  PROVEEDORES {
    bigserial id PK
    text nombre
    text nit
  }
  COMPRAS {
    bigserial id PK
    bigint proveedor_id FK
    numeric total
  }
  COMPRAS_DETALLE {
    bigserial id PK
    bigint compra_id FK
    bigint producto_id FK
    numeric cantidad
    numeric costo
  }
  COMPRAS_FISCAL {
    bigserial id PK
    bigint compra_id FK
    text proveedor_nit
    numeric base
    numeric iva
    numeric total
    text soporte_url
  }
  CAJA {
    bigserial id PK
    bigint usuario_id FK
    numeric saldo_inicial
    numeric saldo_esperado
    numeric saldo_contado
    numeric diferencia
    caja_estado estado
  }
  CAJA_MOVIMIENTOS {
    bigserial id PK
    bigint caja_id FK
    caja_mov_tipo tipo
    numeric monto
    text concepto
    text referencia
  }
  GASTOS {
    bigserial id PK
    gasto_categoria categoria
    numeric monto
    text concepto
    bigint caja_id FK
    bigint usuario_id FK
  }
  FIADOS {
    bigserial id PK
    bigint cliente_id FK
    bigint venta_id FK
    numeric monto
    numeric saldo
  }
  FIADOS_MOVIMIENTOS {
    bigserial id PK
    bigint fiado_id FK
    fiado_mov_tipo tipo
    numeric monto
  }
  CUENTAS_COBRO {
    bigserial id PK
    bigint consecutivo
    bigint cliente_id FK
    text concepto
    numeric monto
    text estado
  }
  FACTURAS_ELECTRONICAS {
    bigserial id PK
    bigint venta_id FK
    fe_tipo tipo
    text prefijo
    bigint consecutivo
    text cufe
    fe_estado estado
    text idempotency_key UK
    smallint intentos
  }
  NOTAS_ELECTRONICAS {
    bigserial id PK
    bigint factura_id FK
    text tipo
    text motivo
    text cufe
    fe_estado estado
  }
  EVENTOS_DIAN {
    bigserial id PK
    bigint factura_id FK
    text evento
    text estado
    jsonb payload
  }
  USUARIOS {
    bigserial id PK
    bigint telegram_id UK
    text nombre
    usuario_rol rol
    boolean activo
  }
```

---

## 5. Secuencia — Venta desde el dashboard (con SSE)

```mermaid
sequenceDiagram
  autonumber
  participant B as Navegador (PWA)
  participant API as api (FastAPI)
  participant MW as TenantMiddleware
  participant DB as App DB empresa (vía PgBouncer)
  participant L as Listener SSE empresa
  participant B2 as Otros dashboards empresa

  B->>API: POST /api/v1/ventas (JWT, Idempotency-Key)
  API->>MW: resolver empresa (subdominio/JWT)
  MW->>MW: control_cache → empresa activa
  MW-->>API: request.state.tenant
  API->>API: auth (tenant_id coincide, rol vendedor)
  API->>DB: get_tenant_db() → Session
  API->>DB: valida stock, calcula totales
  API->>DB: INSERT ventas + ventas_detalle + movimientos_inventario (1 txn)
  Note over API,DB: respeta Idempotency-Key (UNIQUE)
  DB-->>API: commit OK
  API->>DB: pg_notify('ferrebot_events', venta_registrada)
  DB-->>L: NOTIFY (conexión directa)
  L-->>B2: SSE venta_registrada
  API-->>B: 201 { venta_id, consecutivo, total }
```

---

## 6. Secuencia — Mensaje al bot (bypass vs function calling)

```mermaid
sequenceDiagram
  autonumber
  participant U as Usuario (Telegram/voz)
  participant BOT as bot
  participant BP as Bypass router
  participant LLM as Modelo (Haiku/Sonnet)
  participant SVC as Servicio de dominio
  participant DB as App DB empresa

  U->>BOT: "2 cemento gris efectivo"
  BOT->>BOT: @protegido (chat_id → usuario) + resolver tenant
  BOT->>BP: clasificar(mensaje)
  alt match inequívoco y política OK
    BP->>DB: resolver producto (único)
    BP->>SVC: registrar_venta(items, ctx)
  else ambiguo / riesgo / multi-intención
    BP-->>BOT: FALLBACK
    BOT->>LLM: mensaje + catálogo de tools + contexto (RAG)
    LLM-->>BOT: tool_call registrar_venta(args)
    BOT->>SVC: despachar(registrar_venta, ctx)
    SVC-->>LLM: tool_result
    LLM-->>BOT: respuesta en lenguaje natural
  end
  SVC->>DB: INSERT venta + detalle + kardex (1 txn, idempotente)
  DB-->>SVC: commit + pg_notify
  BOT-->>U: "Venta #1234 por $66.640 registrada."
```

---

## 7. Secuencia — Emisión DIAN asíncrona

```mermaid
sequenceDiagram
  autonumber
  participant API as api
  participant Q as Redis (cola ARQ)
  participant W as worker
  participant M as MATIAS
  participant D as DIAN
  participant DB as App DB empresa

  API->>DB: crea factura (estado=pendiente, reserva consecutivo)
  API->>Q: encola emitir_documento(factura_id)
  API-->>API: pg_notify factura_pendiente (SSE)
  W->>Q: toma el job
  W->>DB: lee factura pendiente + secretos (descifra en memoria)
  W->>M: enviar documento (UBL 2.1, _get_city_id caché)
  alt aceptado por MATIAS
    M-->>W: en proceso
    W->>DB: estado=enviada
    M->>D: procesa
    D-->>M: CUFE + PDF + XML
    M->>API: POST /webhooks/matias (firmado)
    API->>DB: estado=aceptada (CUFE, urls) + pg_notify factura_aceptada
  else error transitorio
    M-->>W: error
    W->>DB: estado=error, intentos++
    W->>Q: re-encola con backoff
    Note over W,Q: tras N intentos → dead-letter + alerta
  end
  Note over API,DB: reconciliar_pendientes() (cron) consulta 'enviada' sin webhook
```

---

## 8. Secuencia — Sincronización offline

```mermaid
sequenceDiagram
  autonumber
  participant SW as PWA (sin red)
  participant IDB as IndexedDB (cola)
  participant API as api
  participant DB as App DB empresa

  Note over SW,IDB: vende offline → encola con idempotency_key + secuencia
  SW->>IDB: guarda venta (pendiente de sincronizar)
  Note over SW: vuelve la conexión
  SW->>API: POST /api/v1/ventas/sync (lote, refresh token si expiró)
  loop por cada operación (en orden de secuencia)
    API->>DB: ¿idempotency_key existe?
    alt nueva
      API->>DB: aplica venta (consecutivo y fecha los pone el servidor)
      Note over API,DB: stock insuficiente → acepta igual, marca revisión (AJUSTE)
      API-->>SW: aplicada
    else ya procesada
      API-->>SW: duplicada (devuelve resultado original)
    end
  end
  Note over API,DB: si la venta tenía factura pendiente → encola emisión DIAN
  SW->>IDB: marca confirmadas / limpia cola
```

---

## 9. Secuencia — Aprovisionamiento de una empresa

```mermaid
sequenceDiagram
  autonumber
  participant SA as super_admin
  participant API as api (/admin)
  participant Q as Redis (ARQ)
  participant W as worker
  participant PG as Postgres (admin directo)
  participant CTRL as Control DB
  participant TEN as Nueva App DB
  participant TGM as Telegram

  SA->>API: POST /admin/empresas (nombre, nit, slug, plan)
  API->>CTRL: INSERT empresas (estado=provisionando)
  API->>Q: encola provision_tenant(slug,...)
  W->>PG: CREATE DATABASE empresa
  W->>CTRL: INSERT tenant_databases (url cifrada)
  W->>TEN: alembic upgrade head (árbol tenant)
  W->>TEN: sembrar (categorías, métodos de pago, config)
  W->>CTRL: guardar secretos cifrados + branding + features del plan
  W->>TEN: crear usuario admin
  W->>TGM: set webhook /tg/{slug}
  W->>CTRL: empresas.estado = activa
  Note over W: idempotente y reintentable
  W-->>SA: smoke test (venta + factura de prueba)
```
