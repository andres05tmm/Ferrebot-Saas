"""F2.3a — cierre fiscal por venta (ADR 0014): el documento lo decide capacidad×intención.

Generaliza el cierre POS (ADR 0012 D2) a un único núcleo `cerrar_venta_fiscal` que rutea POS/FE/nada:
- `_resolver_documento` (puro): la matriz capacidad×intención (POS-default, FE on-demand, FE-only, nada).
- núcleo con fakes: ruteo, gate por capacidad, idempotencia y el ORDEN commit→enqueue (fix de auditoría).
- integración: una sesión NUEVA ya ve el pendiente cuando se encola (commit antes de encolar); FE-only.
- camino del bot: `_registrar_venta` (convergencia de bypass/confirmación/modelo) invoca el cierre.
- exclusión POS↔FE (D1) contra base efímera con el repo real."""
from decimal import Decimal
from types import SimpleNamespace

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ai.envelope import Contexto
from ai.tools import Deps, ItemVentaArg, RegistrarVentaArgs, _registrar_venta
from modules.facturacion.pos_hook import (
    CierrePos,
    _resolver_documento,
    cerrar_venta_fiscal,
)
from modules.facturacion.repository import FacturaLeer, SqlFacturacionRepository
from modules.facturacion.service import ConfigFiscal, FacturacionService

_POS = frozenset({"pos", "facturacion_electronica", "pos_electronico"})  # tenant POS (FE a pedido)
_FE_ONLY = frozenset({"pos", "facturacion_electronica"})                 # tenant FE-only
_SIN_FISCAL = frozenset({"pos"})                                         # vende, sin documento DIAN
_CONFIG = ConfigFiscal(resolution_number="r", prefix="FPR", notes="", city_id_default=None)


def _factura(*, id=55, tipo="pos") -> FacturaLeer:
    return FacturaLeer(id=id, venta_id=10, tipo=tipo, prefijo=None, consecutivo=None, cufe=None,
                       estado="pendiente", idempotency_key=f"{tipo}:10", intentos=0)


class _SesionFake:
    """Sesión fake: `commit` deja huella en el orden compartido (para verificar commit-antes-de-encolar)."""

    def __init__(self, orden: list[str]) -> None:
        self._orden = orden

    async def commit(self) -> None:
        self._orden.append("commit")


class _SvcFake:
    """Servicio fake que registra qué pendiente se pidió (POS o FE) y devuelve un resultado fijo."""

    def __init__(self, *, pos=None, fe=None) -> None:
        self._pos = pos
        self._fe = fe
        self.llamado: str | None = None

    async def crear_pendiente_pos(self, venta_id):
        self.llamado = "pos"
        return self._pos

    async def crear_pendiente_fe(self, venta_id):
        self.llamado = "fe"
        return self._fe


# --- matriz capacidad×intención (puro) ---------------------------------------

def test_resolver_pos_default():
    assert _resolver_documento(_POS, None) == "pos"          # POS-default: FE a pedido


def test_resolver_fe_on_demand_sobre_tenant_pos():
    assert _resolver_documento(_POS, "fe") == "fe"           # intención FE con FE → FE (suprime POS, D1)


def test_resolver_fe_only_default():
    assert _resolver_documento(_FE_ONLY, None) == "fe"       # FE-only: FE por defecto


def test_resolver_sin_capacidades_no_documento():
    assert _resolver_documento(_SIN_FISCAL, None) is None    # sin capacidad fiscal → venta solo interna


def test_resolver_intencion_pos_sin_capacidad_cae_al_default():
    assert _resolver_documento(_FE_ONLY, "pos") == "fe"      # pide POS sin tenerlo → default por capacidad


def test_resolver_intencion_fe_sin_capacidad_no_documento():
    assert _resolver_documento(_SIN_FISCAL, "fe") is None    # la intención no crea lo que el tenant no puede


# --- núcleo: ruteo + commit ANTES de encolar ---------------------------------

async def test_core_pos_default_crea_pos_y_commitea_antes_de_encolar():
    orden: list[str] = []

    async def enqueue(job, *args):
        orden.append("enqueue")

    svc = _SvcFake(pos=(_factura(), True))
    fid = await cerrar_venta_fiscal(
        servicio=svc, session=_SesionFake(orden), venta_id=10,
        tenant_id=7, capacidades=_POS, enqueue=enqueue,
    )
    assert fid == 55 and svc.llamado == "pos"
    assert orden == ["commit", "enqueue"]          # el orden ES el fix de la carrera


async def test_core_intencion_fe_crea_fe_no_pos():
    orden: list[str] = []

    async def enqueue(job, *args):
        orden.append("enqueue")

    svc = _SvcFake(fe=(_factura(id=77, tipo="factura"), True))
    fid = await cerrar_venta_fiscal(
        servicio=svc, session=_SesionFake(orden), venta_id=10, tenant_id=7,
        capacidades=_POS, enqueue=enqueue, intencion="fe",
    )
    assert fid == 77 and svc.llamado == "fe"       # FE on-demand: nunca toca crear_pendiente_pos
    assert orden == ["commit", "enqueue"]


async def test_core_fe_only_default_crea_fe():
    orden: list[str] = []

    async def enqueue(job, *args):
        orden.append("enqueue")

    svc = _SvcFake(fe=(_factura(id=88, tipo="factura"), True))
    fid = await cerrar_venta_fiscal(
        servicio=svc, session=_SesionFake(orden), venta_id=10, tenant_id=7,
        capacidades=_FE_ONLY, enqueue=enqueue,
    )
    assert fid == 88 and svc.llamado == "fe" and orden == ["commit", "enqueue"]


async def test_core_sin_capacidades_no_crea_documento():
    orden: list[str] = []

    async def enqueue(job, *args):
        orden.append("enqueue")

    svc = _SvcFake()
    fid = await cerrar_venta_fiscal(
        servicio=svc, session=_SesionFake(orden), venta_id=10,
        tenant_id=7, capacidades=_SIN_FISCAL, enqueue=enqueue,
    )
    assert fid is None and svc.llamado is None      # no se consulta el servicio
    assert orden == []                              # no commitea ni encola → no altera la venta


async def test_core_pendiente_pos_existente_no_reencola():
    orden: list[str] = []

    async def enqueue(job, *args):
        orden.append("enqueue")

    fid = await cerrar_venta_fiscal(
        servicio=_SvcFake(pos=(_factura(), False)), session=_SesionFake(orden), venta_id=10,
        tenant_id=7, capacidades=_POS, enqueue=enqueue,   # creada=False
    )
    assert fid is None and orden == []             # idempotente: ni commit ni segunda emisión


async def test_core_pos_excluido_por_fe_existente_no_commitea():
    orden: list[str] = []

    async def enqueue(job, *args):
        orden.append("enqueue")

    fid = await cerrar_venta_fiscal(
        servicio=_SvcFake(pos=(None, False)), session=_SesionFake(orden), venta_id=10,
        tenant_id=7, capacidades=_POS, enqueue=enqueue,   # la venta ya tiene FE (D1)
    )
    assert fid is None and orden == []


async def test_core_pendiente_fe_existente_no_reencola():
    orden: list[str] = []

    async def enqueue(job, *args):
        orden.append("enqueue")

    fid = await cerrar_venta_fiscal(
        servicio=_SvcFake(fe=(_factura(id=88, tipo="factura"), False)), session=_SesionFake(orden),
        venta_id=10, tenant_id=7, capacidades=_FE_ONLY, enqueue=enqueue,   # creada=False
    )
    assert fid is None and orden == []             # idempotente en FE igual que en POS


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
        fid = await cerrar_venta_fiscal(
            servicio=svc, session=s, venta_id=vid, tenant_id=7, capacidades=_POS,
            enqueue=enqueue_que_verifica,
        )
    assert fid is not None
    assert visto["job"] == "emitir_documento"
    assert visto["fila"] is not None and visto["fila"].tipo == "pos"   # commit ANTES de encolar


async def test_cierre_fe_only_crea_factura(tenant):
    """Tenant FE-only (sin `pos_electronico`): el cierre por defecto crea un pendiente tipo `factura`."""
    encolado: dict = {}

    async def enqueue(job, tenant_id, factura_id):
        encolado.update(job=job, factura_id=factura_id)

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        vid = await _crear_venta(s)
        await s.commit()
        svc = FacturacionService(SqlFacturacionRepository(s), None, _CONFIG)
        fid = await cerrar_venta_fiscal(
            servicio=svc, session=s, venta_id=vid, tenant_id=7, capacidades=_FE_ONLY, enqueue=enqueue,
        )
        tipo = (
            await s.execute(
                text("SELECT tipo FROM facturas_electronicas WHERE id=:i"), {"i": fid}
            )
        ).scalar_one()
    assert fid is not None and tipo == "factura"
    assert encolado == {"job": "emitir_documento", "factura_id": fid}


async def test_cierre_sin_capacidades_no_crea_fila(tenant):
    """Sin capacidad fiscal: la venta queda solo interna — ninguna fila en `facturas_electronicas`."""
    orden: list[str] = []

    async def enqueue(job, *args):
        orden.append("enqueue")

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        vid = await _crear_venta(s)
        await s.commit()
        svc = FacturacionService(SqlFacturacionRepository(s), None, _CONFIG)
        fid = await cerrar_venta_fiscal(
            servicio=svc, session=s, venta_id=vid, tenant_id=7, capacidades=_SIN_FISCAL, enqueue=enqueue,
        )
        n = (
            await s.execute(
                text("SELECT count(*) FROM facturas_electronicas WHERE venta_id=:v"), {"v": vid}
            )
        ).scalar_one()
    assert fid is None and n == 0 and orden == []


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

    async def cerrar(self, venta_id, *, tenant_id, capacidades, intencion=None):
        self.llamadas.append((venta_id, tenant_id, capacidades, intencion))


def _ctx() -> Contexto:
    return Contexto(tenant_id=7, usuario_id=1, rol="vendedor", capacidades=_POS)


def _args() -> RegistrarVentaArgs:
    return RegistrarVentaArgs(
        items=[ItemVentaArg(producto_id=5, cantidad=Decimal("1"))], metodo_pago="efectivo",
    )


async def test_registrar_venta_dispara_cierre_pos():
    cierre = _CierreFake()
    deps = Deps(ventas=_VentasFake(replay=False), caja=None, fiados=None, clientes=None, cierre_pos=cierre)
    res = await _registrar_venta(_args(), _ctx(), deps)
    assert res.ok is True
    assert cierre.llamadas == [(99, 7, _POS, None)]   # bypass/confirmación/modelo convergen aquí; intención default


async def test_registrar_venta_replay_no_dispara_cierre():
    cierre = _CierreFake()
    deps = Deps(ventas=_VentasFake(replay=True), caja=None, fiados=None, clientes=None, cierre_pos=cierre)
    res = await _registrar_venta(_args(), _ctx(), deps)
    assert res.ok is True and cierre.llamadas == []  # idempotencia de la venta: el cierre ya ocurrió


async def test_registrar_venta_sin_cierre_configurado_no_rompe():
    deps = Deps(ventas=_VentasFake(replay=False), caja=None, fiados=None, clientes=None)  # cierre_pos=None
    res = await _registrar_venta(_args(), _ctx(), deps)
    assert res.ok is True                            # plataformas sin POS: la venta sigue intacta


# --- CierrePos: carga la config solo en la rama FE ---------------------------

async def test_cierrepos_carga_config_solo_en_rama_fe(tenant):
    """CierrePos carga la `ConfigFiscal` (control DB) SOLO cuando rutea FE; la rama POS no la toca."""
    cargado: list[int] = []

    async def cargar_config(tenant_id):
        cargado.append(tenant_id)
        return _CONFIG

    async def enqueue(job, *args):
        ...

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        cierre = CierrePos(s, enqueue=enqueue, cargar_config=cargar_config)
        vid_pos = await _crear_venta(s)
        await s.commit()
        await cierre.cerrar(vid_pos, tenant_id=7, capacidades=_POS)        # POS-default
        assert cargado == []                                              # no carga config
        vid_fe = await _crear_venta(s)
        await s.commit()
        await cierre.cerrar(vid_fe, tenant_id=7, capacidades=_FE_ONLY)     # FE-only
    assert cargado == [7]                                                 # carga config solo en FE


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


async def test_cierre_fe_on_demand_suprime_pos_pendiente(tenant):
    """D1 vía el núcleo: con un POS pendiente, cerrar con intención FE deja solo la factura."""
    enc: list[int] = []

    async def enqueue(job, tenant_id, factura_id):
        enc.append(factura_id)

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        repo = SqlFacturacionRepository(s)
        svc = FacturacionService(repo, None, _CONFIG)
        vid = await _crear_venta(s)
        await svc.crear_pendiente_pos(vid)               # POS pendiente previo
        await s.commit()
        fid = await cerrar_venta_fiscal(
            servicio=svc, session=s, venta_id=vid, tenant_id=7, capacidades=_POS,
            enqueue=enqueue, intencion="fe",
        )
        filas = (
            await s.execute(
                text("SELECT tipo FROM facturas_electronicas WHERE venta_id=:v ORDER BY tipo"), {"v": vid}
            )
        ).scalars().all()
    assert fid is not None and enc == [fid]
    assert filas == ["factura"]                          # la FE on-demand suprimió el POS (D1)


async def test_cierre_fe_only_idempotente(tenant):
    """FE-only: re-cerrar la misma venta no crea un segundo documento ni re-encola (idempotencia)."""
    enc: list[int] = []

    async def enqueue(job, tenant_id, factura_id):
        enc.append(factura_id)

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        svc = FacturacionService(SqlFacturacionRepository(s), None, _CONFIG)
        vid = await _crear_venta(s)
        await s.commit()
        fid1 = await cerrar_venta_fiscal(
            servicio=svc, session=s, venta_id=vid, tenant_id=7, capacidades=_FE_ONLY, enqueue=enqueue,
        )
        fid2 = await cerrar_venta_fiscal(
            servicio=svc, session=s, venta_id=vid, tenant_id=7, capacidades=_FE_ONLY, enqueue=enqueue,
        )
        n = (
            await s.execute(
                text("SELECT count(*) FROM facturas_electronicas WHERE venta_id=:v"), {"v": vid}
            )
        ).scalar_one()
    assert fid1 is not None and fid2 is None      # segundo cierre no re-encola
    assert enc == [fid1] and n == 1               # un solo documento


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
