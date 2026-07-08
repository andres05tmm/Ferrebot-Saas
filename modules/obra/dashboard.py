"""Compositor del cockpit del vertical construcción (GET /obras/dashboard, spec cliente 13).

Inyecta los repositorios de cada módulo (maquinaria, caja, compras, cotización AIU, cartera) y REUSA
`ObrasService.panel()` como sección `portafolio`. NO lleva SQL: cada consulta agregada vive en el
repositorio de su módulo (regla #2); aquí sólo se orquesta y se compone la respuesta. Todo el dinero es
`Decimal` (serializa como string) y las ventanas son de MES CALENDARIO en hora Colombia (regla #4).

Secciones opcionales degradan por capacidad (feature flags): las colitas sólo se cuentan con
`cartera_alquiler`; las cotizaciones por vencer sólo con `cotizaciones_aiu`. Sin esas capacidades el badge
queda en 0 (no se consulta), como pide el diseño del cockpit.
"""
from __future__ import annotations

from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from core.config.timezone import now_co, rango_dia_co, today_co
from core.money import cuantizar
from core.tenancy.catalogo import expandir_metapacks
from modules.caja.repository import SqlCajaRepository
from modules.cartera.repository import SqlCarteraAlquilerRepository
from modules.compras.repository import SqlComprasRepository
from modules.cotizacion_obra.repository import SqlCotizacionObraRepository
from modules.maquinaria.repository import SqlMaquinasRepository
from modules.obra.repository import SqlObrasRepository
from modules.obra.schemas import (
    AlertaDashboard,
    ConteosDashboard,
    DashboardConstruccion,
    KpisMes,
    KpisMesAnterior,
    MaquinaOcupadaHoy,
    MaquinasDashboard,
    MesRango,
    ObraPanel,
    ObraPanelItem,
    TopMaquinaMes,
)
from modules.obra.service import ObrasService, PanelObra

# Umbral del semáforo de utilidad del mes: margen ≥3% verde, 0–3% amarillo, pérdida (<0) rojo.
_UMBRAL_MARGEN_VERDE = Decimal("3")
# Ventanas de la alerta de mantenimiento PRÓXIMO (aún no vencido): ≤7 días o ≥80% del horómetro programado.
_DIAS_PROXIMO_MANT = 7
_FRACCION_HORAS_PROXIMO = Decimal("0.8")
# Cotización "por vencer": su vigencia expira dentro de los próximos 5 días.
_DIAS_COTIZACION_POR_VENCER = 5


def _rango_mes(ref: date) -> tuple[date, date]:
    """(primer día, último día) del mes calendario de `ref`."""
    return ref.replace(day=1), ref.replace(day=monthrange(ref.year, ref.month)[1])


def _rango_mes_anterior(ref: date) -> tuple[date, date]:
    """(primer día, último día) del mes calendario ANTERIOR al de `ref`."""
    fin_prev = ref.replace(day=1) - timedelta(days=1)
    return _rango_mes(fin_prev)


def panel_obra_a_schema(p: PanelObra) -> ObraPanel:
    """Mapea el `PanelObra` del service (dataclass) al schema de salida (Pydantic). Compartido por el
    endpoint `/obras/panel` y por el portafolio del cockpit (una sola verdad de mapeo)."""
    return ObraPanel(
        generado_en=p.generado_en,
        total_obras=p.total_obras,
        obras_activas=p.obras_activas,
        por_estado=p.por_estado,
        ingreso_presupuestado_total=p.ingreso_presupuestado_total,
        gasto_total=p.gasto_total,
        utilidad_real_total=p.utilidad_real_total,
        obras_en_alerta=p.obras_en_alerta,
        obras=[
            ObraPanelItem(
                obra_id=it.obra_id, nombre=it.nombre, estado=it.estado, cliente_id=it.cliente_id,
                cliente_nombre=it.cliente_nombre,
                ingreso_presupuestado=it.ingreso_presupuestado, gasto_total=it.gasto_total,
                utilidad_real=it.utilidad_real, tiene_presupuesto=it.tiene_presupuesto,
                semaforo=it.semaforo, alerta_margen=it.alerta_margen,
            )
            for it in p.obras
        ],
    )


def _armar_kpis(
    *,
    ingreso_alquiler: Decimal,
    resbalos: Decimal,
    gastos: Decimal,
    compras: Decimal,
    mes_ant_ingreso: Decimal,
    mes_ant_gasto: Decimal,
) -> KpisMes:
    """Compone los KPIs del mes (PURA): totales, utilidad, margen y semáforo. Redondeo money-safe al final."""
    ingreso_total = ingreso_alquiler + resbalos
    gasto_total = gastos + compras
    utilidad = ingreso_total - gasto_total
    margen = cuantizar(utilidad / ingreso_total * 100) if ingreso_total > 0 else Decimal("0")
    if utilidad < 0:
        semaforo = "rojo"
    elif margen < _UMBRAL_MARGEN_VERDE:
        semaforo = "amarillo"
    else:
        semaforo = "verde"
    return KpisMes(
        ingreso_alquiler=cuantizar(ingreso_alquiler),
        resbalos=cuantizar(resbalos),
        ingreso_total=cuantizar(ingreso_total),
        gastos=cuantizar(gastos),
        compras=cuantizar(compras),
        gasto_total=cuantizar(gasto_total),
        utilidad_estimada=cuantizar(utilidad),
        margen_pct=margen,
        semaforo_utilidad=semaforo,
        flujo_caja_neto=cuantizar(utilidad),   # v1: iguala la utilidad estimada (documentado)
        mes_anterior=KpisMesAnterior(
            ingreso_total=cuantizar(mes_ant_ingreso), gasto_total=cuantizar(mes_ant_gasto)
        ),
    )


class DashboardConstruccionService:
    """Arma la respuesta agregada del cockpit sobre la sesión del tenant + sus capacidades."""

    def __init__(self, session: AsyncSession, capacidades: frozenset[str]) -> None:
        self._caps = expandir_metapacks(capacidades)
        self._obras = ObrasService(SqlObrasRepository(session))
        self._obras_repo = SqlObrasRepository(session)
        self._maq = SqlMaquinasRepository(session)
        self._compras = SqlComprasRepository(session)
        self._caja = SqlCajaRepository(session)
        self._coti = SqlCotizacionObraRepository(session)
        self._cartera = SqlCarteraAlquilerRepository(session)

    async def construir(self) -> DashboardConstruccion:
        """Compone el cockpit en un solo request agregado (N+1-free; cada sección son consultas batcheadas)."""
        hoy = today_co()
        desde, hasta = _rango_mes(hoy)

        kpis = await self._kpis(desde, hasta)
        portafolio_dc = await self._obras.panel()
        maquinas_vivas = await self._maq.listar()
        maquinas = await self._maquinas(maquinas_vivas, desde, hasta, hoy)
        alertas = await self._alertas(maquinas_vivas, hoy, portafolio_dc)
        conteos = await self._conteos(hoy)
        return DashboardConstruccion(
            generado_en=now_co(),
            mes=MesRango(desde=desde, hasta=hasta),
            kpis_mes=kpis,
            portafolio=panel_obra_a_schema(portafolio_dc),
            maquinas=maquinas,
            alertas=alertas,
            conteos=conteos,
        )

    async def _kpis(self, desde: date, hasta: date) -> KpisMes:
        """KPIs del mes en curso + comparativo del mes anterior (mismas consultas sobre la ventana previa)."""
        ingreso_alquiler = await self._maq.ingreso_alquiler_mes(desde=desde, hasta=hasta)
        ini, fin = rango_dia_co(desde, hasta)
        resbalos, compras = await self._compras.agregados_mes(inicio=ini, fin=fin)
        gastos = await self._caja.suma_gastos(inicio=ini, fin=fin)

        pdesde, phasta = _rango_mes_anterior(desde)
        p_ingreso = await self._maq.ingreso_alquiler_mes(desde=pdesde, hasta=phasta)
        pini, pfin = rango_dia_co(pdesde, phasta)
        p_resbalos, p_compras = await self._compras.agregados_mes(inicio=pini, fin=pfin)
        p_gastos = await self._caja.suma_gastos(inicio=pini, fin=pfin)

        return _armar_kpis(
            ingreso_alquiler=ingreso_alquiler, resbalos=resbalos, gastos=gastos, compras=compras,
            mes_ant_ingreso=p_ingreso + p_resbalos, mes_ant_gasto=p_gastos + p_compras,
        )

    async def _maquinas(
        self, maquinas_vivas: list, desde: date, hasta: date, hoy: date
    ) -> MaquinasDashboard:
        """Tablero de máquinas: total + conteo por estado (de las vivas) + ocupadas hoy + top del mes."""
        por_estado: dict[str, int] = {}
        for m in maquinas_vivas:
            por_estado[m.estado] = por_estado.get(m.estado, 0) + 1
        ocupadas = await self._maq.ocupadas_hoy(hoy=hoy)
        top = await self._maq.top_maquinas_mes(desde=desde, hasta=hasta, limite=5)
        return MaquinasDashboard(
            total=len(maquinas_vivas),
            por_estado=por_estado,
            ocupadas_hoy=[
                MaquinaOcupadaHoy(
                    maquina_id=r["maquina_id"], maquina=r["maquina"], obra_nombre=r["obra_nombre"],
                    operador_nombre=r["operador_nombre"],
                    horas_hoy=Decimal(r["horas_hoy"]), ingreso_hoy=cuantizar(Decimal(r["ingreso_hoy"])),
                )
                for r in ocupadas
            ],
            top_mes=[
                TopMaquinaMes(
                    maquina_id=r["maquina_id"], maquina=r["maquina"],
                    horas=Decimal(r["horas"]), ingreso=cuantizar(Decimal(r["ingreso"])),
                )
                for r in top
            ],
        )

    async def _alertas(
        self, maquinas_vivas: list, hoy: date, portafolio: PanelObra
    ) -> list[AlertaDashboard]:
        """Alertas accionables: mantenimiento (vencido/próximo, con los agregados de F1) + obra (derivadas
        del propio panel, sin query extra). Ordenadas rojo→amarillo."""
        alertas = await self._alertas_mantenimiento(maquinas_vivas, hoy)
        alertas.extend(_alertas_obra(portafolio))
        alertas.sort(key=lambda a: 0 if a.severidad == "rojo" else 1)
        return alertas

    async def _alertas_mantenimiento(
        self, maquinas_vivas: list, hoy: date
    ) -> list[AlertaDashboard]:
        """Deriva las alertas de mantenimiento del último servicio de cada máquina + horas acumuladas desde
        entonces (vencido: fecha pasada u horómetro ≥ programado; próximo: ≤7 días o ≥80% del horómetro)."""
        maq_por_id = {m.id: m for m in maquinas_vivas}
        ultimos = await self._maq.ultimo_mantenimiento_por_maquina()
        pares = [(mid, mant.fecha) for mid, mant in ultimos.items() if mid in maq_por_id]
        horas_acum = await self._maq.horas_desde(pares)
        alertas: list[AlertaDashboard] = []
        limite_proximo = hoy + timedelta(days=_DIAS_PROXIMO_MANT)
        for mid, mant in ultimos.items():
            maq = maq_por_id.get(mid)
            if maq is None:   # máquina eliminada: su mantenimiento no alerta
                continue
            acum = horas_acum.get(mid, Decimal("0"))
            por_fecha_venc = mant.proximo_en_fecha is not None and mant.proximo_en_fecha < hoy
            por_horas = mant.proximo_en_horas is not None and mant.proximo_en_horas > 0
            por_horas_venc = por_horas and acum >= mant.proximo_en_horas
            if por_fecha_venc or por_horas_venc:
                alertas.append(
                    AlertaDashboard(
                        tipo="mantenimiento_vencido", severidad="rojo",
                        titulo=f"Mantenimiento vencido: {maq.nombre}",
                        detalle=_detalle_mant(mant, acum, hoy),
                        ref_id=maq.id, ruta="/maquinas",
                    )
                )
                continue
            por_fecha_prox = (
                mant.proximo_en_fecha is not None and mant.proximo_en_fecha <= limite_proximo
            )
            por_horas_prox = por_horas and acum >= mant.proximo_en_horas * _FRACCION_HORAS_PROXIMO
            if por_fecha_prox or por_horas_prox:
                alertas.append(
                    AlertaDashboard(
                        tipo="mantenimiento_proximo", severidad="amarillo",
                        titulo=f"Mantenimiento próximo: {maq.nombre}",
                        detalle=_detalle_mant(mant, acum, hoy),
                        ref_id=maq.id, ruta="/maquinas",
                    )
                )
        return alertas

    async def _conteos(self, hoy: date) -> ConteosDashboard:
        """Badges: gastos por revisar (siempre), colitas y cotizaciones por vencer (degradan por capacidad)."""
        gastos_por_revisar = await self._caja.contar_gastos_por_revisar()
        colitas = 0
        if "cartera_alquiler" in self._caps:
            colitas = len(await self._cartera.colitas(corte=now_co()))
        cotizaciones_por_vencer = 0
        if "cotizaciones_aiu" in self._caps:
            cotizaciones_por_vencer = await self._coti.contar_por_vencer(
                hoy=hoy, limite=hoy + timedelta(days=_DIAS_COTIZACION_POR_VENCER)
            )
        return ConteosDashboard(
            gastos_por_revisar=gastos_por_revisar,
            colitas=colitas,
            cotizaciones_por_vencer=cotizaciones_por_vencer,
        )


def _detalle_mant(mant, acum: Decimal, hoy: date) -> str:
    """Frase corta del porqué de la alerta (fecha programada u horómetro acumulado vs. programado)."""
    partes: list[str] = []
    if mant.proximo_en_fecha is not None:
        partes.append(f"programado para {mant.proximo_en_fecha.isoformat()}")
    if mant.proximo_en_horas is not None and mant.proximo_en_horas > 0:
        partes.append(f"{acum} h de {mant.proximo_en_horas} h programadas")
    return "; ".join(partes) if partes else "sin programación de próximo servicio"


def _alertas_obra(portafolio: PanelObra) -> list[AlertaDashboard]:
    """Alertas de obra derivadas del panel (sin query extra): pérdida (semáforo rojo) o margen en riesgo."""
    alertas: list[AlertaDashboard] = []
    for it in portafolio.obras:
        if it.semaforo == "rojo":
            alertas.append(
                AlertaDashboard(
                    tipo="obra_perdida", severidad="rojo",
                    titulo=f"Obra en pérdida: {it.nombre}",
                    detalle=f"Gasto {it.gasto_total} vs. presupuesto {it.ingreso_presupuestado}",
                    ref_id=it.obra_id, ruta="/obras",
                )
            )
        elif it.alerta_margen:
            alertas.append(
                AlertaDashboard(
                    tipo="obra_margen", severidad="amarillo",
                    titulo=f"Margen en riesgo: {it.nombre}",
                    detalle=f"Utilidad real {it.utilidad_real} por debajo del 50% de lo presupuestado",
                    ref_id=it.obra_id, ruta="/obras",
                )
            )
    return alertas
