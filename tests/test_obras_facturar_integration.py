"""Facturar desde obra (Fase 7 DIAN) contra Postgres efímero — reuso del pipeline venta→FE.

Prueba el flujo real end-to-end SIN tocar MATIAS ni la DIAN: `ObrasService.facturar_obra` arma la venta
interna desde la cotización GANADA (AIU, IVA SOLO sobre la utilidad), crea el documento FE `pendiente`
reusando `FacturacionService.crear_pendiente_fe` (máquina de estados + consecutivo intactos) y ESTAMPA el
rastro `obra_id` en la fila (migración 0050). La EMISIÓN (que arma el CUFE) se ejerce con un MatiasClient
FAKE (regla de oro fiscal: nunca red real), para verificar que el CUFE aterriza y el `obra_id` sobrevive.

Invariantes cubiertos:
- Rastro obra→documento: la factura queda ligada a la obra (`facturas_electronicas.obra_id`).
- IDEMPOTENCIA DURA (test-primero): facturar dos veces NO crea un segundo documento ni una segunda venta;
  emitir dos veces NO genera un segundo CUFE ni re-llama a MATIAS.
- AIU: el único IVA del documento recae sobre la utilidad (la venta interna lo materializa por línea).
- Aislamiento multi-tenant: la factura de la empresa A no existe en la base de la empresa B.
"""
from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from modules.facturacion.matias_client import EmisionResultado
from modules.facturacion.repository import SqlFacturacionRepository
from modules.facturacion.service import ConfigFiscal, FacturacionService
from modules.obra.errors import ObraSinCotizacion
from modules.obra.repository import SqlObrasRepository
from modules.obra.service import ObrasService
from modules.ventas.repository import SqlVentasRepository

# Config fiscal SANDBOX: sólo `prefix` se usa al reservar el consecutivo; el resto lo consume la emisión
# (con MATIAS fake). Ambiente 'pruebas' por defecto — nunca declara producción.
_CONFIG = ConfigFiscal(
    resolution_number="18760000001", prefix="FEV", notes="PIM S.A.S", city_id_default="149"
)
_CUFE = "c" * 96  # CUFE canned del MATIAS fake (nunca se pega a la DIAN)


class _FakeMatias:
    """MatiasClient FAKE (duck-typed): city_id canned + emitir_factura canned; registra si se llamó.

    Espeja el fake de test_facturacion_service. NUNCA toca red: la transmisión real es GO-LIVE GATED."""

    def __init__(self, *, cufe: str = _CUFE, categoria: str = "aceptada") -> None:
        self._cufe = cufe
        self._categoria = categoria
        self.emitir_llamado = False

    async def city_id(self, dane_code):
        return "149"

    async def emitir_factura(self, payload):
        self.emitir_llamado = True
        return EmisionResultado(ok=True, cufe=self._cufe, categoria=self._categoria)


def _facturador(s: AsyncSession) -> tuple[ObrasService, SqlFacturacionRepository]:
    """Arma el `ObrasService` con los colaboradores FE sobre la MISMA sesión del tenant (como el router)."""
    fac_repo = SqlFacturacionRepository(s)
    service = ObrasService(
        SqlObrasRepository(s),
        ventas=SqlVentasRepository(s),
        facturacion=FacturacionService(fac_repo, config=_CONFIG),
        estampador=fac_repo,
    )
    return service, fac_repo


async def _usuario(s: AsyncSession) -> int:
    return (
        await s.execute(
            text("INSERT INTO usuarios (nombre, rol) VALUES ('Admin','admin') RETURNING id")
        )
    ).scalar_one()


async def _cliente(s: AsyncSession) -> int:
    return (
        await s.execute(
            text("INSERT INTO clientes (nombre, saldo_fiado) VALUES ('Alcaldía', 0) RETURNING id")
        )
    ).scalar_one()


async def _obra_con_cotizacion(
    s: AsyncSession,
    cid: int,
    *,
    items: list[tuple[str, str]],
    a="0.05",
    i="0.03",
    u="0.04",
    estado_cotizacion="GANADA",
) -> int:
    """Crea cotización (con ítems) + la obra 1-1 ligada; devuelve el obra_id. Espeja test_obras_gasto_real."""
    numero = f"PIM-{uuid.uuid4().hex[:8]}-2026"
    cot_id = (
        await s.execute(
            text(
                "INSERT INTO cotizaciones_obra "
                "(numero, cliente_id, nombre_obra, administracion_pct, imprevistos_pct, utilidad_pct, "
                " iva_sobre_utilidad_pct, estado) "
                "VALUES (:num,:c,'Vía',:a,:i,:u,0.19,:est) RETURNING id"
            ),
            {"num": numero, "c": cid, "a": a, "i": i, "u": u, "est": estado_cotizacion},
        )
    ).scalar_one()
    for orden, (cant, vu) in enumerate(items, start=1):
        await s.execute(
            text(
                "INSERT INTO items_cotizacion_obra "
                "(cotizacion_id, orden, descripcion, unidad, cantidad, valor_unitario) "
                "VALUES (:c,:o,'renglón','m3',:cant,:vu)"
            ),
            {"c": cot_id, "o": orden, "cant": cant, "vu": vu},
        )
    return (
        await s.execute(
            text(
                "INSERT INTO obras (cotizacion_id, cliente_id, nombre, estado) "
                "VALUES (:cot,:c,'Obra','EN_EJECUCION') RETURNING id"
            ),
            {"cot": cot_id, "c": cid},
        )
    ).scalar_one()


async def _obra_id_de_factura(s: AsyncSession, factura_id: int) -> int | None:
    return (
        await s.execute(
            text("SELECT obra_id FROM facturas_electronicas WHERE id=:i"), {"i": factura_id}
        )
    ).scalar_one()


async def test_facturar_obra_liga_documento_a_la_obra_y_arma_venta_aiu(tenant):
    """Cotización GANADA (subtotal 10M, A5% I3% U4%) → FE `pendiente` ligada a la obra + venta AIU exacta.

    El IVA del documento recae SOLO sobre la utilidad: venta.impuestos == U×0.19 (nunca sobre el subtotal)."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid = await _usuario(s)
        cid = await _cliente(s)
        oid = await _obra_con_cotizacion(s, cid, items=[("1000", "10000")])  # subtotal = 10.000.000
        await s.commit()

        service, fac_repo = _facturador(s)
        res = await service.facturar_obra(oid, vendedor_id=uid)
        await s.commit()

        # Documento NUEVO, aún sin CUFE (lo arma el worker al emitir): reuso de la máquina de estados.
        assert res.creada is True
        assert res.factura.tipo == "factura"
        assert res.factura.estado == "pendiente"
        assert res.factura.cufe is None
        assert res.factura.consecutivo is not None      # consecutivo FE reservado (config.prefix=FEV)
        assert res.factura.prefijo == "FEV"

        # Rastro obra→documento estampado (migración 0050).
        assert await _obra_id_de_factura(s, res.factura.id) == oid
        # Y la vista de idempotencia lo encuentra por obra_id.
        ligada = await fac_repo_factura_de_obra(s, oid)
        assert ligada is not None and ligada.id == res.factura.id

        # Venta interna AIU: subtotal+A+I+U = 11.200.000; IVA sólo sobre la utilidad (400k×0.19 = 76.000);
        # total = 11.276.000. Prueba la regla de negocio crítica del cliente (IVA sobre la utilidad).
        venta = (
            await s.execute(
                text("SELECT subtotal, impuestos, total FROM ventas WHERE id=:v"),
                {"v": res.factura.venta_id},
            )
        ).one()
        assert Decimal(venta.subtotal) == Decimal("11200000.00")
        assert Decimal(venta.impuestos) == Decimal("76000.00")
        assert Decimal(venta.total) == Decimal("11276000.00")

        # Líneas: 1 ítem + Administración + Imprevistos + Utilidad = 4, todas sin producto (imputación
        # fiscal, no mercancía) → NO se asienta ningún movimiento de inventario.
        n_lineas = (
            await s.execute(
                text("SELECT count(*), count(producto_id) FROM ventas_detalle WHERE venta_id=:v"),
                {"v": res.factura.venta_id},
            )
        ).one()
        assert n_lineas[0] == 4 and n_lineas[1] == 0
        movs = (
            await s.execute(
                text("SELECT count(*) FROM movimientos_inventario WHERE referencia=:r"),
                {"r": f"venta:{res.factura.venta_id}"},
            )
        ).scalar_one()
        assert movs == 0


async def test_facturar_obra_es_idempotente_no_duplica_documento_ni_venta(tenant):
    """INVARIANTE (test-primero): facturar una obra YA facturada devuelve el mismo documento (creada=False),
    sin armar una segunda venta ni un segundo CUFE. Una sola fila FE y una sola venta para la obra."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid = await _usuario(s)
        cid = await _cliente(s)
        oid = await _obra_con_cotizacion(s, cid, items=[("100", "5000")])
        await s.commit()

        service, _ = _facturador(s)
        primero = await service.facturar_obra(oid, vendedor_id=uid)
        await s.commit()

    # Segundo intento en OTRA sesión (como un segundo request): corta en `factura_de_obra` sin crear nada.
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        service, _ = _facturador(s)
        segundo = await service.facturar_obra(oid, vendedor_id=uid)
        await s.commit()

        assert segundo.creada is False
        assert segundo.factura.id == primero.factura.id
        # Exactamente un documento y una venta para la obra (no se duplicó nada).
        n_docs = (
            await s.execute(
                text("SELECT count(*) FROM facturas_electronicas WHERE obra_id=:o"), {"o": oid}
            )
        ).scalar_one()
        n_ventas = (
            await s.execute(
                text("SELECT count(*) FROM ventas WHERE idempotency_key=:k"),
                {"k": f"obra-fe:{oid}"},
            )
        ).scalar_one()
        assert n_docs == 1 and n_ventas == 1


async def test_emitir_estampa_cufe_y_conserva_el_rastro_obra(tenant):
    """Tras `facturar_obra`, la EMISIÓN (MATIAS fake) lleva la FE a `aceptada` con su CUFE, y el `obra_id`
    SOBREVIVE la emisión. Emitir de nuevo NO re-llama a MATIAS ni cambia el CUFE (idempotencia dura)."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid = await _usuario(s)
        cid = await _cliente(s)
        oid = await _obra_con_cotizacion(s, cid, items=[("10", "100000")])
        await s.commit()

        service, fac_repo = _facturador(s)
        res = await service.facturar_obra(oid, vendedor_id=uid)
        await s.commit()
        factura_id = res.factura.id

        # Emisión con MATIAS FAKE (nunca red): arma el CUFE y persiste `aceptada`.
        matias = _FakeMatias()
        emisor = FacturacionService(fac_repo, matias=matias, config=_CONFIG)
        decision = await emisor.emitir(factura_id)
        await s.commit()
        assert decision.estado == "aceptada"
        assert matias.emitir_llamado is True

        fila = (
            await s.execute(
                text("SELECT estado, cufe, obra_id FROM facturas_electronicas WHERE id=:i"),
                {"i": factura_id},
            )
        ).one()
        assert fila.estado == "aceptada"
        assert fila.cufe == _CUFE
        assert fila.obra_id == oid            # el rastro obra→documento sobrevive la emisión

        # Re-emitir es idempotente: la FE ya `aceptada` no vuelve a MATIAS (no hay segundo CUFE).
        matias2 = _FakeMatias(cufe="d" * 96)
        emisor2 = FacturacionService(fac_repo, matias=matias2, config=_CONFIG)
        decision2 = await emisor2.emitir(factura_id)
        await s.commit()
        assert decision2.estado == "aceptada"
        assert matias2.emitir_llamado is False
        cufe_final = (
            await s.execute(
                text("SELECT cufe FROM facturas_electronicas WHERE id=:i"), {"i": factura_id}
            )
        ).scalar_one()
        assert cufe_final == _CUFE            # el CUFE original no fue reemplazado


async def test_obra_suelta_sin_cotizacion_no_es_facturable(tenant):
    """Obra sin cotización (suelta) → `ObraSinCotizacion` (no hay ítems legítimos que facturar)."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid = await _usuario(s)
        cid = await _cliente(s)
        oid = (
            await s.execute(
                text("INSERT INTO obras (cliente_id, nombre) VALUES (:c,'Suelta') RETURNING id"),
                {"c": cid},
            )
        ).scalar_one()
        await s.commit()

        service, _ = _facturador(s)
        with pytest.raises(ObraSinCotizacion):
            await service.facturar_obra(oid, vendedor_id=uid)


async def test_cotizacion_no_ganada_no_es_facturable(tenant):
    """Cotización en BORRADOR (no GANADA) → `ObraSinCotizacion`: sólo se factura contra una cotización ganada."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid = await _usuario(s)
        cid = await _cliente(s)
        oid = await _obra_con_cotizacion(
            s, cid, items=[("100", "5000")], estado_cotizacion="BORRADOR"
        )
        await s.commit()

        service, _ = _facturador(s)
        with pytest.raises(ObraSinCotizacion):
            await service.facturar_obra(oid, vendedor_id=uid)


async def test_facturar_obra_aislado_entre_empresas(tenant_factory):
    """La factura de obra vive SÓLO en la base de su empresa: B no ve el documento emitido en A."""
    empresa_a = await tenant_factory()
    empresa_b = await tenant_factory()

    async with AsyncSession(empresa_a.engine, expire_on_commit=False) as s:
        uid = await _usuario(s)
        cid = await _cliente(s)
        oid_a = await _obra_con_cotizacion(s, cid, items=[("100", "5000")])
        await s.commit()
        service, _ = _facturador(s)
        await service.facturar_obra(oid_a, vendedor_id=uid)
        await s.commit()

    async with AsyncSession(empresa_a.engine) as s:
        n_a = (await s.execute(text("SELECT count(*) FROM facturas_electronicas"))).scalar_one()
    async with AsyncSession(empresa_b.engine) as s:
        n_b = (await s.execute(text("SELECT count(*) FROM facturas_electronicas"))).scalar_one()
    assert n_a == 1   # A tiene su documento
    assert n_b == 0   # B no ve nada de A (la base ES la frontera)


async def fac_repo_factura_de_obra(s: AsyncSession, obra_id: int):
    """Atajo de lectura: la factura ligada a la obra vía el repo de obras (`factura_de_obra`)."""
    return await SqlObrasRepository(s).factura_de_obra(obra_id)
