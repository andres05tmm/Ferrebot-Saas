--
-- PostgreSQL database dump
--

\restrict eScIh9Z161oxhX8XZwU6YLXlz6rnfsiCqBtkTnpZfhmE1ubfpiBVSsOQJeUBuWb

-- Dumped from database version 18.3 (Debian 18.3-1.pgdg13+1)
-- Dumped by pg_dump version 18.3

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: pg_trgm; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public;


--
-- Name: EXTENSION pg_trgm; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION pg_trgm IS 'text similarity measurement and index searching based on trigrams';


--
-- Name: set_updated_at(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.set_updated_at() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
                BEGIN
                    NEW.updated_at = NOW();
                    RETURN NEW;
                END;
                $$;


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: alembic_version; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.alembic_version (
    version_num character varying(32) NOT NULL
);


--
-- Name: aliases; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.aliases (
    termino character varying(200) NOT NULL,
    reemplazo character varying(300) NOT NULL,
    created_at timestamp without time zone DEFAULT now(),
    updated_at timestamp without time zone DEFAULT now()
);


--
-- Name: api_costo_diario; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.api_costo_diario (
    id integer NOT NULL,
    fecha date NOT NULL,
    vendedor_id bigint NOT NULL,
    modelo text NOT NULL,
    llamadas integer DEFAULT 0 NOT NULL,
    input_tokens bigint DEFAULT 0 NOT NULL,
    cache_read_tokens bigint DEFAULT 0 NOT NULL,
    cache_created_tokens bigint DEFAULT 0 NOT NULL,
    output_tokens bigint DEFAULT 0 NOT NULL,
    costo_usd numeric(12,6) DEFAULT 0 NOT NULL,
    creado timestamp with time zone DEFAULT now() NOT NULL,
    actualizado timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: api_costo_diario_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.api_costo_diario_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: api_costo_diario_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.api_costo_diario_id_seq OWNED BY public.api_costo_diario.id;


--
-- Name: audio_logs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.audio_logs (
    id integer NOT NULL,
    chat_id bigint NOT NULL,
    vendedor text NOT NULL,
    texto_original text NOT NULL,
    texto_corregido text NOT NULL,
    duracion_seg double precision,
    fecha timestamp with time zone DEFAULT now(),
    canal text,
    turn_id text,
    session_id text,
    no_speech_prob double precision,
    descartado_silencio boolean,
    modelo text,
    riel text,
    latencia_stt_ms integer,
    latencia_claude_ms integer,
    pendiente boolean,
    resultado text
);


--
-- Name: audio_logs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.audio_logs_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: audio_logs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.audio_logs_id_seq OWNED BY public.audio_logs.id;


--
-- Name: bancolombia_transferencias; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.bancolombia_transferencias (
    id integer NOT NULL,
    gmail_message_id text NOT NULL,
    fecha date NOT NULL,
    hora text DEFAULT ''::text NOT NULL,
    monto bigint DEFAULT 0 NOT NULL,
    remitente text DEFAULT ''::text NOT NULL,
    descripcion text DEFAULT ''::text NOT NULL,
    tipo_transaccion text DEFAULT ''::text NOT NULL,
    referencia text DEFAULT ''::text NOT NULL,
    notificado boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: bancolombia_transferencias_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.bancolombia_transferencias_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: bancolombia_transferencias_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.bancolombia_transferencias_id_seq OWNED BY public.bancolombia_transferencias.id;


--
-- Name: caja; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.caja (
    id integer NOT NULL,
    fecha date NOT NULL,
    abierta boolean DEFAULT false,
    monto_apertura integer DEFAULT 0,
    efectivo integer DEFAULT 0,
    transferencias integer DEFAULT 0,
    datafono integer DEFAULT 0,
    cerrada_at timestamp without time zone,
    created_at timestamp without time zone DEFAULT now()
);


--
-- Name: caja_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.caja_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: caja_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.caja_id_seq OWNED BY public.caja.id;


--
-- Name: clientes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.clientes (
    id integer NOT NULL,
    nombre character varying(300) NOT NULL,
    tipo_id character varying(10),
    identificacion character varying(50),
    tipo_persona character varying(20),
    correo character varying(200),
    telefono character varying(50),
    created_at timestamp without time zone DEFAULT now(),
    direccion character varying(300),
    regimen_fiscal integer DEFAULT 2,
    municipio_dian integer DEFAULT 149,
    pais_id integer DEFAULT 45,
    ciudad_nombre character varying(120) DEFAULT 'Cartagena'::character varying
);


--
-- Name: clientes_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.clientes_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: clientes_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.clientes_id_seq OWNED BY public.clientes.id;


--
-- Name: compras; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.compras (
    id integer NOT NULL,
    fecha date NOT NULL,
    hora time without time zone,
    proveedor character varying(200),
    producto_id integer,
    producto_nombre character varying(300) NOT NULL,
    cantidad numeric(10,3) NOT NULL,
    costo_unitario integer,
    costo_total integer,
    created_at timestamp without time zone DEFAULT now(),
    usuario_id integer,
    incluye_iva boolean DEFAULT false,
    tarifa_iva integer DEFAULT 0,
    compra_fiscal_id integer,
    factura_proveedor_id character varying(20),
    estado_fiscal character varying(20) DEFAULT 'sin_factura'::character varying
);


--
-- Name: compras_fiscal; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.compras_fiscal (
    id integer NOT NULL,
    fecha date NOT NULL,
    hora time without time zone,
    proveedor character varying(200),
    producto_id integer,
    producto_nombre character varying(300) NOT NULL,
    cantidad numeric(10,3) NOT NULL,
    costo_unitario integer,
    costo_total integer,
    incluye_iva boolean DEFAULT false,
    tarifa_iva integer DEFAULT 0,
    numero_factura character varying(100),
    notas_fiscales text,
    compra_origen_id integer,
    usuario_id integer,
    created_at timestamp without time zone DEFAULT now(),
    updated_at timestamp without time zone DEFAULT now(),
    gmail_message_id character varying(200),
    cufe_proveedor character varying(300),
    evento_030_at timestamp without time zone,
    evento_031_at timestamp without time zone,
    evento_032_at timestamp without time zone,
    evento_033_at timestamp without time zone,
    evento_estado character varying(20) DEFAULT 'pendiente'::character varying,
    evento_error text,
    estado_vinculacion character varying(20) DEFAULT 'sin_vincular'::character varying
);


--
-- Name: compras_fiscal_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.compras_fiscal_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: compras_fiscal_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.compras_fiscal_id_seq OWNED BY public.compras_fiscal.id;


--
-- Name: compras_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.compras_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: compras_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.compras_id_seq OWNED BY public.compras.id;


--
-- Name: config; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.config (
    clave character varying(100) CONSTRAINT config_sistema_clave_not_null NOT NULL,
    valor text,
    updated_at timestamp without time zone DEFAULT now()
);


--
-- Name: conversaciones_bot; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.conversaciones_bot (
    id bigint NOT NULL,
    chat_id bigint NOT NULL,
    vendedor_id bigint,
    role text NOT NULL,
    content text NOT NULL,
    modelo text,
    tokens_input integer,
    tokens_output integer,
    creado timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT conversaciones_bot_role_check CHECK ((role = ANY (ARRAY['user'::text, 'assistant'::text, 'system'::text])))
);


--
-- Name: conversaciones_bot_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.conversaciones_bot_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: conversaciones_bot_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.conversaciones_bot_id_seq OWNED BY public.conversaciones_bot.id;


--
-- Name: cuentas_cobro; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.cuentas_cobro (
    id integer NOT NULL,
    consecutivo integer NOT NULL,
    numero_display character varying(10) NOT NULL,
    fecha date NOT NULL,
    periodo character varying(30) NOT NULL,
    concepto text NOT NULL,
    valor numeric(15,2) NOT NULL,
    pdf_bytes bytea,
    enviado_telegram boolean DEFAULT false,
    creado_at timestamp with time zone DEFAULT now()
);


--
-- Name: cuentas_cobro_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.cuentas_cobro_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: cuentas_cobro_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.cuentas_cobro_id_seq OWNED BY public.cuentas_cobro.id;


--
-- Name: documentos_soporte; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.documentos_soporte (
    id integer NOT NULL,
    consecutivo character varying(20),
    fecha date,
    valor numeric(12,2),
    cude character varying(200),
    estado_dian character varying(50),
    cuenta_cobro_id integer,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: documentos_soporte_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.documentos_soporte_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: documentos_soporte_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.documentos_soporte_id_seq OWNED BY public.documentos_soporte.id;


--
-- Name: facturas_abonos; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.facturas_abonos (
    id integer NOT NULL,
    factura_id character varying(20),
    monto integer NOT NULL,
    fecha date NOT NULL,
    foto_url text DEFAULT ''::text,
    foto_nombre character varying(300) DEFAULT ''::character varying,
    created_at timestamp without time zone DEFAULT now()
);


--
-- Name: facturas_abonos_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.facturas_abonos_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: facturas_abonos_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.facturas_abonos_id_seq OWNED BY public.facturas_abonos.id;


--
-- Name: facturas_electronicas; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.facturas_electronicas (
    id integer NOT NULL,
    venta_id integer,
    numero character varying(30) NOT NULL,
    cufe character varying(200),
    fecha_emision timestamp without time zone DEFAULT now(),
    estado character varying(20) DEFAULT 'emitida'::character varying,
    cliente_nombre character varying(300),
    total integer,
    error_msg text,
    created_at timestamp without time zone DEFAULT now(),
    tipo character varying(20) DEFAULT 'factura'::character varying NOT NULL,
    razon_id smallint,
    factura_cufe_ref character varying(200)
);


--
-- Name: facturas_electronicas_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.facturas_electronicas_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: facturas_electronicas_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.facturas_electronicas_id_seq OWNED BY public.facturas_electronicas.id;


--
-- Name: facturas_proveedores; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.facturas_proveedores (
    id character varying(20) NOT NULL,
    proveedor character varying(200) NOT NULL,
    descripcion character varying(500),
    total integer NOT NULL,
    pagado integer DEFAULT 0,
    pendiente integer NOT NULL,
    estado character varying(20) DEFAULT 'pendiente'::character varying,
    fecha date NOT NULL,
    foto_url text DEFAULT ''::text,
    foto_nombre character varying(300) DEFAULT ''::character varying,
    created_at timestamp without time zone DEFAULT now(),
    usuario_id integer
);


--
-- Name: ferrebot_config; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ferrebot_config (
    clave character varying(100) NOT NULL,
    valor text,
    updated_at timestamp without time zone DEFAULT now()
);


--
-- Name: fiados; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.fiados (
    id integer CONSTRAINT fiados_new_id_not_null NOT NULL,
    cliente_id integer,
    cliente_nombre character varying(300) CONSTRAINT fiados_new_cliente_nombre_not_null NOT NULL,
    saldo_actual integer DEFAULT 0,
    ultima_actualizacion timestamp without time zone DEFAULT now(),
    notas text,
    created_at timestamp without time zone DEFAULT now()
);


--
-- Name: fiados_movimientos; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.fiados_movimientos (
    id bigint NOT NULL,
    fiado_id integer NOT NULL,
    fecha date DEFAULT CURRENT_DATE NOT NULL,
    hora time without time zone DEFAULT (now())::time without time zone NOT NULL,
    concepto text DEFAULT ''::text NOT NULL,
    cargo numeric(15,2) DEFAULT 0 NOT NULL,
    abono numeric(15,2) DEFAULT 0 NOT NULL,
    saldo_resultante numeric(15,2) NOT NULL,
    creado_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT fiados_movimientos_check CHECK (((cargo >= (0)::numeric) AND (abono >= (0)::numeric)))
);


--
-- Name: fiados_movimientos_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.fiados_movimientos_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: fiados_movimientos_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.fiados_movimientos_id_seq OWNED BY public.fiados_movimientos.id;


--
-- Name: fiados_new_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.fiados_new_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: fiados_new_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.fiados_new_id_seq OWNED BY public.fiados.id;


--
-- Name: gastos; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.gastos (
    id integer NOT NULL,
    fecha date NOT NULL,
    hora time without time zone,
    concepto character varying(300) NOT NULL,
    monto integer NOT NULL,
    categoria character varying(100),
    origen character varying(50) DEFAULT 'bot'::character varying,
    fac_id character varying(20),
    created_at timestamp without time zone DEFAULT now(),
    usuario_id integer
);


--
-- Name: gastos_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.gastos_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: gastos_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.gastos_id_seq OWNED BY public.gastos.id;


--
-- Name: historico_ventas; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.historico_ventas (
    fecha date NOT NULL,
    ventas integer DEFAULT 0,
    efectivo integer DEFAULT 0,
    transferencia integer DEFAULT 0,
    datafono integer DEFAULT 0,
    n_transacciones integer DEFAULT 0,
    gastos integer DEFAULT 0,
    abonos_proveedores integer DEFAULT 0,
    updated_at timestamp without time zone DEFAULT now(),
    origen character varying(20) DEFAULT 'calculado'::character varying NOT NULL,
    incluir_en_balances boolean DEFAULT true NOT NULL,
    notas text,
    CONSTRAINT chk_origen_valido CHECK (((origen)::text = ANY ((ARRAY['calculado'::character varying, 'manual_historico'::character varying])::text[])))
);


--
-- Name: inventario; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.inventario (
    id integer NOT NULL,
    producto_id integer,
    cantidad numeric(10,3) DEFAULT 0,
    minimo numeric(10,3) DEFAULT 0,
    unidad character varying(50) DEFAULT 'Unidad'::character varying,
    updated_at timestamp without time zone DEFAULT now(),
    nombre_original character varying(300),
    costo_promedio numeric(12,2),
    ultimo_costo numeric(12,2),
    ultimo_proveedor character varying(200),
    ultima_compra timestamp without time zone,
    ultima_venta timestamp without time zone,
    ultimo_ajuste timestamp without time zone,
    fecha_conteo timestamp without time zone
);


--
-- Name: inventario_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.inventario_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: inventario_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.inventario_id_seq OWNED BY public.inventario.id;


--
-- Name: iva_saldos_bimestrales; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.iva_saldos_bimestrales (
    "año" integer NOT NULL,
    bimestre integer NOT NULL,
    iva_ventas bigint DEFAULT 0,
    iva_compras bigint DEFAULT 0,
    saldo_anterior bigint DEFAULT 0,
    iva_neto bigint DEFAULT 0,
    estado character varying(20) DEFAULT 'borrador'::character varying,
    fecha_declaracion date,
    observaciones text,
    cerrado_at timestamp without time zone,
    CONSTRAINT iva_saldos_bimestrales_bimestre_check CHECK (((bimestre >= 1) AND (bimestre <= 6)))
);


--
-- Name: memoria_entidades; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.memoria_entidades (
    id integer NOT NULL,
    tipo text NOT NULL,
    entidad_key text NOT NULL,
    nota text NOT NULL,
    confidence real DEFAULT 1.0 NOT NULL,
    fecha_generada date NOT NULL,
    vigente boolean DEFAULT true NOT NULL,
    creado_en timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT memoria_entidades_tipo_check CHECK ((tipo = ANY (ARRAY['producto'::text, 'alias'::text, 'vendedor'::text])))
);


--
-- Name: memoria_entidades_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.memoria_entidades_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: memoria_entidades_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.memoria_entidades_id_seq OWNED BY public.memoria_entidades.id;


--
-- Name: productos; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.productos (
    id integer NOT NULL,
    clave character varying(200) NOT NULL,
    nombre character varying(300) NOT NULL,
    nombre_lower character varying(300) NOT NULL,
    codigo character varying(100),
    categoria character varying(200),
    precio_unidad integer DEFAULT 0 NOT NULL,
    unidad_medida character varying(50) DEFAULT 'Unidad'::character varying,
    activo boolean DEFAULT true,
    created_at timestamp without time zone DEFAULT now(),
    updated_at timestamp without time zone DEFAULT now(),
    tiene_iva boolean DEFAULT false,
    porcentaje_iva integer DEFAULT 0,
    aliases text[] DEFAULT '{}'::text[],
    precio_umbral integer,
    precio_bajo_umbral integer,
    precio_sobre_umbral integer
);


--
-- Name: productos_fracciones; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.productos_fracciones (
    id integer NOT NULL,
    producto_id integer,
    fraccion character varying(10) NOT NULL,
    precio_total integer NOT NULL,
    precio_unitario integer NOT NULL
);


--
-- Name: productos_fracciones_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.productos_fracciones_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: productos_fracciones_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.productos_fracciones_id_seq OWNED BY public.productos_fracciones.id;


--
-- Name: productos_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.productos_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: productos_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.productos_id_seq OWNED BY public.productos.id;


--
-- Name: usuarios; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.usuarios (
    id integer NOT NULL,
    telegram_id bigint NOT NULL,
    nombre character varying(100) NOT NULL,
    rol character varying(20) DEFAULT 'vendedor'::character varying NOT NULL,
    activo boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: usuarios_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.usuarios_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: usuarios_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.usuarios_id_seq OWNED BY public.usuarios.id;


--
-- Name: ventas; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ventas (
    id integer NOT NULL,
    consecutivo integer NOT NULL,
    fecha date NOT NULL,
    hora time without time zone,
    cliente_id integer,
    cliente_nombre character varying(300) DEFAULT 'Consumidor Final'::character varying,
    vendedor character varying(100),
    metodo_pago character varying(50),
    total integer DEFAULT 0 NOT NULL,
    created_at timestamp without time zone DEFAULT now(),
    usuario_id integer,
    factura_numero character varying(30),
    factura_cufe character varying(200),
    factura_estado character varying(20) DEFAULT 'sin_factura'::character varying,
    facturada_at timestamp without time zone
);


--
-- Name: ventas_detalle; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ventas_detalle (
    id integer NOT NULL,
    venta_id integer,
    producto_id integer,
    producto_nombre character varying(300) NOT NULL,
    cantidad numeric(10,3) NOT NULL,
    unidad_medida character varying(50) DEFAULT 'Unidad'::character varying,
    precio_unitario integer,
    total integer NOT NULL,
    alias_usado character varying(200),
    sin_detalle boolean DEFAULT false
);


--
-- Name: ventas_detalle_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.ventas_detalle_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: ventas_detalle_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.ventas_detalle_id_seq OWNED BY public.ventas_detalle.id;


--
-- Name: ventas_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.ventas_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: ventas_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.ventas_id_seq OWNED BY public.ventas.id;


--
-- Name: ventas_pendientes_voz; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ventas_pendientes_voz (
    session_id text NOT NULL,
    chat_id bigint NOT NULL,
    vendedor text,
    ventas jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: api_costo_diario id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.api_costo_diario ALTER COLUMN id SET DEFAULT nextval('public.api_costo_diario_id_seq'::regclass);


--
-- Name: audio_logs id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audio_logs ALTER COLUMN id SET DEFAULT nextval('public.audio_logs_id_seq'::regclass);


--
-- Name: bancolombia_transferencias id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bancolombia_transferencias ALTER COLUMN id SET DEFAULT nextval('public.bancolombia_transferencias_id_seq'::regclass);


--
-- Name: caja id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.caja ALTER COLUMN id SET DEFAULT nextval('public.caja_id_seq'::regclass);


--
-- Name: clientes id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.clientes ALTER COLUMN id SET DEFAULT nextval('public.clientes_id_seq'::regclass);


--
-- Name: compras id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.compras ALTER COLUMN id SET DEFAULT nextval('public.compras_id_seq'::regclass);


--
-- Name: compras_fiscal id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.compras_fiscal ALTER COLUMN id SET DEFAULT nextval('public.compras_fiscal_id_seq'::regclass);


--
-- Name: conversaciones_bot id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.conversaciones_bot ALTER COLUMN id SET DEFAULT nextval('public.conversaciones_bot_id_seq'::regclass);


--
-- Name: cuentas_cobro id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cuentas_cobro ALTER COLUMN id SET DEFAULT nextval('public.cuentas_cobro_id_seq'::regclass);


--
-- Name: documentos_soporte id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.documentos_soporte ALTER COLUMN id SET DEFAULT nextval('public.documentos_soporte_id_seq'::regclass);


--
-- Name: facturas_abonos id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.facturas_abonos ALTER COLUMN id SET DEFAULT nextval('public.facturas_abonos_id_seq'::regclass);


--
-- Name: facturas_electronicas id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.facturas_electronicas ALTER COLUMN id SET DEFAULT nextval('public.facturas_electronicas_id_seq'::regclass);


--
-- Name: fiados id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fiados ALTER COLUMN id SET DEFAULT nextval('public.fiados_new_id_seq'::regclass);


--
-- Name: fiados_movimientos id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fiados_movimientos ALTER COLUMN id SET DEFAULT nextval('public.fiados_movimientos_id_seq'::regclass);


--
-- Name: gastos id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.gastos ALTER COLUMN id SET DEFAULT nextval('public.gastos_id_seq'::regclass);


--
-- Name: inventario id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.inventario ALTER COLUMN id SET DEFAULT nextval('public.inventario_id_seq'::regclass);


--
-- Name: memoria_entidades id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.memoria_entidades ALTER COLUMN id SET DEFAULT nextval('public.memoria_entidades_id_seq'::regclass);


--
-- Name: productos id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.productos ALTER COLUMN id SET DEFAULT nextval('public.productos_id_seq'::regclass);


--
-- Name: productos_fracciones id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.productos_fracciones ALTER COLUMN id SET DEFAULT nextval('public.productos_fracciones_id_seq'::regclass);


--
-- Name: usuarios id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.usuarios ALTER COLUMN id SET DEFAULT nextval('public.usuarios_id_seq'::regclass);


--
-- Name: ventas id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ventas ALTER COLUMN id SET DEFAULT nextval('public.ventas_id_seq'::regclass);


--
-- Name: ventas_detalle id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ventas_detalle ALTER COLUMN id SET DEFAULT nextval('public.ventas_detalle_id_seq'::regclass);


--
-- Name: alembic_version alembic_version_pkc; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alembic_version
    ADD CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num);


--
-- Name: aliases aliases_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.aliases
    ADD CONSTRAINT aliases_pkey PRIMARY KEY (termino);


--
-- Name: api_costo_diario api_costo_diario_fecha_vendedor_id_modelo_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.api_costo_diario
    ADD CONSTRAINT api_costo_diario_fecha_vendedor_id_modelo_key UNIQUE (fecha, vendedor_id, modelo);


--
-- Name: api_costo_diario api_costo_diario_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.api_costo_diario
    ADD CONSTRAINT api_costo_diario_pkey PRIMARY KEY (id);


--
-- Name: audio_logs audio_logs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audio_logs
    ADD CONSTRAINT audio_logs_pkey PRIMARY KEY (id);


--
-- Name: bancolombia_transferencias bancolombia_transferencias_gmail_message_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bancolombia_transferencias
    ADD CONSTRAINT bancolombia_transferencias_gmail_message_id_key UNIQUE (gmail_message_id);


--
-- Name: bancolombia_transferencias bancolombia_transferencias_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bancolombia_transferencias
    ADD CONSTRAINT bancolombia_transferencias_pkey PRIMARY KEY (id);


--
-- Name: caja caja_fecha_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.caja
    ADD CONSTRAINT caja_fecha_key UNIQUE (fecha);


--
-- Name: caja caja_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.caja
    ADD CONSTRAINT caja_pkey PRIMARY KEY (id);


--
-- Name: clientes clientes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.clientes
    ADD CONSTRAINT clientes_pkey PRIMARY KEY (id);


--
-- Name: compras_fiscal compras_fiscal_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.compras_fiscal
    ADD CONSTRAINT compras_fiscal_pkey PRIMARY KEY (id);


--
-- Name: compras compras_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.compras
    ADD CONSTRAINT compras_pkey PRIMARY KEY (id);


--
-- Name: config config_sistema_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.config
    ADD CONSTRAINT config_sistema_pkey PRIMARY KEY (clave);


--
-- Name: conversaciones_bot conversaciones_bot_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.conversaciones_bot
    ADD CONSTRAINT conversaciones_bot_pkey PRIMARY KEY (id);


--
-- Name: cuentas_cobro cuentas_cobro_consecutivo_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cuentas_cobro
    ADD CONSTRAINT cuentas_cobro_consecutivo_key UNIQUE (consecutivo);


--
-- Name: cuentas_cobro cuentas_cobro_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cuentas_cobro
    ADD CONSTRAINT cuentas_cobro_pkey PRIMARY KEY (id);


--
-- Name: documentos_soporte documentos_soporte_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.documentos_soporte
    ADD CONSTRAINT documentos_soporte_pkey PRIMARY KEY (id);


--
-- Name: facturas_abonos facturas_abonos_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.facturas_abonos
    ADD CONSTRAINT facturas_abonos_pkey PRIMARY KEY (id);


--
-- Name: facturas_electronicas facturas_electronicas_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.facturas_electronicas
    ADD CONSTRAINT facturas_electronicas_pkey PRIMARY KEY (id);


--
-- Name: facturas_proveedores facturas_proveedores_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.facturas_proveedores
    ADD CONSTRAINT facturas_proveedores_pkey PRIMARY KEY (id);


--
-- Name: ferrebot_config ferrebot_config_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ferrebot_config
    ADD CONSTRAINT ferrebot_config_pkey PRIMARY KEY (clave);


--
-- Name: fiados_movimientos fiados_movimientos_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fiados_movimientos
    ADD CONSTRAINT fiados_movimientos_pkey PRIMARY KEY (id);


--
-- Name: fiados fiados_new_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fiados
    ADD CONSTRAINT fiados_new_pkey PRIMARY KEY (id);


--
-- Name: gastos gastos_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.gastos
    ADD CONSTRAINT gastos_pkey PRIMARY KEY (id);


--
-- Name: historico_ventas historico_ventas_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.historico_ventas
    ADD CONSTRAINT historico_ventas_pkey PRIMARY KEY (fecha);


--
-- Name: inventario inventario_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.inventario
    ADD CONSTRAINT inventario_pkey PRIMARY KEY (id);


--
-- Name: inventario inventario_producto_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.inventario
    ADD CONSTRAINT inventario_producto_id_key UNIQUE (producto_id);


--
-- Name: iva_saldos_bimestrales iva_saldos_bimestrales_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.iva_saldos_bimestrales
    ADD CONSTRAINT iva_saldos_bimestrales_pkey PRIMARY KEY ("año", bimestre);


--
-- Name: memoria_entidades memoria_entidades_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.memoria_entidades
    ADD CONSTRAINT memoria_entidades_pkey PRIMARY KEY (id);


--
-- Name: memoria_entidades memoria_entidades_unica; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.memoria_entidades
    ADD CONSTRAINT memoria_entidades_unica UNIQUE (tipo, entidad_key, fecha_generada);


--
-- Name: productos productos_clave_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.productos
    ADD CONSTRAINT productos_clave_key UNIQUE (clave);


--
-- Name: productos_fracciones productos_fracciones_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.productos_fracciones
    ADD CONSTRAINT productos_fracciones_pkey PRIMARY KEY (id);


--
-- Name: productos productos_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.productos
    ADD CONSTRAINT productos_pkey PRIMARY KEY (id);


--
-- Name: fiados uq_fiados_cliente; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fiados
    ADD CONSTRAINT uq_fiados_cliente UNIQUE (cliente_id);


--
-- Name: usuarios usuarios_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.usuarios
    ADD CONSTRAINT usuarios_pkey PRIMARY KEY (id);


--
-- Name: usuarios usuarios_telegram_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.usuarios
    ADD CONSTRAINT usuarios_telegram_id_key UNIQUE (telegram_id);


--
-- Name: ventas ventas_consecutivo_fecha_unique; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ventas
    ADD CONSTRAINT ventas_consecutivo_fecha_unique UNIQUE (consecutivo, fecha);


--
-- Name: ventas_detalle ventas_detalle_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ventas_detalle
    ADD CONSTRAINT ventas_detalle_pkey PRIMARY KEY (id);


--
-- Name: ventas_pendientes_voz ventas_pendientes_voz_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ventas_pendientes_voz
    ADD CONSTRAINT ventas_pendientes_voz_pkey PRIMARY KEY (session_id);


--
-- Name: ventas ventas_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ventas
    ADD CONSTRAINT ventas_pkey PRIMARY KEY (id);


--
-- Name: api_costo_diario_fecha_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX api_costo_diario_fecha_idx ON public.api_costo_diario USING btree (fecha DESC);


--
-- Name: api_costo_diario_vendedor_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX api_costo_diario_vendedor_idx ON public.api_costo_diario USING btree (vendedor_id, fecha DESC);


--
-- Name: audio_logs_canal_fecha_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX audio_logs_canal_fecha_idx ON public.audio_logs USING btree (canal, fecha DESC);


--
-- Name: audio_logs_fecha_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX audio_logs_fecha_idx ON public.audio_logs USING btree (fecha DESC);


--
-- Name: audio_logs_turn_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX audio_logs_turn_id_idx ON public.audio_logs USING btree (turn_id);


--
-- Name: conversaciones_bot_chat_creado_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX conversaciones_bot_chat_creado_idx ON public.conversaciones_bot USING btree (chat_id, creado DESC);


--
-- Name: conversaciones_bot_content_fts_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX conversaciones_bot_content_fts_idx ON public.conversaciones_bot USING gin (to_tsvector('spanish'::regconfig, content));


--
-- Name: conversaciones_bot_content_trgm_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX conversaciones_bot_content_trgm_idx ON public.conversaciones_bot USING gin (content public.gin_trgm_ops);


--
-- Name: conversaciones_bot_creado_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX conversaciones_bot_creado_idx ON public.conversaciones_bot USING btree (creado);


--
-- Name: conversaciones_bot_vendedor_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX conversaciones_bot_vendedor_idx ON public.conversaciones_bot USING btree (vendedor_id, creado DESC) WHERE (vendedor_id IS NOT NULL);


--
-- Name: idx_bancolombia_transferencias_fecha; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_bancolombia_transferencias_fecha ON public.bancolombia_transferencias USING btree (fecha DESC);


--
-- Name: idx_compras_factura_proveedor; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_compras_factura_proveedor ON public.compras USING btree (factura_proveedor_id) WHERE (factura_proveedor_id IS NOT NULL);


--
-- Name: idx_compras_fiscal_cufe; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_compras_fiscal_cufe ON public.compras_fiscal USING btree (cufe_proveedor) WHERE (cufe_proveedor IS NOT NULL);


--
-- Name: idx_compras_fiscal_evento_estado; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_compras_fiscal_evento_estado ON public.compras_fiscal USING btree (evento_estado);


--
-- Name: idx_compras_fiscal_fecha; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_compras_fiscal_fecha ON public.compras_fiscal USING btree (fecha);


--
-- Name: idx_compras_fiscal_gmail_msg; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX idx_compras_fiscal_gmail_msg ON public.compras_fiscal USING btree (gmail_message_id, producto_nombre) WHERE (gmail_message_id IS NOT NULL);


--
-- Name: idx_compras_fiscal_gmail_not_null; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_compras_fiscal_gmail_not_null ON public.compras_fiscal USING btree (created_at DESC) WHERE (gmail_message_id IS NOT NULL);


--
-- Name: idx_compras_fiscal_iva; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_compras_fiscal_iva ON public.compras_fiscal USING btree (incluye_iva, tarifa_iva) WHERE (incluye_iva = true);


--
-- Name: idx_compras_fiscal_origen; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_compras_fiscal_origen ON public.compras_fiscal USING btree (compra_origen_id) WHERE (compra_origen_id IS NOT NULL);


--
-- Name: idx_compras_sin_factura; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_compras_sin_factura ON public.compras USING btree (proveedor, fecha) WHERE (factura_proveedor_id IS NULL);


--
-- Name: idx_compras_usuario_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_compras_usuario_id ON public.compras USING btree (usuario_id);


--
-- Name: idx_facturas_cufe; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_facturas_cufe ON public.facturas_electronicas USING btree (cufe);


--
-- Name: idx_facturas_cufe_ref; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_facturas_cufe_ref ON public.facturas_electronicas USING btree (factura_cufe_ref);


--
-- Name: idx_facturas_proveedores_usuario_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_facturas_proveedores_usuario_id ON public.facturas_proveedores USING btree (usuario_id);


--
-- Name: idx_facturas_tipo; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_facturas_tipo ON public.facturas_electronicas USING btree (tipo);


--
-- Name: idx_facturas_venta; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_facturas_venta ON public.facturas_electronicas USING btree (venta_id);


--
-- Name: idx_fiados_cliente; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_fiados_cliente ON public.fiados USING btree (cliente_id);


--
-- Name: idx_fiados_mov_creado; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_fiados_mov_creado ON public.fiados_movimientos USING btree (creado_at);


--
-- Name: idx_fiados_mov_fiado_fecha; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_fiados_mov_fiado_fecha ON public.fiados_movimientos USING btree (fiado_id, fecha DESC, hora DESC, id DESC);


--
-- Name: idx_fiados_saldo; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_fiados_saldo ON public.fiados USING btree (saldo_actual) WHERE (saldo_actual > 0);


--
-- Name: idx_fiscal_sin_vincular; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_fiscal_sin_vincular ON public.compras_fiscal USING btree (proveedor, fecha) WHERE (compra_origen_id IS NULL);


--
-- Name: idx_gastos_fecha; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_gastos_fecha ON public.gastos USING btree (fecha);


--
-- Name: idx_gastos_usuario_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_gastos_usuario_id ON public.gastos USING btree (usuario_id);


--
-- Name: idx_historico_fecha_balances; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_historico_fecha_balances ON public.historico_ventas USING btree (fecha) WHERE (incluir_en_balances = true);


--
-- Name: idx_historico_origen; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_historico_origen ON public.historico_ventas USING btree (origen);


--
-- Name: idx_productos_aliases; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_productos_aliases ON public.productos USING gin (aliases);


--
-- Name: idx_productos_precio_escalonado; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_productos_precio_escalonado ON public.productos USING btree (precio_umbral) WHERE (precio_umbral IS NOT NULL);


--
-- Name: idx_ventas_consecutivo; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ventas_consecutivo ON public.ventas USING btree (consecutivo);


--
-- Name: idx_ventas_detalle_venta; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ventas_detalle_venta ON public.ventas_detalle USING btree (venta_id);


--
-- Name: idx_ventas_fecha; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ventas_fecha ON public.ventas USING btree (fecha);


--
-- Name: idx_ventas_usuario_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ventas_usuario_id ON public.ventas USING btree (usuario_id);


--
-- Name: ix_cuentas_cobro_fecha; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_cuentas_cobro_fecha ON public.cuentas_cobro USING btree (fecha DESC);


--
-- Name: ix_documentos_soporte_fecha; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_documentos_soporte_fecha ON public.documentos_soporte USING btree (fecha DESC);


--
-- Name: memoria_entidades_fecha_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX memoria_entidades_fecha_idx ON public.memoria_entidades USING btree (fecha_generada);


--
-- Name: memoria_entidades_lookup_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX memoria_entidades_lookup_idx ON public.memoria_entidades USING btree (tipo, entidad_key, vigente, fecha_generada DESC);


--
-- Name: uq_prod_fraccion; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_prod_fraccion ON public.productos_fracciones USING btree (producto_id, fraccion);


--
-- Name: ventas_detalle_producto_fts_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ventas_detalle_producto_fts_idx ON public.ventas_detalle USING gin (to_tsvector('spanish'::regconfig, (producto_nombre)::text));


--
-- Name: ventas_detalle_producto_trgm_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ventas_detalle_producto_trgm_idx ON public.ventas_detalle USING gin (producto_nombre public.gin_trgm_ops);


--
-- Name: ventas_pendientes_voz_created_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ventas_pendientes_voz_created_idx ON public.ventas_pendientes_voz USING btree (created_at);


--
-- Name: compras_fiscal trg_compras_fiscal_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_compras_fiscal_updated_at BEFORE UPDATE ON public.compras_fiscal FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();


--
-- Name: compras compras_compra_fiscal_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.compras
    ADD CONSTRAINT compras_compra_fiscal_id_fkey FOREIGN KEY (compra_fiscal_id) REFERENCES public.compras_fiscal(id) ON DELETE SET NULL;


--
-- Name: compras compras_factura_proveedor_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.compras
    ADD CONSTRAINT compras_factura_proveedor_id_fkey FOREIGN KEY (factura_proveedor_id) REFERENCES public.facturas_proveedores(id) ON DELETE SET NULL;


--
-- Name: compras_fiscal compras_fiscal_compra_origen_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.compras_fiscal
    ADD CONSTRAINT compras_fiscal_compra_origen_id_fkey FOREIGN KEY (compra_origen_id) REFERENCES public.compras(id) ON DELETE SET NULL;


--
-- Name: compras_fiscal compras_fiscal_producto_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.compras_fiscal
    ADD CONSTRAINT compras_fiscal_producto_id_fkey FOREIGN KEY (producto_id) REFERENCES public.productos(id);


--
-- Name: compras compras_producto_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.compras
    ADD CONSTRAINT compras_producto_id_fkey FOREIGN KEY (producto_id) REFERENCES public.productos(id);


--
-- Name: compras compras_usuario_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.compras
    ADD CONSTRAINT compras_usuario_id_fkey FOREIGN KEY (usuario_id) REFERENCES public.usuarios(id);


--
-- Name: documentos_soporte documentos_soporte_cuenta_cobro_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.documentos_soporte
    ADD CONSTRAINT documentos_soporte_cuenta_cobro_id_fkey FOREIGN KEY (cuenta_cobro_id) REFERENCES public.cuentas_cobro(id);


--
-- Name: facturas_abonos facturas_abonos_factura_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.facturas_abonos
    ADD CONSTRAINT facturas_abonos_factura_id_fkey FOREIGN KEY (factura_id) REFERENCES public.facturas_proveedores(id) ON DELETE CASCADE;


--
-- Name: facturas_electronicas facturas_electronicas_venta_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.facturas_electronicas
    ADD CONSTRAINT facturas_electronicas_venta_id_fkey FOREIGN KEY (venta_id) REFERENCES public.ventas(id) ON DELETE SET NULL;


--
-- Name: facturas_proveedores facturas_proveedores_usuario_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.facturas_proveedores
    ADD CONSTRAINT facturas_proveedores_usuario_id_fkey FOREIGN KEY (usuario_id) REFERENCES public.usuarios(id);


--
-- Name: fiados_movimientos fiados_movimientos_fiado_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fiados_movimientos
    ADD CONSTRAINT fiados_movimientos_fiado_id_fkey FOREIGN KEY (fiado_id) REFERENCES public.fiados(id) ON DELETE CASCADE;


--
-- Name: fiados fiados_new_cliente_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fiados
    ADD CONSTRAINT fiados_new_cliente_id_fkey FOREIGN KEY (cliente_id) REFERENCES public.clientes(id);


--
-- Name: compras_fiscal fk_compras_fiscal_usuario; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.compras_fiscal
    ADD CONSTRAINT fk_compras_fiscal_usuario FOREIGN KEY (usuario_id) REFERENCES public.usuarios(id) ON DELETE SET NULL;


--
-- Name: gastos gastos_usuario_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.gastos
    ADD CONSTRAINT gastos_usuario_id_fkey FOREIGN KEY (usuario_id) REFERENCES public.usuarios(id);


--
-- Name: inventario inventario_producto_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.inventario
    ADD CONSTRAINT inventario_producto_id_fkey FOREIGN KEY (producto_id) REFERENCES public.productos(id) ON DELETE CASCADE;


--
-- Name: productos_fracciones productos_fracciones_producto_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.productos_fracciones
    ADD CONSTRAINT productos_fracciones_producto_id_fkey FOREIGN KEY (producto_id) REFERENCES public.productos(id) ON DELETE CASCADE;


--
-- Name: ventas ventas_cliente_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ventas
    ADD CONSTRAINT ventas_cliente_id_fkey FOREIGN KEY (cliente_id) REFERENCES public.clientes(id);


--
-- Name: ventas_detalle ventas_detalle_producto_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ventas_detalle
    ADD CONSTRAINT ventas_detalle_producto_id_fkey FOREIGN KEY (producto_id) REFERENCES public.productos(id);


--
-- Name: ventas_detalle ventas_detalle_venta_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ventas_detalle
    ADD CONSTRAINT ventas_detalle_venta_id_fkey FOREIGN KEY (venta_id) REFERENCES public.ventas(id) ON DELETE CASCADE;


--
-- Name: ventas ventas_usuario_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ventas
    ADD CONSTRAINT ventas_usuario_id_fkey FOREIGN KEY (usuario_id) REFERENCES public.usuarios(id);


--
-- PostgreSQL database dump complete
--

\unrestrict eScIh9Z161oxhX8XZwU6YLXlz6rnfsiCqBtkTnpZfhmE1ubfpiBVSsOQJeUBuWb

