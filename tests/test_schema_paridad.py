"""Guardarraíl de paridad del esquema tenant: red de seguridad antes del ETL de Punto Rojo.

Falla si el esquema migrado a head se desvía del set esperado de tablas. La lista
ESPERADA está hardcodeada a propósito: agregar o quitar una tabla sin actualizar este test rompe la
prueba, forzando la revisión. Tras la 0005, `config_empresa` ya NO existe en la app DB.
"""
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import AsyncEngine

# 49 tablas de negocio del esquema tenant en head (sin 'alembic_version').
# Incluye el pack Agenda/Citas (0008): agenda_config, bloqueos, citas, disponibilidad,
# recurso_servicio, recursos, servicios; el handoff transversal (0009): conversaciones;
# el pack FAQ (0012_faq_conocimiento): conocimiento; y el pack cobranza (0017, ADR 0015):
# cobranza_config, cobranza_clientes, promesas_pago, pagos_reportados.
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
    "conocimiento",   # pack FAQ (0012_faq_conocimiento)
    "webhooks_matias_recibidos",   # idempotencia del webhook MATIAS (0014, D7.1)
    "cobranza_config", "cobranza_clientes", "promesas_pago", "pagos_reportados",   # pack cobranza (0017)
    "cobranza_recordatorios",   # log durable → métrica "pesos recuperados" (0018)
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
