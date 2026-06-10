"""E3 RED — repositorio de facturación contra una base efímera real (Postgres).

Verifica consecutivo por SEQUENCE, creación de pendiente + idempotencia (UNIQUE), transiciones de
estado y la lectura de la venta a facturar. En RED todos fallan por NotImplementedError (no por
conexión: la base efímera del fixture sí levanta).
"""
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from modules.facturacion.repository import SqlFacturacionRepository


async def _crear_pendiente(repo, *, key, venta_id=None):
    consecutivo = await repo.siguiente_consecutivo()
    return await repo.crear_pendiente(
        venta_id=venta_id, tipo="factura", prefijo="FPR",
        consecutivo=consecutivo, idempotency_key=key,
    )


async def test_siguiente_consecutivo_secuencia(tenant):
    async with AsyncSession(tenant.engine) as s:
        repo = SqlFacturacionRepository(s)
        a = await repo.siguiente_consecutivo()
        b = await repo.siguiente_consecutivo()
    assert b == a + 1                                  # de fe_factura_consecutivo_seq


async def test_crear_pendiente_y_obtener(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        repo = SqlFacturacionRepository(s)
        f = await _crear_pendiente(repo, key="k-1")
        await s.commit()
        got = await repo.obtener(f.id)
    assert got is not None and got.estado == "pendiente"
    assert got.idempotency_key == "k-1" and got.consecutivo == f.consecutivo


async def test_idempotency_unique(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        repo = SqlFacturacionRepository(s)
        await _crear_pendiente(repo, key="dup")
        await s.commit()
        with pytest.raises(IntegrityError):            # la UNIQUE protege la misma key
            await _crear_pendiente(repo, key="dup")
            await s.flush()
        await s.rollback()


async def test_documento_para_xml_y_guardar_xml(tenant):
    """D7.3: estado/cufe/tiene_xml + archivado del XML técnico contra base efímera real."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        repo = SqlFacturacionRepository(s)
        f = await _crear_pendiente(repo, key="k-xml")
        await s.commit()
        await repo.marcar_aceptada(f.id, cufe="c" * 40, dian_respuesta={"urlinvoicexml": "http://m/x"})
        await s.commit()
        antes = await repo.documento_para_xml(f.id)
        assert antes is not None
        assert antes.estado == "aceptada" and antes.cufe == "c" * 40 and antes.tiene_xml is False
        assert antes.dian_respuesta == {"urlinvoicexml": "http://m/x"}
        await repo.guardar_xml(f.id, xml="<Invoice>ok</Invoice>", xml_url="http://m/x", pdf_url="http://m/p")
        await s.commit()
        despues = await repo.documento_para_xml(f.id)
        assert despues.tiene_xml is True
    async with AsyncSession(tenant.engine) as s:
        xml, xml_url, pdf_url = (
            await s.execute(
                text("SELECT xml_contenido, xml_url, pdf_url FROM facturas_electronicas WHERE id=:i"),
                {"i": f.id},
            )
        ).one()
    assert xml == "<Invoice>ok</Invoice>" and xml_url == "http://m/x" and pdf_url == "http://m/p"


async def test_registrar_recibido_idempotente(tenant):
    """D7.1: el `webhook_id` UNIQUE da la idempotencia; el segundo registro devuelve None."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        repo = SqlFacturacionRepository(s)
        rid = await repo.registrar_recibido("wh-1", "document.accepted", {"document_key": "a" * 40})
        await s.commit()
        assert rid is not None
        dup = await repo.registrar_recibido("wh-1", "document.accepted", {"document_key": "a" * 40})
        await s.commit()
        assert dup is None                                  # idempotente: MATIAS reintenta
        rec = await repo.leer_recibido(rid)
        assert rec is not None and rec.evento == "document.accepted"
        assert rec.payload == {"document_key": "a" * 40}
        await repo.marcar_recibido_procesado(rid)
        await s.commit()
    async with AsyncSession(tenant.engine) as s:
        procesado = (
            await s.execute(
                text("SELECT procesado_en FROM webhooks_matias_recibidos WHERE id=:i"), {"i": rid}
            )
        ).scalar_one()
    assert procesado is not None


async def test_buscar_por_cufe_y_numero(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        repo = SqlFacturacionRepository(s)
        f = await _crear_pendiente(repo, key="k-cufe")
        await s.commit()
        await repo.marcar_aceptada(f.id, cufe="d" * 40, dian_respuesta={})
        await s.commit()
        por_cufe = await repo.buscar_por_cufe("d" * 40)
        por_numero = await repo.buscar_por_numero("FPR", f.consecutivo)
        falta = await repo.buscar_por_cufe("z" * 40)
    assert por_cufe is not None and por_cufe.id == f.id
    assert por_numero is not None and por_numero.id == f.id
    assert falta is None


async def test_marcar_aceptada(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        repo = SqlFacturacionRepository(s)
        f = await _crear_pendiente(repo, key="k-acc")
        await s.commit()
        out = await repo.marcar_aceptada(f.id, cufe="a" * 40, dian_respuesta={"cufe": "a" * 40})
        await s.commit()
    assert out.estado == "aceptada" and out.cufe == "a" * 40
    async with AsyncSession(tenant.engine) as s:
        estado, cufe, emitido = (
            await s.execute(
                text("SELECT estado, cufe, emitido_en FROM facturas_electronicas WHERE id=:i"),
                {"i": f.id},
            )
        ).one()
    assert estado == "aceptada" and cufe == "a" * 40 and emitido is not None


async def test_marcar_rechazada(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        repo = SqlFacturacionRepository(s)
        f = await _crear_pendiente(repo, key="k-rech")
        await s.commit()
        out = await repo.marcar_rechazada(
            f.id, error_msg="Rechazado DIAN", dian_respuesta={"rechazo": "Rechazado DIAN"}
        )
        await s.commit()
    assert out.estado == "rechazada" and out.intentos == 0      # terminal de negocio: no incrementa
    async with AsyncSession(tenant.engine) as s:
        estado, resp, emitido = (
            await s.execute(
                text("SELECT estado, dian_respuesta, emitido_en FROM facturas_electronicas WHERE id=:i"),
                {"i": f.id},
            )
        ).one()
    assert estado == "rechazada" and resp == {"rechazo": "Rechazado DIAN"} and emitido is not None


async def test_marcar_error(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        repo = SqlFacturacionRepository(s)
        f = await _crear_pendiente(repo, key="k-err")
        await s.commit()
        out = await repo.marcar_error(f.id, error_msg="Rechazado por DIAN")
        await s.commit()
    assert out.estado == "error" and out.intentos == 1
    async with AsyncSession(tenant.engine) as s:
        estado, resp = (
            await s.execute(
                text("SELECT estado, dian_respuesta FROM facturas_electronicas WHERE id=:i"),
                {"i": f.id},
            )
        ).one()
    assert estado == "error" and resp == {"error": "Rechazado por DIAN"}


async def test_datos_para_factura(tenant, seed_producto):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid, pid = await seed_producto(s, precio="11900", iva=19)
        cli = (await s.execute(text(
            "INSERT INTO clientes (nombre, tipo_documento, documento, correo, ciudad_dane, regimen, saldo_fiado) "
            "VALUES ('Ferre SAS','NIT','900123456','f@e.co','5001','responsable_iva',0) RETURNING id"
        ))).scalar_one()
        cons = (await s.execute(text("SELECT nextval('ventas_consecutivo_seq')"))).scalar_one()
        vid = (await s.execute(text(
            "INSERT INTO ventas (consecutivo, cliente_id, vendedor_id, fecha, subtotal, impuestos, total, metodo_pago) "
            "VALUES (:c,:cli,:u, now(), 10000, 1900, 11900, 'efectivo') RETURNING id"
        ), {"c": cons, "cli": cli, "u": uid})).scalar_one()
        await s.execute(text(
            "INSERT INTO ventas_detalle (venta_id, producto_id, descripcion, cantidad, precio_unitario, iva) "
            "VALUES (:v,:p,'martillo',1,11900,19)"
        ), {"v": vid, "p": pid})
        await s.commit()
        datos = await SqlFacturacionRepository(s).datos_para_factura(vid)
    assert datos is not None
    assert datos.cliente.tipo_id == "NIT" and datos.cliente.identificacion == "900123456"
    assert datos.cliente.municipio_dian == "5001"
    assert len(datos.items) == 1
    assert datos.items[0].precio_unitario_con_iva == Decimal("11900.00")
    assert datos.items[0].pct_iva == Decimal("19")
