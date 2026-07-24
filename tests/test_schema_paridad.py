"""Guardarraíl de paridad del esquema tenant: red de seguridad antes del ETL de Punto Rojo.

Falla si el esquema migrado a head se desvía del set esperado de tablas. La lista
ESPERADA está hardcodeada a propósito: agregar o quitar una tabla sin actualizar este test rompe la
prueba, forzando la revisión. Tras la 0005, `config_empresa` ya NO existe en la app DB.
"""
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import AsyncEngine

# 94 tablas de negocio del esquema tenant en head (sin 'alembic_version').
# Incluye el pack Agenda/Citas (0008): agenda_config, bloqueos, citas, disponibilidad,
# recurso_servicio, recursos, servicios; el handoff transversal (0009): conversaciones, y su hilo
# de mensajes (0024): conversacion_mensajes; el pack FAQ (0012_faq_conocimiento): conocimiento; y el
# pack cobranza (0017, ADR 0015): cobranza_config, cobranza_clientes, promesas_pago, pagos_reportados.
_TABLAS_ESPERADAS: frozenset[str] = frozenset({
    "aliases", "api_costo_diario", "audio_logs", "bancolombia_transferencias", "caja",
    "caja_movimientos", "clientes", "compras", "compras_detalle", "compras_fiscal",
    "conversaciones_bot", "cuentas_cobro", "documentos_soporte", "eventos_dian", "facturas_abonos",
    "facturas_electronicas", "facturas_proveedores", "fiados", "fiados_movimientos", "gastos",
    "historico_ventas", "inventario", "iva_saldos_bimestrales", "libro_iva", "memoria_entidades",
    "movimientos_inventario", "notas_electronicas", "productos", "productos_fracciones",
    "proveedores", "usuarios", "ventas", "ventas_detalle", "ventas_pendientes_voz",
    "agenda_config", "bloqueos", "citas", "disponibilidad", "recurso_servicio", "recursos",
    "servicios", "conversaciones",
    "conversacion_mensajes",   # hilo del inbox / handoff (0024, Fase 2)
    "conocimiento",   # pack FAQ (0012_faq_conocimiento)
    "webhooks_matias_recibidos",   # idempotencia del webhook MATIAS (0014, D7.1)
    "cobranza_config", "cobranza_clientes", "promesas_pago", "pagos_reportados",   # pack cobranza (0017)
    "cobranza_recordatorios",   # log durable → métrica "pesos recuperados" (0018)
    "pedido_config", "zonas_domicilio", "pedidos", "pedido_items",   # pack pedidos (0019, ADR 0016)
    "modificador_grupos", "modificador_opciones",   # modificadores de menú (0060, ADR 0032 F2)
    "mesas",   # salón/orden abierta por mesa (0061, ADR 0032 F3)
    "comanda_zonas", "comandas", "comanda_items",   # KDS (0062, ADR 0032 F4)
    "recetas",   # BOM del plato (0063, ADR 0032 F6)
    "trabajos_impresion",   # cola de impresión térmica (0064, ADR 0033 R1)
    "ventas_wa_config", "cotizaciones", "cotizacion_items",   # pack ventas/cotizaciones (0020, ADR 0017)
    "cobros",   # frente de pagos (0021, ADR 0013)
    "comprobantes_pago",   # comprobante de pago por foto → desempate conciliación (0057, demo Sirius)
    "postventa_config", "postventa_envios", "encuestas_respuestas",   # pack postventa (0023)
    "pagar_config", "pagar_avisos",   # pack pagar (0026, ADR 0019): config + dedup de avisos al dueño
    "devoluciones", "devoluciones_detalle",   # notas crédito/débito + devoluciones (0031, ADR 0026)
    "config_retenciones",       # catálogo tributario editable (0032, ADR 0027)
    "retenciones_documento",    # renglones de retención/INC por documento (0033, ADR 0027)
    # motor contable: ledger de doble partida + PUC (0037-0041, ADR 0030)
    "puc_cuentas", "periodo_contable", "journal_entry", "journal_line", "saldo_cache",
    # --- vertical construcción PIM (0043-0049) ---
    # base + obras + operación (0043-0046)
    "obras", "cotizaciones_obra", "items_cotizacion_obra", "consumos_inventario",
    "reportes_diarios_obra", "asignaciones_trabajador_obra", "asignaciones_maquina_obra",
    "trabajadores", "maquinas", "herramientas", "registros_asistencia",
    "registros_horas_maquina", "mantenimientos",
    # nómina + liquidación (0047-0048)
    "periodos_nomina", "detalles_liquidacion", "prorrateo_nomina_obra", "liquidaciones_obra",
    "parametros_legales",
    # cartera de alquiler (0049)
    "cartera_config", "cupos_alquiler", "cargos_alquiler",
    # pedidos a proveedor con lead time (0052, reforma dashboard POS F2)
    "pedidos_proveedor", "pedidos_proveedor_detalle",
    # partes del cobro de una venta mixta (0053, reforma dashboard POS F5)
    "ventas_pagos",
    # turnos de rotación de operadores dentro de un parte de horas (0054)
    "turnos_horas_maquina",
    # operación de máquina en vivo: sesión con cronómetro + tramos de operador (0055)
    "sesiones_maquina", "tramos_operador",
})


async def _tablas_reales(engine: AsyncEngine) -> frozenset[str]:
    """Tablas de la app DB migrada (sin la de control de Alembic)."""
    async with engine.connect() as conn:
        nombres = await conn.run_sync(lambda c: inspect(c).get_table_names())
    return frozenset(nombres) - {"alembic_version"}


async def test_esquema_tenant_paridad(tenant):
    reales = await _tablas_reales(tenant.engine)
    diff = reales ^ _TABLAS_ESPERADAS
    assert reales == _TABLAS_ESPERADAS, (
        f"esquema desviado — faltantes={_TABLAS_ESPERADAS - reales}, "
        f"sobrantes={reales - _TABLAS_ESPERADAS} (diferencia simétrica={diff})"
    )
