"""F2.2 — cierre POS post-venta (ADR 0012 D2): núcleo, carrera commit↔encolado y disparo en el bot.

- `cerrar_venta_con_pos` con fakes: gate de capacidad, idempotencia y el ORDEN commit→enqueue (fix de
  auditoría: el worker no debe correr `emitir()` antes de que la fila exista).
- Integración: una sesión NUEVA ya ve el pendiente POS cuando se encola (commit antes de encolar).
- Camino del bot: `_registrar_venta` (convergencia de bypass/confirmación/modelo) invoca el cierre.
- Exclusión POS↔FE (D1) contra base efímera con el repo real."""
from decimal import Decimal
from types import SimpleNamespace

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ai.envelope import Contexto
from ai.tools import Deps, ItemVentaArg, RegistrarVentaArgs, _registrar_venta
from modules.facturacion.pos_hook import CierrePos, cerrar_venta_con_pos
from modules.facturacion.repository import FacturaLeer, SqlFacturacionRepository
from modules.facturacion.service import ConfigFiscal, FacturacionService

_CAPS = frozenset({"pos", "facturacion_electronica", "pos_electronico"})
_CONFIG = ConfigFiscal(resolution_number="r", prefix="FPR", notes="", city_id_default=None)


def _factura(*, id=55) -> FacturaLeer:
    return FacturaLeer(id=id, venta_id=10, tipo="pos", prefijo=None, consecutivo=None, cufe=None,
                       estado="pendiente", idempotency_key="pos:10", intentos=0)


class _SesionFake:
    """Sesión fake: `commit` deja huella en el orden compartido (para verificar commit-antes-de-encolar)."""

    def __init__(self, orden: list[str]) -> None:
        self._orden = orden

    async def commit(self) -> None:
        self._orden.append("commit")


class _SvcFake:
    def __init__(self, resultado) -> None:
        self._r = resultado

    async def crear_pendiente_pos(self, venta_id):
        return self._r


# --- núcleo: commit ANTES de encolar -----------------------------------------

async def test_core_commitea_antes_de_encolar():
    orden: list[str] = []

    async def enqueue(job, *args):
        orden.append("enqueue")

    fid = await cerrar_venta_con_pos(
        servicio=_SvcFake((_factura(), True)), session=_SesionFake(orden), venta_id=10,
        tenant_id=7, capacidades=_CAPS, enqueue=enqueue,
    )
    assert fid == 55
    assert orden == ["commit", "enqueue"]          # el orden ES el fix de la carrera


async def test_core_pos_apagado_no_toca_la_transaccion():
    orden: list[str] = []

    async def enqueue(job, *args):
        orden.append("enqueue")

    fid = await cerrar_venta_con_pos(
        servicio=_SvcFake((None, False)), session=_SesionFake(orden), venta_id=10,
        tenant_id=7, capacidades=frozenset({"pos"}), enqueue=enqueue,   # sin pos_electronico
    )
    assert fid is None and orden == []             # no commitea ni encola → no altera la venta


async def test_core_pendiente_existente_no_reencola():
    orden: list[str] = []

    async def enqueue(job, *args):
        orden.append("enqueue")

    fid = await cerrar_venta_con_pos(
        servicio=_SvcFake((_factura(), False)), session=_SesionFake(orden), venta_id=10,   # creada=False
        tenant_id=7, capacidades=_CAPS, enqueue=enqueue,
    )
    assert fid is None and orden == []             # idempotente: ni commit ni segunda emisión


# --- integración: la fila existe cuando se encola (commit ocurrió antes) ------

async def _crear_venta(s: AsyncSession) -> int:
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


async def test_cierre_fila_visible_al_encolar(tenant):
    """Carrera commit↔encolado: cuando se llama al enqueue, una sesión NUEVA ya ve la fila POS."""
    visto: dict = {}

    async def enqueue_que_verifica(job, tenant_id, factura_id):
        async with AsyncSession(tenant.engine) as s2:        # sesión SEPARADA: solo ve lo commiteado
            fila = (
                await s2.execute(
                    text("SELECT tipo, estado FROM facturas_electronicas WHERE id=:i"), {"i": factura_id}
                )
            ).one_or_none()
        visto.update(job=job, fila=fila)

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        vid = await _crear_venta(s)
        await s.commit()                                     # la venta debe existir (FK venta_id→ventas)
        svc = FacturacionService(SqlFacturacionRepository(s), None, _CONFIG)
        fid = await cerrar_venta_con_pos(
            servicio=svc, session=s, venta_id=vid, tenant_id=7, capacidades=_CAPS,
            enqueue=enqueue_que_verifica,
        )
    assert fid is not None
    assert visto["job"] == "emitir_documento"
    assert visto["fila"] is not None and visto["fila"].tipo == "pos"   # commit ANTES de encolar


# --- camino del bot: _registrar_venta dispara el cierre ----------------------

class _VentasFake:
    def __init__(self, *, replay: bool) -> None:
        self._replay = replay

    async def registrar_venta(self, datos, vendedor_id):
        venta = SimpleNamespace(id=99, consecutivo=5, subtotal=Decimal("100"),
                                impuestos=Decimal("19"), total=Decimal("119"), metodo_pago="efectivo")
        return SimpleNamespace(venta=venta, replay=self._replay)


class _CierreFake:
    def __init__(self) -> None:
        self.llamadas: list[tuple] = []

    async def cerrar(self, venta_id, *, tenant_id, capacidades):
        self.llamadas.append((venta_id, tenant_id, capacidades))


def _ctx() -> Contexto:
    return Contexto(tenant_id=7, usuario_id=1, rol="vendedor", capacidades=_CAPS)


def _args() -> RegistrarVentaArgs:
    return RegistrarVentaArgs(
        items=[ItemVentaArg(producto_id=5, cantidad=Decimal("1"))], metodo_pago="efectivo",
    )


async def test_registrar_venta_dispara_cierre_pos():
    cierre = _CierreFake()
    deps = Deps(ventas=_VentasFake(replay=False), caja=None, fiados=None, clientes=None, cierre_pos=cierre)
    res = await _registrar_venta(_args(), _ctx(), deps)
    assert res.ok is True
    assert cierre.llamadas == [(99, 7, _CAPS)]      # bypass/confirmación/modelo convergen aquí


async def test_registrar_venta_replay_no_dispara_cierre():
    cierre = _CierreFake()
    deps = Deps(ventas=_VentasFake(replay=True), caja=None, fiados=None, clientes=None, cierre_pos=cierre)
    res = await _registrar_venta(_args(), _ctx(), deps)
    assert res.ok is True and cierre.llamadas == []  # idempotencia de la venta: el cierre ya ocurrió


async def test_registrar_venta_sin_cierre_configurado_no_rompe():
    deps = Deps(ventas=_VentasFake(replay=False), caja=None, fiados=None, clientes=None)  # cierre_pos=None
    res = await _registrar_venta(_args(), _ctx(), deps)
    assert res.ok is True                            # plataformas sin POS: la venta sigue intacta


# --- exclusión POS↔FE en integración (repo real) ----------------------------

async def test_fe_suprime_pos_pendiente(tenant):
    """D1: crear una FE NUEVA borra un POS aún pendiente de la misma venta; el POS no resucita."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        repo = SqlFacturacionRepository(s)
        svc = FacturacionService(repo, None, _CONFIG)
        vid = await _crear_venta(s)
        pos, creada = await svc.crear_pendiente_pos(vid)
        await s.commit()
        assert creada and pos.tipo == "pos"
        fe = await svc.crear_pendiente(vid, idempotency_key="fe-key-1")
        await s.commit()
        assert fe.tipo == "factura"
        filas = (
            await s.execute(
                text("SELECT tipo FROM facturas_electronicas WHERE venta_id=:v ORDER BY tipo"), {"v": vid}
            )
        ).scalars().all()
    assert filas == ["factura"]                      # el POS pendiente se borró; queda solo la FE


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
