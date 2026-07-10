"""Servicio de maquinaria: validación de dominio sobre el repositorio (sin SQL).

Calca `modules/inventario/service.py`: el código de máquina es único (409); la edición es PARCIAL
(solo los campos enviados en el PATCH). El SQL vive en `SqlMaquinasRepository`; aquí solo la lógica.

El WRITE de horas (`registrar_horas`, Fase 3) aplica el MÍNIMO facturable con la función pura
`services.calculations.maquinas.horas_facturables` y deja el SEAM de la cartera de alquiler (Fase 5).
"""
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

from core.config.timezone import today_co
from services.calculations.maquinas import horas_facturables

from modules.maquinaria.errors import (
    AsignacionInexistente,
    AsignacionSolapada,
    CodigoMaquinaDuplicado,
    MantenimientoInexistente,
    MaquinaInexistente,
    ObraNoAsignable,
    OperadorInexistente,
    SinAsignacionActiva,
)
from modules.maquinaria.models import (
    AsignacionMaquinaObra,
    Mantenimiento,
    Maquina,
    RegistroHorasMaquina,
)
from modules.maquinaria.repository import SqlMaquinasRepository
from modules.maquinaria.schemas import (
    AsignacionMaquinaActualizar,
    AsignacionMaquinaCrear,
    MantenimientoActualizar,
    MantenimientoCrear,
    MaquinaActualizar,
    MaquinaCrear,
    RegistroHorasCrear,
)

if TYPE_CHECKING:   # solo para el type hint: no se acopla maquinaria↔cartera en runtime (import perezoso)
    from modules.cartera.service import CarteraAlquilerService


@dataclass(frozen=True, slots=True)
class ResultadoRegistroHoras:
    """Resumen de un parte de horas (lo que ve el caller/bot). `replay=True` = el parte de ese día ya
    existía (idempotencia por clave natural máquina/obra/fecha); no se creó un segundo registro."""

    registro_id: int
    maquina_id: int
    obra_id: int
    fecha: date
    horas_trabajadas: Decimal
    horas_facturables: Decimal
    minimo_cubierto: bool
    precio_hora: Decimal
    ingreso: Decimal
    origen_registro: str
    replay: bool
    turnos: list[dict] = field(default_factory=list)   # franjas de operador del día (rotación)


class MaquinariaService:
    def __init__(
        self, repo: SqlMaquinasRepository, cartera: "CarteraAlquilerService | None" = None
    ) -> None:
        """`cartera` es OPCIONAL: se inyecta SOLO para tenants con la capacidad `cartera_alquiler`
        (lo decide el wiring del router/worker). Sin ella (default `None`) el registro de horas conserva
        el comportamiento actual —ningún cargo a cartera— para tenants sin la capacidad."""
        self._repo = repo
        self._cartera = cartera

    async def listar(self, *, estado: str | None = None, q: str | None = None) -> list[Maquina]:
        return await self._repo.listar(estado=estado, q=q)

    async def obtener(self, maquina_id: int) -> Maquina:
        maquina = await self._repo.obtener(maquina_id)
        if maquina is None:
            raise MaquinaInexistente(maquina_id)
        return maquina

    async def crear(self, datos: MaquinaCrear) -> Maquina:
        """Da de alta la máquina. 409 si el código ya lo usa otra (incluida una eliminada: el UNIQUE
        de la BD no distingue soft delete)."""
        if await self._repo.codigo_existe(datos.codigo):
            raise CodigoMaquinaDuplicado(datos.codigo)
        return await self._repo.crear(datos)

    async def actualizar(self, maquina_id: int, datos: MaquinaActualizar) -> Maquina:
        """Edición parcial: solo los campos presentes en el PATCH. 404 si no existe; 409 si el nuevo
        código lo usa otra máquina."""
        cambios = datos.model_dump(exclude_unset=True)
        codigo = cambios.get("codigo")
        if codigo is not None and await self._repo.codigo_existe(codigo, excluir_id=maquina_id):
            raise CodigoMaquinaDuplicado(codigo)
        maquina = await self._repo.obtener(maquina_id)
        if maquina is None:
            raise MaquinaInexistente(maquina_id)
        return await self._repo.actualizar(maquina, cambios)

    async def eliminar(self, maquina_id: int) -> None:
        """Soft delete (`eliminado_en`). 404 si no existe o ya estaba eliminada."""
        if not await self._repo.soft_delete(maquina_id):
            raise MaquinaInexistente(maquina_id)

    # ---- Lecturas de operación (solo lectura) -------------------------------
    async def listar_asignaciones(self, maquina_id: int) -> list[AsignacionMaquinaObra]:
        return await self._repo.listar_asignaciones(maquina_id)

    # ---- CRUD de asignaciones máquina→obra (Calendario de obra) ---------------------------------
    async def crear_asignacion(
        self, maquina_id: int, datos: AsignacionMaquinaCrear
    ) -> AsignacionMaquinaObra:
        """Asigna la máquina a una obra (Calendario de obra), con precio/mínimo pactados.

        Validaciones: máquina viva (404 MaquinaInexistente); obra existente y no LIQUIDADA (ObraNoAsignable
        `inexistente`/`liquidada`); operador —si viene— trabajador activo (OperadorInexistente); sin solape
        con otra asignación activa de la máquina (AsignacionSolapada). Defaults: `fecha_inicio`=hoy Colombia
        (regla #4); `precio_hora`/`minimo_horas` = los de la máquina si no se envían.

        Transición de estado (SOLO en el alta): si la asignación cubre HOY (fecha_inicio<=hoy<=fecha_fin/
        NULL) y la máquina está `DISPONIBLE`, pasa a `OCUPADA`. NUNCA se toca una máquina en
        MANTENIMIENTO/DAÑADA/BAJA (un mantenimiento o baja manda sobre la asignación)."""
        maquina = await self._repo.obtener(maquina_id)
        if maquina is None:
            raise MaquinaInexistente(maquina_id)

        estado_obra = await self._repo.obra_asignable(datos.obra_id)
        if estado_obra is None:
            raise ObraNoAsignable(datos.obra_id, "inexistente")
        if estado_obra == "LIQUIDADA":
            raise ObraNoAsignable(datos.obra_id, "liquidada")

        if datos.operador_id is not None and not await self._repo.operador_valido(datos.operador_id):
            raise OperadorInexistente(datos.operador_id)

        inicio = datos.fecha_inicio or today_co()
        precio = (
            datos.precio_hora if datos.precio_hora is not None else maquina.precio_hora_default
        )
        minimo = (
            datos.minimo_horas if datos.minimo_horas is not None else maquina.minimo_horas_factura
        )

        if await self._repo.asignacion_solapada(maquina_id, inicio, datos.fecha_fin):
            raise AsignacionSolapada(maquina_id, inicio, datos.fecha_fin)

        asig = await self._repo.crear_asignacion(
            maquina_id=maquina_id,
            obra_id=datos.obra_id,
            fecha_inicio=inicio,
            fecha_fin=datos.fecha_fin,
            precio_hora=precio,
            minimo_horas=minimo,
            operador_id=datos.operador_id,
        )

        hoy = today_co()
        cubre_hoy = inicio <= hoy and (datos.fecha_fin is None or datos.fecha_fin >= hoy)
        if cubre_hoy and maquina.estado == "DISPONIBLE":
            await self._repo.set_estado_maquina(maquina, "OCUPADA")
        return asig

    async def actualizar_asignacion(
        self, maquina_id: int, asignacion_id: int, datos: AsignacionMaquinaActualizar
    ) -> AsignacionMaquinaObra:
        """Edición parcial de una asignación. 404 si no existe para esa máquina; revalida el solape si
        cambia `fecha_fin` (y sigue activa); si viene `operador_id`, valida el trabajador (OperadorInexistente).

        Transición de CIERRE: si tras el parche la asignación queda cerrada (`activa=false` o
        `fecha_fin < hoy`) y la máquina está `OCUPADA` sin OTRA asignación vigente hoy, vuelve a `DISPONIBLE`.
        La reapertura NO re-flipa a OCUPADA (decisión del plan): el alta es la única que activa OCUPADA."""
        asig = await self._repo.obtener_asignacion(maquina_id, asignacion_id)
        if asig is None:
            raise AsignacionInexistente(asignacion_id)

        cambios = datos.model_dump(exclude_unset=True)
        if (
            "operador_id" in cambios
            and cambios["operador_id"] is not None
            and not await self._repo.operador_valido(cambios["operador_id"])
        ):
            raise OperadorInexistente(cambios["operador_id"])

        nueva_fin = cambios.get("fecha_fin", asig.fecha_fin)
        nueva_activa = cambios.get("activa", asig.activa)
        # Revalida el solape si cambia el rango O si se REACTIVA una asignación cerrada: mientras estuvo
        # inactiva pudo crearse otra activa sobre el mismo rango, y reactivar sin chequear rompería el
        # invariante de no-solape.
        reactivada = nueva_activa and not asig.activa
        if (
            nueva_activa
            and ("fecha_fin" in cambios or reactivada)
            and await self._repo.asignacion_solapada(
                maquina_id, asig.fecha_inicio, nueva_fin, excluir_id=asignacion_id
            )
        ):
            raise AsignacionSolapada(maquina_id, asig.fecha_inicio, nueva_fin)

        asig = await self._repo.actualizar_asignacion(asig, cambios)

        hoy = today_co()
        cerrada = (not nueva_activa) or (nueva_fin is not None and nueva_fin < hoy)
        if cerrada:
            maquina = await self._repo.obtener(maquina_id)
            if (
                maquina is not None
                and maquina.estado == "OCUPADA"
                and not await self._repo.otra_asignacion_vigente(
                    maquina_id, hoy, excluir_id=asignacion_id
                )
            ):
                await self._repo.set_estado_maquina(maquina, "DISPONIBLE")
        return asig

    async def listar_horas(
        self, maquina_id: int, *, limite: int = 100, offset: int = 0
    ) -> list[RegistroHorasMaquina]:
        return await self._repo.listar_horas(maquina_id, limite=limite, offset=offset)

    # ---- Mantenimientos (Fase 1 del cockpit): CRUD sobre la máquina --------------------------------
    async def listar_mantenimientos(
        self, maquina_id: int, *, limite: int = 100, offset: int = 0
    ) -> list[Mantenimiento]:
        """Mantenimientos de una máquina (más recientes primero). 404 si la máquina no existe."""
        await self.obtener(maquina_id)   # valida existencia (404 si no)
        return await self._repo.listar_mantenimientos(maquina_id, limite=limite, offset=offset)

    async def crear_mantenimiento(
        self, maquina_id: int, datos: MantenimientoCrear
    ) -> Mantenimiento:
        """Registra un mantenimiento de la máquina. 404 si la máquina no existe.

        La `fecha` por defecto es HOY en hora Colombia (regla #4), resuelta aquí antes de persistir. NO
        cambia `maquina.estado`: pasar la máquina a MANTENIMIENTO/DISPONIBLE es una decisión aparte, por
        `PATCH /maquinas/{id}` (registrar un servicio no la saca de operación por sí solo)."""
        await self.obtener(maquina_id)   # valida existencia (404 si no)
        valores = datos.model_dump()
        valores["fecha"] = datos.fecha or today_co()
        return await self._repo.crear_mantenimiento(maquina_id, valores)

    async def actualizar_mantenimiento(
        self, maquina_id: int, mantenimiento_id: int, datos: MantenimientoActualizar
    ) -> Mantenimiento:
        """Edición parcial de un mantenimiento (solo lo enviado). 404 si no existe para esa máquina."""
        mant = await self._repo.obtener_mantenimiento(maquina_id, mantenimiento_id)
        if mant is None:
            raise MantenimientoInexistente(mantenimiento_id)
        cambios = datos.model_dump(exclude_unset=True)
        return await self._repo.actualizar_mantenimiento(mant, cambios)

    async def eliminar_mantenimiento(self, maquina_id: int, mantenimiento_id: int) -> None:
        """Borra (DELETE duro) un mantenimiento de la máquina. 404 si no existe para esa máquina."""
        mant = await self._repo.obtener_mantenimiento(maquina_id, mantenimiento_id)
        if mant is None:
            raise MantenimientoInexistente(mantenimiento_id)
        await self._repo.eliminar_mantenimiento(mant)

    # ---- Registro de horas (WRITE, Fase 3) ----------------------------------
    async def registrar_horas(
        self, maquina_id: int, datos: RegistroHorasCrear
    ) -> ResultadoRegistroHoras:
        """Registra el parte de horas del día de una máquina en una obra, aplicando el MÍNIMO facturable.

        Resuelve la asignación ACTIVA de (máquina, obra) que cubre `datos.fecha` para tomar el precio y el
        mínimo PACTADOS (pueden diferir del default de la máquina). Las horas a facturar salen de la
        función pura `horas_facturables(horas, mínimo) = max(...)`: si trabajó menos que el mínimo se cobra
        el mínimo (piso de movilización); si trabajó más, lo trabajado.

        IDEMPOTENCIA (invariante del carve-out): la identidad de un parte es la CLAVE NATURAL
        `(maquina, obra, fecha)` —la spec define un parte POR MÁQUINA POR DÍA—. Reintentar el mismo día
        (p. ej. el bot de Fase 6 tras un timeout) devuelve el registro existente con `replay=True` y NO
        inserta un segundo → el cargo a cartera de Fase 5 se asienta una sola vez. `datos.idempotency_key`
        se acepta para el contrato del bot, pero hoy la idempotencia se ancla en la clave natural: no hay
        columna dedicada (models/migraciones son de otro agente). Hardening futuro (cuando se puedan tocar
        esos árboles): columna `idempotency_key` + índice único parcial, espejando el de `modules/fiados`.

        Para SERIALIZAR partes concurrentes del mismo día se bloquea la asignación (`FOR UPDATE`) antes del
        pre-check, igual que el lock-de-ancla de fiados. 404 si la máquina no existe; `SinAsignacionActiva`
        (→409) si no hay asignación activa que cubra la fecha.
        """
        maquina = await self._repo.obtener(maquina_id)
        if maquina is None:
            raise MaquinaInexistente(maquina_id)

        # Lock del ancla (asignación) → sección crítica del pre-check de idempotencia.
        asignacion = await self._repo.asignacion_activa(
            maquina_id, datos.obra_id, datos.fecha, bloquear=True
        )
        if asignacion is None:
            raise SinAsignacionActiva(maquina_id, datos.obra_id, datos.fecha)

        # Rotación de operadores: un parte por día que agrega turnos. `trae_turno` = el payload aporta una
        # franja de operador (operador o hora); sin ella el parte queda sin turnos (comportamiento legacy).
        minimo = Decimal(asignacion.minimo_horas)
        trae_turno = (
            datos.operador_id is not None
            or datos.hora_inicio is not None
            or datos.hora_fin is not None
        )
        existente = await self._repo.registro_del_dia(maquina_id, datos.obra_id, datos.fecha)
        if existente is None:
            return await self._crear_parte(maquina_id, datos, asignacion, minimo, trae_turno)
        return await self._registrar_en_parte(existente, datos, asignacion, minimo, trae_turno)

    async def _crear_parte(
        self,
        maquina_id: int,
        datos: RegistroHorasCrear,
        asignacion: AsignacionMaquinaObra,
        minimo: Decimal,
        trae_turno: bool,
    ) -> ResultadoRegistroHoras:
        """Primer parte del día: crea el registro (facturables = max(horas, mínimo)) y —si el payload trae
        franja— su primer turno. El primer asiento de cartera queda a nivel de registro (turno_id NULL),
        EN LA MISMA TRANSACCIÓN (invariante «nada mueve cartera sin registro»)."""
        facturables = horas_facturables(datos.horas_trabajadas, minimo)
        registro = await self._repo.crear_registro_horas(
            maquina_id=maquina_id,
            obra_id=datos.obra_id,
            fecha=datos.fecha,
            horas_trabajadas=datos.horas_trabajadas,
            horas_facturables=facturables,
            operador_id=datos.operador_id,
            observaciones=datos.observaciones,
            origen_registro=datos.origen_registro,
        )
        if trae_turno:
            await self._repo.crear_turno(
                registro_horas_id=registro.id,
                operador_id=datos.operador_id,
                hora_inicio=datos.hora_inicio,
                hora_fin=datos.hora_fin,
                horas=datos.horas_trabajadas,
            )
        await self._asentar_consumo_cartera(registro, asignacion)
        return await self._resumen(registro, asignacion, replay=False)

    async def _registrar_en_parte(
        self,
        registro: RegistroHorasMaquina,
        datos: RegistroHorasCrear,
        asignacion: AsignacionMaquinaObra,
        minimo: Decimal,
        trae_turno: bool,
    ) -> ResultadoRegistroHoras:
        """Ya hay parte del día. Replay si el payload no trae franja (idempotencia legacy por clave natural)
        o coincide con un turno registrado. Si trae una franja NUEVA, agrega el turno, recalcula el agregado
        del día (Σ turnos, mínimo aplicado UNA vez) y asienta SOLO el delta de facturables a cartera."""
        turnos = (await self._repo.turnos_por_registros([registro.id])).get(registro.id, [])
        if not trae_turno or _turno_coincidente(turnos, datos):
            return await self._resumen(registro, asignacion, replay=True)

        if not turnos:
            # Parte legacy sin turnos: materializa sus horas como turno implícito para que Σ no las pierda
            # al recalcular (backfill perezoso, solo cuando entra la rotación).
            await self._repo.crear_turno(
                registro_horas_id=registro.id,
                operador_id=registro.operador_id,
                hora_inicio=None,
                hora_fin=None,
                horas=registro.horas_trabajadas,
            )
        f_old = registro.horas_facturables
        turno = await self._repo.crear_turno(
            registro_horas_id=registro.id,
            operador_id=datos.operador_id,
            hora_inicio=datos.hora_inicio,
            hora_fin=datos.hora_fin,
            horas=datos.horas_trabajadas,
        )
        turnos = (await self._repo.turnos_por_registros([registro.id])).get(registro.id, [])
        suma = sum((t["horas"] for t in turnos), Decimal("0"))
        f_new = horas_facturables(suma, minimo)
        await self._repo.actualizar_registro_horas(
            registro, horas_trabajadas=suma, horas_facturables=f_new, operador_id=_operador_unico(turnos)
        )
        await self._asentar_delta_turno_cartera(registro, asignacion, turno.id, f_new - f_old)
        return await self._resumen(registro, asignacion, replay=False)

    async def _resumen(
        self,
        registro: RegistroHorasMaquina,
        asignacion: AsignacionMaquinaObra,
        *,
        replay: bool,
    ) -> ResultadoRegistroHoras:
        """Arma el resumen de salida: mínimo cubierto e ingreso = horas_facturables (del DÍA) × precio
        pactado, más los turnos del día (con nombre de operador resuelto) para el front."""
        minimo = Decimal(asignacion.minimo_horas)
        turnos = (await self._repo.turnos_por_registros([registro.id])).get(registro.id, [])
        return ResultadoRegistroHoras(
            registro_id=registro.id,
            maquina_id=registro.maquina_id,
            obra_id=registro.obra_id,
            fecha=registro.fecha,
            horas_trabajadas=registro.horas_trabajadas,
            horas_facturables=registro.horas_facturables,
            minimo_cubierto=registro.horas_trabajadas >= minimo,
            precio_hora=asignacion.precio_hora,
            ingreso=registro.horas_facturables * asignacion.precio_hora,
            origen_registro=registro.origen_registro,
            replay=replay,
            turnos=turnos,
        )

    async def _asentar_consumo_cartera(
        self, registro: RegistroHorasMaquina, asignacion: AsignacionMaquinaObra
    ) -> None:
        """SEAM Fase 5: asienta el consumo de horas en el ledger de fiados (cartera de alquiler), EN LA
        MISMA TRANSACCIÓN que el registro (invariante «nada mueve cartera sin registro de horas»). El
        registro ya tiene `id` por el flush del repositorio.

        Compuertas (diseño §2.1, refinado por Fase 5) — se asienta el cargo SOLO si:
          1. hay `CarteraAlquilerService` inyectado — que ES la señal de que el tenant tiene la capacidad
             `cartera_alquiler` (el wiring del router/worker solo lo inyecta para esos tenants);
          2. la obra resuelve a un cliente (`obra.cliente_id`); y
          3. ese cliente tiene un CUPO de alquiler ACTIVO —sin cupo, las horas no van a la cartera de
             crédito (el alquiler a crédito solo aplica a clientes con cupo otorgado).

        Con las tres, delega en `asentar_consumo_horas` (idempotente por `registro.id`; reusa
        `FiadosService.crear(idempotency_key="alquiler:horas:{registro.id}")`). El cupo dispara la ALERTA
        de excedido dentro del service (SSE al dueño); NUNCA bloquea. Diseño en
        `docs/research/pim-fase5-cartera-diseno.md` §2/§4.
        """
        if self._cartera is None:
            return
        cliente_id = await self._cartera.cliente_de_obra(registro.obra_id)
        if cliente_id is None:
            return
        if await self._cartera.cupo_activo(cliente_id) is None:
            return
        await self._cartera.asentar_consumo_horas(
            registro_horas_id=registro.id,          # ancla de idempotencia (UNIQUE en cargos_alquiler)
            obra_id=registro.obra_id,
            maquina_id=registro.maquina_id,
            asignacion_id=asignacion.id,            # precio/mínimo pactados de la asignación
            cliente_id=cliente_id,                  # resuelto por la obra
            horas_facturables=registro.horas_facturables,
            precio_hora=asignacion.precio_hora,
        )

    async def _asentar_delta_turno_cartera(
        self,
        registro: RegistroHorasMaquina,
        asignacion: AsignacionMaquinaObra,
        turno_id: int,
        delta_horas: Decimal,
    ) -> None:
        """SEAM del delta de rotación: cuando un turno nuevo SUBE las horas facturables del día
        (F_old→F_new), asienta SOLO el delta (F_new−F_old)×precio como cargo ADICIONAL idempotente por
        turno (la rotación NO multiplica el cobro: el mínimo del día ya fue cargado por el primer asiento).

        Mismas compuertas que `_asentar_consumo_cartera` (cartera inyectada + obra con cliente + cupo
        activo). Si el día sigue bajo el mínimo (`delta_horas <= 0`) → no-op (no se asienta nada)."""
        if self._cartera is None or delta_horas <= 0:
            return
        cliente_id = await self._cartera.cliente_de_obra(registro.obra_id)
        if cliente_id is None:
            return
        if await self._cartera.cupo_activo(cliente_id) is None:
            return
        await self._cartera.asentar_delta_turno(
            registro_horas_id=registro.id,
            turno_id=turno_id,                      # ancla de idempotencia (UNIQUE parcial WHERE turno_id)
            obra_id=registro.obra_id,
            maquina_id=registro.maquina_id,
            asignacion_id=asignacion.id,
            cliente_id=cliente_id,
            delta_horas=delta_horas,
            precio_hora=asignacion.precio_hora,
        )

    async def turnos_por_registros(self, registro_ids: list[int]) -> dict[int, list[dict]]:
        """Turnos (con nombre de operador) de varios partes, batcheado (N+1-free). Lo consume el router de
        `GET /maquinas/{id}/horas` para adjuntar `turnos` a cada parte del kárdex."""
        return await self._repo.turnos_por_registros(registro_ids)


def _turno_coincidente(turnos: list[dict], datos: RegistroHorasCrear) -> bool:
    """¿El payload coincide con un turno ya registrado (mismo operador, misma franja de inicio y mismas
    horas)? → REPLAY (no duplica el turno ni su cargo). Base de la idempotencia del reintento del bot."""
    return any(
        t["operador_id"] == datos.operador_id
        and t["hora_inicio"] == datos.hora_inicio
        and t["horas"] == datos.horas_trabajadas
        for t in turnos
    )


def _operador_unico(turnos: list[dict]) -> int | None:
    """Operador de cabecera del parte: el único operador de los turnos, o NULL si hay >1 distinto (o
    ninguno con operador). El front cae a los turnos cuando la cabecera es NULL."""
    distintos = {t["operador_id"] for t in turnos if t["operador_id"] is not None}
    return next(iter(distintos)) if len(distintos) == 1 else None
