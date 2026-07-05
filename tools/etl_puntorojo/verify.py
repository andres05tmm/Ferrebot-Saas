"""Validación de paridad origen↔destino (spec §6) — gate de corte. Exit ≠ 0 si algo no cuadra.

Uso:
    python -m tools.etl_puntorojo.verify --origen-url postgresql://... --slug puntorojo
"""
from __future__ import annotations

import argparse
import sys
from decimal import Decimal

import psycopg
from psycopg.rows import dict_row

from core.config import get_settings
from core.db.urls import tenant_url, to_libpq
from core.logging import configure_logging, get_logger
from tools.etl_puntorojo import transform
from tools.etl_puntorojo.extract import leer_origen
from tools.provision_tenant import _db_name

log = get_logger("etl_puntorojo.verify")

# origen → destino con conteo 1:1 (el resto tiene derivaciones o filtros documentados).
_CONTEOS_DIRECTOS = {
    "usuarios": "usuarios", "productos": "productos", "clientes": "clientes",
    "productos_fracciones": "productos_fracciones", "ventas": "ventas",
    "ventas_detalle": "ventas_detalle", "historico_ventas": "historico_ventas",
    "facturas_electronicas": "facturas_electronicas", "cuentas_cobro": "cuentas_cobro",
    "documentos_soporte": "documentos_soporte", "compras_fiscal": "compras_fiscal",
    "facturas_proveedores": "facturas_proveedores", "facturas_abonos": "facturas_abonos",
    "gastos": "gastos", "bancolombia_transferencias": "bancolombia_transferencias",
}


def verificar(origen_url: str, tenant_url_: str) -> list[str]:
    """Devuelve la lista de fallos de paridad (vacía = verde)."""
    fallos: list[str] = []
    origen = leer_origen(origen_url)
    with psycopg.connect(to_libpq(tenant_url_), row_factory=dict_row) as dst:
        dst.execute("SET default_transaction_read_only = on")

        def _uno(sql: str, params: tuple = ()):
            fila = dst.execute(sql, params).fetchone()
            return next(iter(fila.values())) if fila else None

        # 1. Conteos
        for tabla_o, tabla_d in _CONTEOS_DIRECTOS.items():
            esperado = len(origen[tabla_o])
            real = _uno(f"SELECT count(*) FROM {tabla_d}")
            if real != esperado:
                fallos.append(f"conteo {tabla_d}: origen={esperado} destino={real}")

        # memoria_entidades: el destino tiene UNIQUE(tipo, clave) → se compara deduplicado.
        esperado_memoria = len(transform.dedupe_memoria(origen["memoria_entidades"]))
        real_memoria = _uno("SELECT count(*) FROM memoria_entidades")
        if real_memoria != esperado_memoria:
            fallos.append(f"conteo memoria_entidades: esperado(dedupe)={esperado_memoria} "
                          f"destino={real_memoria}")

        # 2. Sumas de control
        suma_ventas_origen = sum(Decimal(v["total"] or 0) for v in origen["ventas"])
        suma_ventas_destino = _uno("SELECT coalesce(sum(total),0) FROM ventas")
        if suma_ventas_destino != suma_ventas_origen:
            fallos.append(f"Σ ventas.total: origen={suma_ventas_origen} destino={suma_ventas_destino}")

        suma_hist_origen = sum(Decimal(h["ventas"] or 0) for h in origen["historico_ventas"])
        suma_hist_destino = _uno("SELECT coalesce(sum(ventas),0) FROM historico_ventas")
        if suma_hist_destino != suma_hist_origen:
            fallos.append(f"Σ historico_ventas: origen={suma_hist_origen} destino={suma_hist_destino}")

        # 3. Continuidad DIAN: máximo consecutivo legal preservado y CUFEs intactos.
        max_fe_origen = max(
            (transform.split_numero_fe(f["numero"])[1] or 0
             for f in origen["facturas_electronicas"]
             if not f["numero"].upper().startswith("ERR")), default=0)
        max_fe_destino = _uno(
            "SELECT coalesce(max(consecutivo),0) FROM facturas_electronicas "
            "WHERE tipo='factura' AND prefijo <> 'ERR'")
        if max_fe_destino != max_fe_origen:
            fallos.append(f"max consecutivo FE: origen={max_fe_origen} destino={max_fe_destino}")

        cufes_origen = {f["cufe"] for f in origen["facturas_electronicas"] if f.get("cufe")}
        cufes_destino = {r["cufe"] for r in dst.execute(
            "SELECT cufe FROM facturas_electronicas WHERE cufe IS NOT NULL")}
        if cufes_origen != cufes_destino:
            fallos.append(f"CUFEs: faltan={sorted(cufes_origen - cufes_destino)[:3]} "
                          f"sobran={sorted(cufes_destino - cufes_origen)[:3]}")

        siguiente = _uno("SELECT last_value + 1 FROM fe_factura_consecutivo_seq") if max_fe_origen else None
        if max_fe_origen and siguiente != max_fe_origen + 1:
            fallos.append(f"fe_factura_consecutivo_seq: siguiente={siguiente}, esperado={max_fe_origen + 1}")

        # 4. FKs: cero huérfanos
        huerfanos = {
            "ventas_detalle→ventas": "SELECT count(*) FROM ventas_detalle d "
                                     "LEFT JOIN ventas v ON v.id=d.venta_id WHERE v.id IS NULL",
            "fe→ventas": "SELECT count(*) FROM facturas_electronicas f "
                         "LEFT JOIN ventas v ON v.id=f.venta_id "
                         "WHERE f.venta_id IS NOT NULL AND v.id IS NULL",
            "ds→cuentas_cobro": "SELECT count(*) FROM documentos_soporte d "
                                "LEFT JOIN cuentas_cobro c ON c.id=d.cuenta_cobro_id "
                                "WHERE d.cuenta_cobro_id IS NOT NULL AND c.id IS NULL",
            "abonos→facturas_proveedores": "SELECT count(*) FROM facturas_abonos a "
                                           "LEFT JOIN facturas_proveedores f ON f.id=a.factura_id "
                                           "WHERE a.factura_id IS NOT NULL AND f.id IS NULL",
        }
        for nombre, sql in huerfanos.items():
            n = _uno(sql)
            if n:
                fallos.append(f"huérfanos {nombre}: {n}")

        # 5. Fechas (G4/G5): muestra de 5 ventas re-leída en hora Colombia == fecha origen.
        dst.execute("SET TIME ZONE 'America/Bogota'")
        muestra = sorted(origen["ventas"], key=lambda v: v["id"])[-5:]
        for v in muestra:
            d = _uno("SELECT fecha::date FROM ventas WHERE id=%s", (v["id"],))
            if d != v["fecha"]:
                fallos.append(f"fecha venta {v['id']}: origen={v['fecha']} destino(CO)={d}")

        # 6. Consecutivo de venta: renumerado sin duplicados y monótono en el tiempo.
        dup = _uno("SELECT count(*) FROM (SELECT consecutivo FROM ventas "
                   "GROUP BY consecutivo HAVING count(*)>1) d")
        if dup:
            fallos.append(f"consecutivos de venta duplicados: {dup}")
        desorden = _uno(
            "SELECT count(*) FROM (SELECT consecutivo, fecha, "
            "lag(fecha) OVER (ORDER BY consecutivo) AS ant FROM ventas) x WHERE fecha < ant")
        if desorden:
            fallos.append(f"consecutivo de venta no monótono con la fecha: {desorden} saltos")

        # 7. Invariante stock⇔kardex
        sin_kardex = _uno(
            "SELECT count(*) FROM inventario i LEFT JOIN movimientos_inventario m "
            "ON m.producto_id = i.producto_id WHERE i.stock_actual > 0 AND m.id IS NULL")
        if sin_kardex:
            fallos.append(f"productos con stock sin movimiento: {sin_kardex}")

    return fallos


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    parser = argparse.ArgumentParser(description="Paridad origen↔tenant (gate de corte)")
    parser.add_argument("--origen-url", required=True)
    grupo = parser.add_mutually_exclusive_group(required=True)
    grupo.add_argument("--slug")
    grupo.add_argument("--tenant-url")
    args = parser.parse_args(argv)

    url = args.tenant_url or tenant_url(get_settings().tenants_direct_url_base, _db_name(args.slug))
    fallos = verificar(args.origen_url, url)
    if fallos:
        for f in fallos:
            log.error("PARIDAD: %s", f)
        print(f"\nFALLÓ la paridad: {len(fallos)} problema(s). Ver log.")
        return 1
    print("Paridad OK: conteos, sumas, DIAN, FKs, fechas y kardex verificados.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
