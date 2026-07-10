"""Compositor del calendario de obra (GET /obras/calendario, /obras/calendario/dia — commit 2 del plan
"Calendario de obra PIM").

Clona estructuralmente `modules.obra.dashboard`: recibe la sesión del tenant + sus capacidades, arma los
repositorios de cada módulo y degrada por capacidad. Es una vista de OPERACIÓN (no financiera): agrega la
actividad de la obra por día — horas de máquina, reportes, asistencia, mantenimientos, consumos, hitos —
más lo PLANEADO (asignaciones máquina/trabajador→obra que aún no produjeron actividad). NINGÚN campo de
dinero: los métodos de repositorio del calendario no traen precio/costo y aquí no se inventan.

Una sola verdad de datos: `mes()` y `dia()` recolectan con las MISMAS consultas del repositorio (una por
origen, para todo el rango — N+1-free). `mes()` agrega en Python (Counter/defaultdict) y proyecta los
rangos de asignación a cada día que cubren (intersección rango∩mes); `dia()` corre el mismo recolectado
con `desde=hasta=fecha` (las de rango con solape del día) y lo tipa en secciones. Degradación por
capacidad: sin `maquinaria` → horas/mantenimientos/próximos/planeado_maquinas vacíos; sin `nomina` →
asistencia/planeado_trabajadores vacíos; reportes/consumos/hitos siempre (el router ya gatea `obras`).
"""
from __future__ import annotations

from calendar import monthrange
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from core.config.timezone import today_co
from core.tenancy.catalogo import expandir_metapacks
from modules.maquinaria.repository import SqlMaquinasRepository
from modules.obra.repository import SqlObrasRepository
from modules.obra.schemas import (
    CalendarioMes,
    ConteosDiaCalendario,
    DetalleDiaCalendario,
    DiaCalendario,
    EstadoCalendario,
    EstadoMaquina,
    EstadoTrabajador,
)
from modules.trabajadores.repository import SqlTrabajadoresRepository


@dataclass(slots=True)
class _Secciones:
    """Filas crudas por origen (dicts del repo) para un rango. Punto único que consumen mes y detalle."""

    horas: list[dict] = field(default_factory=list)
    reportes: list[dict] = field(default_factory=list)
    asistencia: list[dict] = field(default_factory=list)
    mantenimientos: list[dict] = field(default_factory=list)
    consumos: list[dict] = field(default_factory=list)
    hitos: list[dict] = field(default_factory=list)
    proximos: list[dict] = field(default_factory=list)
    asign_maquinas: list[dict] = field(default_factory=list)
    asign_trabajadores: list[dict] = field(default_factory=list)


def _rango_mes(anio: int, mes: int) -> tuple[date, date]:
    """(primer día, último día) del mes calendario."""
    return date(anio, mes, 1), date(anio, mes, monthrange(anio, mes)[1])


class CalendarioObraService:
    """Arma el calendario de obra sobre la sesión del tenant + sus capacidades (degrada por capacidad)."""

    def __init__(self, session: AsyncSession, capacidades: frozenset[str]) -> None:
        self._caps = expandir_metapacks(capacidades)
        self._maq = SqlMaquinasRepository(session)
        self._obras = SqlObrasRepository(session)
        self._trab = SqlTrabajadoresRepository(session)

    async def mes(
        self,
        anio: int,
        mes: int,
        *,
        obra_id: int | None = None,
        maquina_id: int | None = None,
        trabajador_id: int | None = None,
    ) -> CalendarioMes:
        """Resumen del mes: conteos por día (solo los días con al menos un conteo > 0)."""
        desde, hasta = _rango_mes(anio, mes)
        sec = await self._recolectar(
            desde, hasta, obra_id=obra_id, maquina_id=maquina_id, trabajador_id=trabajador_id
        )
        return CalendarioMes(anio=anio, mes=mes, dias=_agregar_dias(sec, desde, hasta))

    async def dia(
        self,
        fecha: date,
        *,
        obra_id: int | None = None,
        maquina_id: int | None = None,
        trabajador_id: int | None = None,
    ) -> DetalleDiaCalendario:
        """Detalle de un día: todas las secciones tipadas (las de rango con solape del día)."""
        sec = await self._recolectar(
            fecha, fecha, obra_id=obra_id, maquina_id=maquina_id, trabajador_id=trabajador_id
        )
        return _detalle_dia(fecha, sec)

    async def estado(self, *, hoy: date | None = None) -> EstadoCalendario:
        """Foto del ESTADO ACTUAL de la operación a hoy Colombia (¿dónde está cada máquina/trabajador y con
        qué?). Degrada por capacidad: sin `maquinaria` → maquinas=[]; sin `nomina` → NO se listan las
        asignaciones trabajador→obra, PERO los operadores de máquinas vigentes sí (vienen de maquinaria).

        `maquinas_raw` ya viene ordenada por el repo (con obra primero, luego nombre). La unión de
        trabajadores (asignados a obra ∪ operadores de máquina) se compone en Python sobre los mismos datos
        (sin SQL extra ni N+1): la asignación trabajador→obra manda en obra/desde; la máquina se adjunta
        desde donde el trabajador es operador."""
        hoy = hoy or today_co()
        maquinas_raw = (
            await self._maq.estado_maquinas_hoy(hoy) if "maquinaria" in self._caps else []
        )
        asignados = (
            await self._trab.estado_trabajadores_hoy(hoy) if "nomina" in self._caps else []
        )
        return EstadoCalendario(
            fecha=hoy,
            maquinas=[_a_estado_maquina(m) for m in maquinas_raw],
            trabajadores=_unir_trabajadores(asignados, maquinas_raw),
        )

    async def _recolectar(
        self,
        desde: date,
        hasta: date,
        *,
        obra_id: int | None,
        maquina_id: int | None,
        trabajador_id: int | None,
    ) -> _Secciones:
        """Corre las consultas de cada origen (degradando por capacidad) para el rango [desde, hasta]."""
        sec = _Secciones()
        if "maquinaria" in self._caps:
            sec.horas = await self._maq.horas_calendario(
                desde, hasta, maquina_id=maquina_id, obra_id=obra_id, operador_id=trabajador_id
            )
            sec.mantenimientos = await self._maq.mantenimientos_calendario(
                desde, hasta, maquina_id=maquina_id
            )
            sec.proximos = await self._maq.proximos_mantenimientos_calendario(
                desde, hasta, maquina_id=maquina_id
            )
            sec.asign_maquinas = await self._maq.asignaciones_maquina_calendario(
                desde, hasta, maquina_id=maquina_id, obra_id=obra_id
            )
        if "nomina" in self._caps:
            sec.asistencia = await self._trab.asistencia_calendario(
                desde, hasta, trabajador_id=trabajador_id, obra_id=obra_id
            )
            sec.asign_trabajadores = await self._trab.asignaciones_trabajador_calendario(
                desde, hasta, trabajador_id=trabajador_id, obra_id=obra_id
            )
        sec.reportes = await self._obras.reportes_calendario(desde, hasta, obra_id=obra_id)
        sec.consumos = await self._obras.consumos_calendario(desde, hasta, obra_id=obra_id)
        hitos = await self._obras.hitos_calendario(desde, hasta)
        sec.hitos = [h for h in hitos if obra_id is None or h["obra_id"] == obra_id]
        return sec


# --- Agregación del mes (Python puro sobre las filas del repo) ---------------------------------------
# Cada origen de "actividad real" suma 1 a su conteo del día (por la fecha de la fila). Las asignaciones
# no son actividad: se PROYECTAN a cada día que cubren (intersección con el mes) como conteo de planeado.
_PUNTUALES: tuple[tuple[str, str], ...] = (
    ("horas", "horas_maquina"),
    ("reportes", "reportes"),
    ("asistencia", "asistencias"),
    ("mantenimientos", "mantenimientos"),
    ("consumos", "consumos"),
    ("hitos", "hitos"),
    ("proximos", "proximos_mantenimientos"),
)


def _agregar_dias(sec: _Secciones, desde: date, hasta: date) -> list[DiaCalendario]:
    """Conteos por día + Σ horas de máquina del día. Solo emite los días con al menos un conteo > 0."""
    conteos: dict[str, Counter] = defaultdict(Counter)
    horas_total: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    for h in sec.horas:
        conteos[h["fecha"].isoformat()]["horas_maquina"] += 1
        horas_total[h["fecha"].isoformat()] += h["horas_trabajadas"]
    for attr, clave in _PUNTUALES[1:]:   # horas ya contadas arriba (aporta también horas_total)
        for fila in getattr(sec, attr):
            conteos[fila["fecha"].isoformat()][clave] += 1
    _proyectar(conteos, sec.asign_maquinas, "maquinas_asignadas", desde, hasta)
    _proyectar(conteos, sec.asign_trabajadores, "trabajadores_asignados", desde, hasta)
    dias: list[DiaCalendario] = []
    for iso in sorted(conteos):
        c = conteos[iso]
        if not c:
            continue
        dias.append(
            DiaCalendario(
                fecha=date.fromisoformat(iso),
                horas_maquina_total=horas_total[iso],
                conteos=ConteosDiaCalendario(**dict(c)),
            )
        )
    return dias


def _proyectar(
    conteos: dict[str, Counter], asignaciones: list[dict], clave: str, desde: date, hasta: date
) -> None:
    """Suma 1 a `clave` en cada día del mes que la asignación cubre (intersección rango∩mes)."""
    for a in asignaciones:
        ini = max(a["fecha_inicio"], desde)
        fin = min(a["fecha_fin"] or hasta, hasta)
        dia = ini
        while dia <= fin:
            conteos[dia.isoformat()][clave] += 1
            dia += timedelta(days=1)


# --- Detalle del día (tipa cada sección con su schema; los eventos ignoran la clave extra `fecha`) ----
def _detalle_dia(fecha: date, sec: _Secciones) -> DetalleDiaCalendario:
    """Compone el detalle del día pasando las filas del repo a sus schemas (extra `fecha` se ignora)."""
    return DetalleDiaCalendario(
        fecha=fecha,
        horas_maquina=sec.horas,
        reportes=sec.reportes,
        asistencia=sec.asistencia,
        mantenimientos=sec.mantenimientos,
        consumos=sec.consumos,
        hitos=sec.hitos,
        proximos_mantenimientos=sec.proximos,
        planeado_maquinas=sec.asign_maquinas,
        planeado_trabajadores=sec.asign_trabajadores,
    )


# --- Estado ACTUAL (compone la foto de ahora a partir de las filas de los repos, sin SQL extra) -------
def _a_estado_maquina(m: dict) -> EstadoMaquina:
    """Tipa una fila de `estado_maquinas_hoy` (horas_mes → string decimal: "0" / "6.0000")."""
    return EstadoMaquina(
        maquina_id=m["maquina_id"],
        maquina=m["maquina"],
        estado=m["estado"],
        obra_id=m["obra_id"],
        obra=m["obra"],
        operador_id=m["operador_id"],
        operador=m["operador"],
        desde=m["desde"],
        horas_mes=str(m["horas_mes"]),
    )


def _unir_trabajadores(asignados: list[dict], maquinas_raw: list[dict]) -> list[EstadoTrabajador]:
    """Une trabajadores asignados a obra ∪ operadores de máquina (sin duplicar por trabajador_id).

    La asignación trabajador→obra manda en obra/desde; a quien SOLO es operador se le pone la obra de su
    máquina (desde NULL: no tiene asignación propia). A todos se les adjunta la máquina donde son operador.
    Orden estable por nombre de trabajador, luego id."""
    por_id: dict[int, EstadoTrabajador] = {}
    for a in asignados:
        por_id[a["trabajador_id"]] = EstadoTrabajador(
            trabajador_id=a["trabajador_id"], trabajador=a["trabajador"],
            obra_id=a["obra_id"], obra=a["obra"], desde=a["desde"],
            maquina_id=None, maquina=None,
        )
    for m in maquinas_raw:
        op_id = m["operador_id"]
        if op_id is None:
            continue
        actual = por_id.get(op_id)
        if actual is None:   # solo operador (sin asignación trabajador→obra): obra de la máquina, desde NULL
            por_id[op_id] = EstadoTrabajador(
                trabajador_id=op_id, trabajador=m["operador"],
                obra_id=m["obra_id"], obra=m["obra"], desde=None,
                maquina_id=m["maquina_id"], maquina=m["maquina"],
            )
        elif actual.maquina_id is None:   # asignado a obra Y operador: le adjuntamos su máquina
            actual.maquina_id = m["maquina_id"]
            actual.maquina = m["maquina"]
    return sorted(por_id.values(), key=lambda w: ((w.trabajador or ""), w.trabajador_id))
