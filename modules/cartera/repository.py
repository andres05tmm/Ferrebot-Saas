"""Repositorio de la cartera de alquiler: único lugar con SQL del módulo (regla no negociable #2).

El SALDO consumido NO vive aquí: se LEE del ledger de `modules/fiados` (`clientes.saldo_fiado` y
`fiados.saldo`). Este repo escribe el plano propio de la cartera —`cupos_alquiler` (tope de crédito),
`cargos_alquiler` (traza idempotente `RegistroHorasMaquina`→`Fiado`) y `cartera_config`— y hace lecturas
CROSS-MÓDULO de solo lectura sobre `obras`/`fiados`/`fiados_movimientos` (por import de su ORM/tabla, sin
tocar sus archivos). La sesión del tenant ES la transacción y la frontera del aislamiento (sin `empresa_id`).
"""
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from core.events import publish
from modules.cartera.models import CargoAlquiler, CarteraConfig, Cupo


class SqlCarteraAlquilerRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    # --- cupos ----------------------------------------------------------------
    async def cupo_activo(self, cliente_id: int) -> Cupo | None:
        """Cupo ACTIVO del cliente (el único parcial garantiza a lo sumo uno). None si no tiene."""
        return (
            await self._s.execute(
                select(Cupo).where(Cupo.cliente_id == cliente_id, Cupo.activo.is_(True))
            )
        ).scalar_one_or_none()

    async def obtener_cupo(self, cupo_id: int) -> Cupo | None:
        return await self._s.get(Cupo, cupo_id)

    async def crear_cupo(
        self,
        *,
        cliente_id: int,
        cupo: Decimal,
        vigente_desde: date,
        vigente_hasta: date | None,
        notas: str | None,
    ) -> Cupo:
        """Da de alta un cupo DESACTIVANDO el activo previo del cliente (histórico por vigencia).

        La desactivación va ANTES del INSERT (misma transacción) para no chocar con el único parcial
        `uq_cupos_alquiler_cliente_activo` (un solo cupo activo por cliente)."""
        await self._s.execute(
            update(Cupo).where(Cupo.cliente_id == cliente_id, Cupo.activo.is_(True)).values(activo=False)
        )
        await self._s.flush()
        fila = Cupo(
            cliente_id=cliente_id, cupo=cupo, vigente_desde=vigente_desde,
            vigente_hasta=vigente_hasta, notas=notas, activo=True,
        )
        self._s.add(fila)
        await self._s.flush()
        await publish(self._s, "cartera_cupo_actualizado", {"cliente_id": cliente_id, "cupo_id": fila.id})
        return fila

    async def actualizar_cupo(self, cupo: Cupo, cambios: dict) -> Cupo:
        """Aplica `cambios` (dict campo→valor validado) sobre el cupo cargado. Si reactiva (`activo=True`)
        desactiva primero cualquier otro activo del cliente (respeta el único parcial)."""
        if cambios.get("activo") is True and not cupo.activo:
            await self._s.execute(
                update(Cupo)
                .where(Cupo.cliente_id == cupo.cliente_id, Cupo.activo.is_(True), Cupo.id != cupo.id)
                .values(activo=False)
            )
            await self._s.flush()
        for campo, valor in cambios.items():
            setattr(cupo, campo, valor)
        await self._s.flush()
        await self._s.refresh(cupo)
        return cupo

    async def cupo_con_consumo(self, cupo_id: int) -> dict | None:
        """Un cupo (activo o no) + nombre del cliente + `consumido` (= `clientes.saldo_fiado`).

        Para la respuesta de alta/edición: a diferencia de `listar_cupos_con_consumo`, NO filtra por
        `activo` (un PUT que desactiva debe poder devolver la fila igual)."""
        fila = (
            await self._s.execute(
                text(
                    "SELECT ca.id, ca.cliente_id, c.nombre AS cliente_nombre, ca.cupo, "
                    "       ca.vigente_desde, ca.vigente_hasta, ca.activo, ca.notas, "
                    "       COALESCE(c.saldo_fiado, 0) AS consumido "
                    "FROM cupos_alquiler ca "
                    "LEFT JOIN clientes c ON c.id = ca.cliente_id "
                    "WHERE ca.id = :id"
                ),
                {"id": cupo_id},
            )
        ).first()
        return dict(fila._mapping) if fila is not None else None

    async def listar_cupos_con_consumo(self) -> list[dict]:
        """Cupos ACTIVOS + nombre del cliente + `consumido` (= `clientes.saldo_fiado`, ledger).

        LEFT JOIN a `clientes` para el nombre y el saldo; el disponible/semáforo los deriva el service.
        """
        filas = (
            await self._s.execute(
                text(
                    "SELECT ca.id, ca.cliente_id, c.nombre AS cliente_nombre, ca.cupo, "
                    "       ca.vigente_desde, ca.vigente_hasta, ca.activo, ca.notas, "
                    "       COALESCE(c.saldo_fiado, 0) AS consumido "
                    "FROM cupos_alquiler ca "
                    "LEFT JOIN clientes c ON c.id = ca.cliente_id "
                    "WHERE ca.activo "
                    "ORDER BY ca.cupo DESC"
                )
            )
        ).all()
        return [dict(fila._mapping) for fila in filas]

    # --- consumo (ledger) — lecturas cross-módulo de solo lectura -------------
    async def cliente_de_obra(self, obra_id: int) -> int | None:
        """`obras.cliente_id` de la obra (resuelve el cliente del cargo). None si la obra no existe."""
        return (
            await self._s.execute(
                text("SELECT cliente_id FROM obras WHERE id = :o"), {"o": obra_id}
            )
        ).scalar_one_or_none()

    async def saldo_cliente(self, cliente_id: int) -> Decimal:
        """`clientes.saldo_fiado` (consumo total del cliente en el ledger). 0 si no existe."""
        saldo = (
            await self._s.execute(
                text("SELECT saldo_fiado FROM clientes WHERE id = :c"), {"c": cliente_id}
            )
        ).scalar_one_or_none()
        return Decimal(saldo) if saldo is not None else Decimal("0")

    async def saldo_obra(self, obra_id: int) -> Decimal:
        """Saldo pendiente de la obra: Σ `fiados.saldo` de los fiados enlazados por `cargos_alquiler`."""
        total = (
            await self._s.execute(
                text(
                    "SELECT COALESCE(SUM(f.saldo), 0) FROM cargos_alquiler ca "
                    "JOIN fiados f ON f.id = ca.fiado_id WHERE ca.obra_id = :o"
                ),
                {"o": obra_id},
            )
        ).scalar_one()
        return Decimal(total)

    # --- traza idempotente (cargos_alquiler) ----------------------------------
    async def cargo_por_registro(self, registro_horas_id: int) -> CargoAlquiler | None:
        """Cargo YA asentado para ese `RegistroHorasMaquina` (ancla de idempotencia; UNIQUE en la BD)."""
        return (
            await self._s.execute(
                select(CargoAlquiler).where(CargoAlquiler.registro_horas_id == registro_horas_id)
            )
        ).scalar_one_or_none()

    async def crear_cargo(
        self,
        *,
        registro_horas_id: int,
        fiado_id: int,
        obra_id: int,
        maquina_id: int,
        asignacion_id: int,
        monto: Decimal,
    ) -> CargoAlquiler:
        """Inserta la fila puente `cargos_alquiler`. El UNIQUE(registro_horas_id) es el ancla dura del
        invariante «un registro de horas no genera dos cargos» (defensa en profundidad sobre el lock de
        cliente de `FiadosService.crear`)."""
        cargo = CargoAlquiler(
            registro_horas_id=registro_horas_id, fiado_id=fiado_id, obra_id=obra_id,
            maquina_id=maquina_id, asignacion_id=asignacion_id, monto=monto,
        )
        self._s.add(cargo)
        await self._s.flush()
        return cargo

    async def cargos_de_obra(self, obra_id: int) -> list[dict]:
        """Cargos de alquiler de la obra + saldo vivo de su fiado + nombre de máquina y horas facturables
        (vista de liquidación).

        Resuelve `maquina_nombre` (LEFT JOIN `maquinas`) y `horas_facturables` (LEFT JOIN
        `registros_horas_maquina` por `registro_horas_id`) EN LA MISMA query —sin N+1—; LEFT para no
        perder el cargo si la máquina o el registro fueron borrados."""
        filas = (
            await self._s.execute(
                text(
                    "SELECT ca.id, ca.registro_horas_id, ca.fiado_id, ca.maquina_id, "
                    "       m.nombre AS maquina_nombre, ca.asignacion_id, "
                    "       rh.horas_facturables AS horas_facturables, "
                    "       ca.monto, ca.creado_en, f.saldo AS fiado_saldo "
                    "FROM cargos_alquiler ca "
                    "JOIN fiados f ON f.id = ca.fiado_id "
                    "LEFT JOIN maquinas m ON m.id = ca.maquina_id "
                    "LEFT JOIN registros_horas_maquina rh ON rh.id = ca.registro_horas_id "
                    "WHERE ca.obra_id = :o ORDER BY ca.id"
                ),
                {"o": obra_id},
            )
        ).all()
        return [dict(fila._mapping) for fila in filas]

    async def obra_cabecera(self, obra_id: int) -> dict | None:
        """Nombre de la obra + su cliente_id y nombre (encabezado del detalle de cartera por obra).

        Una sola query (LEFT JOIN a `clientes`); None si la obra no existe. Alimenta `obra_nombre`/
        `cliente_nombre` de `ObraCarteraLeer` (el dashboard cae a "#id" sin ellos)."""
        fila = (
            await self._s.execute(
                text(
                    "SELECT o.cliente_id, o.nombre AS obra_nombre, c.nombre AS cliente_nombre "
                    "FROM obras o LEFT JOIN clientes c ON c.id = o.cliente_id "
                    "WHERE o.id = :o"
                ),
                {"o": obra_id},
            )
        ).first()
        return dict(fila._mapping) if fila is not None else None

    async def abonos_de_obra(self, obra_id: int) -> list[dict]:
        """Abonos del ledger imputables a la obra: movimientos `abono` de los fiados enlazados por los
        cargos de ESTA obra.

        Cada cargo crea su propio fiado (`FiadosService.crear` con key `alquiler:horas:{registro}`), así
        que el abono queda atribuido a la obra —no solo al cliente— por su `fiado_id`. Subconsulta `IN`
        (no JOIN) para no duplicar filas si un fiado tuviera más de un cargo en la misma obra."""
        filas = (
            await self._s.execute(
                text(
                    "SELECT fm.id, fm.monto, fm.creado_en AS fecha "
                    "FROM fiados_movimientos fm "
                    "WHERE fm.tipo = 'abono' "
                    "  AND fm.fiado_id IN (SELECT fiado_id FROM cargos_alquiler WHERE obra_id = :o) "
                    "ORDER BY fm.creado_en, fm.id"
                ),
                {"o": obra_id},
            )
        ).all()
        return [dict(fila._mapping) for fila in filas]

    # --- alertas (SSE interno al dueño, patrón pagar) -------------------------
    async def avisar_cupo_excedido(
        self, *, cliente_id: int, obra_id: int, cupo: Decimal, saldo: Decimal, generado_en: datetime
    ) -> None:
        """Publica el aviso INTERNO al dueño (pg_notify transaccional: viaja al COMMIT junto con el cargo).
        NO bloquea la operación (decisión del dueño, diseño §4.a): el cargo se asienta igual."""
        await publish(self._s, "cartera_cupo_excedido", {
            "cliente_id": cliente_id, "obra_id": obra_id,
            "cupo": str(cupo), "saldo": str(saldo), "excedente": str(saldo - cupo),
            "generado_en": generado_en.isoformat(),
        })

    async def avisar_colita(
        self, *, cliente_id: int, obra_id: int, saldo: Decimal, dias_sin_abono: int, generado_en: datetime
    ) -> None:
        """Publica el aviso INTERNO de colita estancada al dueño (SSE, mismo molde que `pagar_aviso`)."""
        await publish(self._s, "cartera_colita", {
            "cliente_id": cliente_id, "obra_id": obra_id, "saldo": str(saldo),
            "dias_sin_abono": dias_sin_abono, "generado_en": generado_en.isoformat(),
        })

    # --- config (una fila, get-or-create con defaults) ------------------------
    async def obtener_config(self) -> CarteraConfig:
        config = (await self._s.execute(select(CarteraConfig).limit(1))).scalar_one_or_none()
        if config is None:
            config = CarteraConfig()
            self._s.add(config)
            await self._s.flush()
        return config

    async def guardar_config(self, cambios: dict) -> CarteraConfig:
        config = await self.obtener_config()
        for campo, valor in cambios.items():
            setattr(config, campo, valor)
        await self._s.flush()
        return config

    # --- colitas (escaneo del cron y semáforo del dashboard) ------------------
    async def colitas(self, *, corte: datetime) -> list[dict]:
        """Obras FINALIZADA/LIQUIDADA con saldo estancado: Σ `fiados.saldo` > 0 y el último abono a esos
        fiados es anterior a `corte` (o nunca hubo abono). Devuelve una fila por (cliente, obra).

        `ultimo_abono_en` = MAX de abonos de los fiados de la obra; `primer_cargo` ancla `dias_sin_abono`
        cuando el cliente NUNCA abonó (el service lo calcula contra `ahora`)."""
        filas = (
            await self._s.execute(
                text(
                    "SELECT o.cliente_id, ca.obra_id, o.nombre AS obra_nombre, "
                    "       cl.nombre AS cliente_nombre, SUM(f.saldo) AS saldo, "
                    "       MAX(ab.ultimo_abono) AS ultimo_abono_en, MIN(ca.creado_en) AS primer_cargo "
                    "FROM cargos_alquiler ca "
                    "JOIN obras o ON o.id = ca.obra_id "
                    "LEFT JOIN clientes cl ON cl.id = o.cliente_id "
                    "JOIN fiados f ON f.id = ca.fiado_id "
                    "LEFT JOIN LATERAL ("
                    "    SELECT MAX(fm.creado_en) AS ultimo_abono FROM fiados_movimientos fm "
                    "    WHERE fm.fiado_id = ca.fiado_id AND fm.tipo = 'abono'"
                    ") ab ON true "
                    "WHERE o.estado IN ('FINALIZADA', 'LIQUIDADA') "
                    "GROUP BY o.cliente_id, ca.obra_id, o.nombre, cl.nombre "
                    "HAVING SUM(f.saldo) > 0 "
                    "   AND (MAX(ab.ultimo_abono) IS NULL OR MAX(ab.ultimo_abono) < :corte)"
                ),
                {"corte": corte},
            )
        ).all()
        return [dict(fila._mapping) for fila in filas]
