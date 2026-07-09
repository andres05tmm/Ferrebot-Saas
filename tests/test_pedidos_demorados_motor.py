"""Motor de avisos de pedido demorado (F6 reforma dashboard) — determinista, contra base efímera.

Molde de `test_pagar_motor`: el criterio de demora (fecha prometida > promedio del proveedor >
nada), el dedup por `ultimo_aviso_at` + cadencia, y que solo un envío EXITOSO sella. El barrido
multi-tenant del worker es smoke manual (como los demás crons).
"""
from datetime import timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

import modules.maquinaria.models  # noqa: F401  (registra `maquinas`: FK de gastos)
import modules.obra.models  # noqa: F401  (registra `obras`: FK de compras/gastos)
from core.config.timezone import now_co
from modules.caja.repository import SqlCajaRepository
from modules.caja.service import CajaService
from modules.compras.repository import SqlComprasRepository
from modules.compras.service import ComprasService
from modules.inventario.repository import SqlInventarioRepository
from modules.inventario.service import InventarioService
from modules.pedidos_proveedor.repository import SqlPedidosProveedorRepository
from modules.pedidos_proveedor.schemas import PedidoCrear, ProveedorRef
from modules.pedidos_proveedor.service import (
    PedidosProveedorService,
    procesar_avisos_demorados,
)
from modules.proveedores.repository import SqlProveedoresRepository


def _svc(s: AsyncSession) -> PedidosProveedorService:
    return PedidosProveedorService(
        SqlPedidosProveedorRepository(s),
        compras=ComprasService(SqlComprasRepository(s)),
        compras_repo=SqlComprasRepository(s),
        proveedores=SqlProveedoresRepository(s),
        caja=CajaService(SqlCajaRepository(s)),
        inventario=InventarioService(SqlInventarioRepository(s)),
    )


class SpyEnviar:
    def __init__(self, ok: bool = True) -> None:
        self.avisos = []
        self._ok = ok

    async def __call__(self, aviso) -> bool:
        self.avisos.append(aviso)
        return self._ok


async def _crear_pedido(s: AsyncSession, uid: int, *, proveedor="Ferrisariato", **extra) -> int:
    res = await _svc(s).crear(
        PedidoCrear(proveedor=ProveedorRef(nombre=proveedor), descripcion="lo de siempre", **extra),
        usuario_id=uid,
    )
    return res.pedido.id


async def _envejecer(s: AsyncSession, pedido_id: int, *, horas: float) -> None:
    """Retrocede `fecha_pedido` (el reloj del cronómetro) para simular un pedido viejo."""
    await s.execute(
        text("UPDATE pedidos_proveedor SET fecha_pedido = fecha_pedido - make_interval(secs => :s) "
             "WHERE id = :id"),
        {"s": horas * 3600, "id": pedido_id},
    )


async def _dar_historial(s: AsyncSession, pedido_id: int, *, lead_horas: float) -> None:
    """Convierte un pedido en historial recibido con un lead time exacto (alimenta el promedio)."""
    await s.execute(
        text("UPDATE pedidos_proveedor SET estado = 'recibido', "
             "fecha_recepcion = fecha_pedido + make_interval(secs => :s) WHERE id = :id"),
        {"s": lead_horas * 3600, "id": pedido_id},
    )


async def _ultimo_aviso(engine, pedido_id: int):
    async with AsyncSession(engine) as s:
        return (
            await s.execute(
                text("SELECT ultimo_aviso_at FROM pedidos_proveedor WHERE id = :id"),
                {"id": pedido_id},
            )
        ).scalar_one()


# --- Criterio de demora -------------------------------------------------------------------------

async def test_fecha_estimada_pasada_avisa_y_sella(tenant, seed_producto):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid, _pid = await seed_producto(s)
        await s.commit()
        ayer = (now_co() - timedelta(days=1)).date()
        pid_pedido = await _crear_pedido(s, uid, fecha_estimada=ayer)
        await s.commit()

    enviar = SpyEnviar()
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        n = await procesar_avisos_demorados(
            SqlPedidosProveedorRepository(s), ahora=now_co(), enviar=enviar,
        )
        await s.commit()

    assert n == 1
    assert len(enviar.avisos) == 1
    demorado = enviar.avisos[0].pedidos[0]
    assert demorado.pedido_id == pid_pedido
    assert demorado.motivo == "estimada"
    assert demorado.proveedor_nombre == "Ferrisariato"
    assert await _ultimo_aviso(tenant.engine, pid_pedido) is not None   # dedup sellado


async def test_fecha_estimada_vigente_no_avisa_aunque_pase_el_promedio(tenant, seed_producto):
    """La promesa explícita del proveedor GANA sobre el promedio: si dijo 'llega el viernes',
    no es demora aunque el promedio histórico ya se haya pasado."""
    manana = (now_co() + timedelta(days=1)).date()
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid, _pid = await seed_producto(s)
        await s.commit()
        historico = await _crear_pedido(s, uid)               # mismo proveedor: alimenta el promedio
        await _dar_historial(s, historico, lead_horas=5)      # promedio = 5h
        vivo = await _crear_pedido(s, uid, fecha_estimada=manana)
        await _envejecer(s, vivo, horas=48)                   # 48h > promedio, pero la promesa vive
        await s.commit()

    enviar = SpyEnviar()
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        n = await procesar_avisos_demorados(
            SqlPedidosProveedorRepository(s), ahora=now_co(), enviar=enviar,
        )
        await s.commit()

    assert n == 0 and enviar.avisos == []


async def test_sin_fecha_estimada_el_promedio_del_proveedor_es_la_vara(tenant, seed_producto):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid, _pid = await seed_producto(s)
        await s.commit()
        historico = await _crear_pedido(s, uid)
        await _dar_historial(s, historico, lead_horas=10)     # promedio = 10h
        joven = await _crear_pedido(s, uid)                   # recién pedido: 0h < 10h → al día
        viejo = await _crear_pedido(s, uid)
        await _envejecer(s, viejo, horas=24)                  # 24h > 10h → demorado
        await s.commit()

    enviar = SpyEnviar()
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        n = await procesar_avisos_demorados(
            SqlPedidosProveedorRepository(s), ahora=now_co(), enviar=enviar,
        )
        await s.commit()

    assert n == 1
    demorado = enviar.avisos[0].pedidos[0]
    assert demorado.pedido_id == viejo
    assert demorado.motivo == "promedio"
    assert demorado.promedio_proveedor_horas is not None
    assert await _ultimo_aviso(tenant.engine, joven) is None   # el que va al día no se sella


async def test_sin_promesa_ni_historial_no_hay_vara_ni_aviso(tenant, seed_producto):
    """Cero falsas alarmas: un proveedor nuevo sin fecha prometida no tiene contra qué medirse."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid, _pid = await seed_producto(s)
        await s.commit()
        pedido = await _crear_pedido(s, uid, proveedor="Proveedor Nuevo")
        await _envejecer(s, pedido, horas=200)                # viejísimo, pero sin vara
        await s.commit()

    enviar = SpyEnviar()
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        n = await procesar_avisos_demorados(
            SqlPedidosProveedorRepository(s), ahora=now_co(), enviar=enviar,
        )
        await s.commit()

    assert n == 0 and enviar.avisos == []


# --- Dedup por cadencia + envío fallido -----------------------------------------------------------

async def test_cadencia_no_reavisa_hasta_que_pase_y_luego_si(tenant, seed_producto):
    ayer = (now_co() - timedelta(days=1)).date()
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid, _pid = await seed_producto(s)
        await s.commit()
        await _crear_pedido(s, uid, fecha_estimada=ayer)
        await s.commit()

    ahora = now_co()
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        repo = SqlPedidosProveedorRepository(s)
        n1 = await procesar_avisos_demorados(repo, ahora=ahora, enviar=SpyEnviar())
        n2 = await procesar_avisos_demorados(repo, ahora=ahora, enviar=SpyEnviar())
        await s.commit()
    assert (n1, n2) == (1, 0)                                 # misma corrida doble: dedup

    # Pasada la cadencia (24h + margen) el pedido sigue en camino → se vuelve a avisar.
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        n3 = await procesar_avisos_demorados(
            SqlPedidosProveedorRepository(s), ahora=ahora + timedelta(hours=25), enviar=SpyEnviar(),
        )
        await s.commit()
    assert n3 == 1


async def test_envio_fallido_no_sella_y_se_reintenta(tenant, seed_producto):
    ayer = (now_co() - timedelta(days=1)).date()
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid, _pid = await seed_producto(s)
        await s.commit()
        pedido = await _crear_pedido(s, uid, fecha_estimada=ayer)
        await s.commit()

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        n = await procesar_avisos_demorados(
            SqlPedidosProveedorRepository(s), ahora=now_co(), enviar=SpyEnviar(ok=False),
        )
        await s.commit()
    assert n == 0
    assert await _ultimo_aviso(tenant.engine, pedido) is None   # NO sellado: se reintenta

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        n = await procesar_avisos_demorados(
            SqlPedidosProveedorRepository(s), ahora=now_co(), enviar=SpyEnviar(),
        )
        await s.commit()
    assert n == 1                                               # la próxima corrida sí avisa
