"""F2.2 — hook post-venta del POS (ADR 0012 D2) + exclusión POS↔FE (D1) en integración.

`_encolar_si_aplica` con fakes (gate de capacidad + solo-si-creada). La exclusión real (FE suprime el
POS pendiente; POS no se crea si ya hay documento) se prueba contra la base efímera con el repo real."""
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from modules.facturacion.pos_hook import _encolar_si_aplica
from modules.facturacion.repository import FacturaLeer, SqlFacturacionRepository
from modules.facturacion.service import ConfigFiscal, FacturacionService

_CAPS = frozenset({"pos", "facturacion_electronica", "pos_electronico"})
_CONFIG = ConfigFiscal(resolution_number="r", prefix="FPR", notes="", city_id_default=None)


class _FakeEnqueue:
    def __init__(self):
        self.llamadas = []

    async def __call__(self, job, *args):
        self.llamadas.append((job, *args))


class _ServicioFake:
    def __init__(self, resultado):
        self._r = resultado

    async def crear_pendiente_pos(self, venta_id):
        return self._r


async def test_hook_encola_cuando_pos_activo_y_creada():
    enq = _FakeEnqueue()
    f = FacturaLeer(id=55, venta_id=10, tipo="pos", prefijo=None, consecutivo=None, cufe=None,
                    estado="pendiente", idempotency_key="pos:10", intentos=0)
    fid = await _encolar_si_aplica(
        servicio=_ServicioFake((f, True)), capacidades=_CAPS, tenant_id=7,
        enqueue=enq, venta_id=10,
    )
    assert fid == 55 and enq.llamadas == [("emitir_documento", 7, 55)]


async def test_hook_no_encola_sin_capacidad():
    enq = _FakeEnqueue()
    fid = await _encolar_si_aplica(
        servicio=_ServicioFake((None, True)), capacidades=frozenset({"pos"}), tenant_id=7,
        enqueue=enq, venta_id=10,
    )
    assert fid is None and enq.llamadas == []     # POS apagado: ni siquiera crea pendiente


async def test_hook_no_reencola_si_ya_existia():
    enq = _FakeEnqueue()
    f = FacturaLeer(id=55, venta_id=10, tipo="pos", prefijo=None, consecutivo=None, cufe=None,
                    estado="pendiente", idempotency_key="pos:10", intentos=0)
    fid = await _encolar_si_aplica(
        servicio=_ServicioFake((f, False)), capacidades=_CAPS, tenant_id=7,   # creada=False
        enqueue=enq, venta_id=10,
    )
    assert fid is None and enq.llamadas == []     # idempotente: no segunda emisión


async def test_hook_no_encola_si_excluido():
    enq = _FakeEnqueue()
    fid = await _encolar_si_aplica(
        servicio=_ServicioFake((None, False)), capacidades=_CAPS, tenant_id=7,  # excluido por FE/POS
        enqueue=enq, venta_id=10,
    )
    assert fid is None and enq.llamadas == []


# --- exclusión POS↔FE en integración (repo real) ----------------------------

async def _crear_venta(s: AsyncSession) -> int:
    """Inserta un vendedor + una venta y devuelve su id (la FK venta_id→ventas lo exige)."""
    uid = (
        await s.execute(text("INSERT INTO usuarios (nombre, rol) VALUES ('V','vendedor') RETURNING id"))
    ).scalar_one()
    cons = (await s.execute(text("SELECT nextval('ventas_consecutivo_seq')"))).scalar_one()
    vid = (
        await s.execute(
            text("INSERT INTO ventas (consecutivo, vendedor_id, fecha, subtotal, impuestos, total, metodo_pago) "
                 "VALUES (:c,:u, now(), 10000, 1900, 11900, 'efectivo') RETURNING id"),
            {"c": cons, "u": uid},
        )
    ).scalar_one()
    return vid


async def test_fe_suprime_pos_pendiente(tenant):
    """D1: crear una FE NUEVA borra un POS aún pendiente de la misma venta; el POS no resucita."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        repo = SqlFacturacionRepository(s)
        svc = FacturacionService(repo, None, _CONFIG)
        vid = await _crear_venta(s)
        pos, creada = await svc.crear_pendiente_pos(vid)
        await s.commit()
        assert creada and pos.tipo == "pos"
        # El cliente pide factura de la misma venta → se crea FE y se suprime el POS pendiente.
        fe = await svc.crear_pendiente(vid, idempotency_key="fe-key-1")
        await s.commit()
        assert fe.tipo == "factura"
        filas = (
            await s.execute(
                text("SELECT tipo FROM facturas_electronicas WHERE venta_id=:v ORDER BY tipo"), {"v": vid}
            )
        ).scalars().all()
    assert filas == ["factura"]                    # el POS pendiente se borró; queda solo la FE


async def test_pos_excluido_si_venta_ya_tiene_fe(tenant):
    """D1 (otra dirección): si la venta ya tiene FE, el cierre POS no crea otro documento."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        repo = SqlFacturacionRepository(s)
        svc = FacturacionService(repo, None, _CONFIG)
        vid = await _crear_venta(s)
        await svc.crear_pendiente(vid, idempotency_key="fe-key-1")
        await s.commit()
        pos, creada = await svc.crear_pendiente_pos(vid)
        await s.commit()
    assert pos is None and creada is False
