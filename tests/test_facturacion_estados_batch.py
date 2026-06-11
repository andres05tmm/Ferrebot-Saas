"""F2.3c — `estados_por_ventas`: lectura BATCH del estado fiscal por venta (badge del dashboard).

Contra base efímera con el repo real: el batch resuelve varias ventas en UNA sola query (sin N+1),
mapea {tipo, estado, cufe, numero=consecutivo, prefijo}, omite ventas sin documento y, ante un
histórico con varios documentos para una venta, elige el representativo (no-anulado, más reciente).
"""
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession

from modules.facturacion.repository import EstadoFiscalVenta, SqlFacturacionRepository


async def _venta(s: AsyncSession) -> int:
    uid = (
        await s.execute(text("INSERT INTO usuarios (nombre, rol) VALUES ('V','vendedor') RETURNING id"))
    ).scalar_one()
    cons = (await s.execute(text("SELECT nextval('ventas_consecutivo_seq')"))).scalar_one()
    return (
        await s.execute(
            text("INSERT INTO ventas (consecutivo, vendedor_id, fecha, subtotal, impuestos, total, metodo_pago) "
                 "VALUES (:c,:u, now(), 10000, 1900, 11900, 'efectivo') RETURNING id"),
            {"c": cons, "u": uid},
        )
    ).scalar_one()


async def _doc(
    s: AsyncSession, venta_id: int, *, tipo: str, estado: str, key: str,
    cufe: str | None = None, consecutivo: int | None = None, prefijo: str | None = None,
) -> int:
    return (
        await s.execute(
            text("INSERT INTO facturas_electronicas (venta_id, tipo, estado, cufe, consecutivo, prefijo, idempotency_key) "
                 "VALUES (:v,:t,:e,:c,:n,:p,:k) RETURNING id"),
            {"v": venta_id, "t": tipo, "e": estado, "c": cufe, "n": consecutivo, "p": prefijo, "k": key},
        )
    ).scalar_one()


async def test_pos_aceptado_poblado_y_sin_documento_ausente(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        v_pos = await _venta(s)
        v_sin = await _venta(s)
        await _doc(s, v_pos, tipo="pos", estado="aceptada", cufe="CUDE-1",
                   consecutivo=7, prefijo="DPOS", key="pos:1")
        await s.commit()
        estados = await SqlFacturacionRepository(s).estados_por_ventas([v_pos, v_sin])
    assert set(estados) == {v_pos}                          # la venta sin documento NO aparece
    e = estados[v_pos]
    assert isinstance(e, EstadoFiscalVenta)
    assert (e.tipo, e.estado, e.cufe, e.numero, e.prefijo) == ("pos", "aceptada", "CUDE-1", 7, "DPOS")


async def test_prioriza_no_anulado(tenant):
    """Histórico con un documento anulado + uno vivo para la misma venta → gana el no-anulado."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        vid = await _venta(s)
        await _doc(s, vid, tipo="pos", estado="anulada", cufe="OLD", consecutivo=1, prefijo="DPOS", key="anu")
        await _doc(s, vid, tipo="factura", estado="aceptada", cufe="NEW", consecutivo=2, prefijo="FPR", key="ok")
        await s.commit()
        estados = await SqlFacturacionRepository(s).estados_por_ventas([vid])
    assert estados[vid].estado == "aceptada" and estados[vid].cufe == "NEW"


async def test_batch_una_sola_query(tenant):
    """3 ventas → UNA sola sentencia SQL (sin N+1), verificado con un contador de cursor_execute."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        vids = [await _venta(s) for _ in range(3)]
        await _doc(s, vids[1], tipo="factura", estado="pendiente", key="pend")  # número/prefijo NULL (POS/FE pendiente)
        await s.commit()
        repo = SqlFacturacionRepository(s)
        conteo = {"q": 0}

        def _contar(*_a, **_k):
            conteo["q"] += 1

        event.listen(tenant.engine.sync_engine, "before_cursor_execute", _contar)
        try:
            estados = await repo.estados_por_ventas(vids)
        finally:
            event.remove(tenant.engine.sync_engine, "before_cursor_execute", _contar)
    assert conteo["q"] == 1                                  # UNA query para las 3 ventas
    assert set(estados) == {vids[1]} and estados[vids[1]].estado == "pendiente"
    assert estados[vids[1]].numero is None and estados[vids[1]].cufe is None


async def test_lista_vacia_no_consulta(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        assert await SqlFacturacionRepository(s).estados_por_ventas([]) == {}
