"""Entregable 4 — hardening transaccional de los side-writes best-effort (integración, Postgres).

PIN del fix aprobado: un side-write best-effort que FALLA (server-side) NO debe abortar la
transacción del turno. Hoy los repos hacen `flush()` sobre la MISMA transacción del turno; si el
write falla, tragarse la excepción de Python NO basta —la transacción queda envenenada
(InFailedSqlTransaction) y el commit final revierte la venta que el usuario ya vio "registrada".
El fix aísla cada write en un SAVEPOINT (`session.begin_nested()`): el fallo revierte solo hasta el
savepoint y la transacción del turno sigue sana para commitear la venta.

Este test reusa el fixture `tenant` (base efímera real, igual que test_migracion_tenant_0004).
Contra el código actual (sin savepoints) DEBE FALLAR: la venta del paso 1 se pierde. Con el savepoint
DEBE PASAR: la venta sobrevive al fallo del side-write.
"""
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from modules.memoria.repository import SqlMemoriaRepository
from modules.memoria.service import MemoriaService


async def test_side_write_best_effort_no_aborta_la_venta_del_turno(tenant):
    async with AsyncSession(tenant.engine) as s:
        # 1) Mutación de negocio conocida del turno: un vendedor + una venta mínima.
        vendedor_id = (
            await s.execute(
                text("INSERT INTO usuarios (nombre, rol) VALUES ('Vendedor','vendedor') RETURNING id")
            )
        ).scalar_one()
        await s.execute(
            text(
                "INSERT INTO ventas (consecutivo, vendedor_id, subtotal, impuestos, total, metodo_pago) "
                "VALUES (1, :v, 1000, 0, 1000, 'efectivo')"
            ),
            {"v": vendedor_id},
        )
        await s.flush()

        # Prepara una colisión de PK determinista para el PRÓXIMO insert de conversaciones_bot:
        # se rearma la secuencia para que el siguiente nextval choque con esta fila ya existente.
        previo_id = (
            await s.execute(
                text(
                    "INSERT INTO conversaciones_bot (chat_id, rol, contenido) "
                    "VALUES (555,'user','previo') RETURNING id"
                )
            )
        ).scalar_one()
        seq = (
            await s.execute(text("SELECT pg_get_serial_sequence('conversaciones_bot','id')"))
        ).scalar_one()
        assert seq is not None                       # conversaciones_bot.id es serial (BIGSERIAL)
        await s.execute(text("SELECT setval(:seq, :v, false)"), {"seq": seq, "v": previo_id})
        await s.flush()

        # 2) Side-write best-effort que FALLA server-side (PK duplicada); el service lo traga.
        await MemoriaService(SqlMemoriaRepository(s)).guardar_turno(
            555, usuario="hola", asistente="respuesta"
        )

        # 3) Commit del turno (en RED ya viene de una transacción envenenada → revienta; lo toleramos
        #    porque lo que importa es si la venta sobrevive, no si el commit lanza).
        try:
            await s.commit()
        except Exception:
            pass

    # 4) En una sesión NUEVA: la venta del paso 1 debe seguir persistida.
    async with AsyncSession(tenant.engine) as s2:
        ventas = (
            await s2.execute(text("SELECT count(*) FROM ventas WHERE consecutivo = 1"))
        ).scalar_one()

    assert ventas == 1   # RED: 0 (la venta confirmada se perdió); GREEN: 1 (el savepoint aisló el fallo)
