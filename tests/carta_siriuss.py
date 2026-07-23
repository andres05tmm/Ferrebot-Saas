"""Siembra de la carta REAL de Siriuss (docs/fixtures/carta-siriuss/carta.yaml) para los E2E.

Lee el fixture (fuente única: la carta extraída bajo el contrato ADR 0011, con las DUDAS D1-D5 ya
resueltas en el ADR 0032) y lo materializa en una base efímera: productos con INC 8% (precio final
al público — D4), grupos de modificadores (Proteína min1/max1, Acompañantes min1/max2 — D1),
zona Bocagrande con recargo POR PLATO, config de cocina 24h (tests independientes de la hora) y
— para el E2E de recetas — insumos con inventario y BOM del plato fuerte.
"""
from decimal import Decimal
from pathlib import Path

import yaml
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_FIXTURE = Path(__file__).parents[1] / "docs" / "fixtures" / "carta-siriuss" / "carta.yaml"


async def sembrar_carta_siriuss(s: AsyncSession) -> dict:
    """Siembra la carta + zonas KDS + insumos/receta. Devuelve ids por nombre y datos útiles."""
    carta = yaml.safe_load(_FIXTURE.read_text(encoding="utf-8"))
    ids: dict = {}

    await s.execute(
        text(
            "INSERT INTO pedido_config (activo, hora_apertura, hora_cierre, minimo_pedido, "
            "tiempo_estimado_min, costo_domicilio_default) VALUES (true, '00:00', '23:59', 0, 40, 3000)"
        )
    )
    # Recargo POR PLATO visto en la carta (politicas_vistas_en_carta.recargo_domicilio_bocagrande).
    recargo = carta["politicas_vistas_en_carta"]["recargo_domicilio_bocagrande"]
    await s.execute(
        text(
            "INSERT INTO zonas_domicilio (nombre, tarifa, recargo_por_item, activo) "
            "VALUES ('Bocagrande', 3000, :r, true)"
        ),
        {"r": recargo},
    )
    # Zonas KDS: platos calientes a 'parrilla', sopas a 'sopas'.
    for zona in ("parrilla", "sopas"):
        ids[f"zona:{zona}"] = (
            await s.execute(
                text("INSERT INTO comanda_zonas (nombre, activo) VALUES (:n, true) RETURNING id"),
                {"n": zona},
            )
        ).scalar_one()

    zona_por_nombre = {
        "Sopa de hueso": ids["zona:sopas"],
        "Plato fuerte del día": ids["zona:parrilla"],
        "Menú especial": ids["zona:parrilla"],
    }
    for item in carta["carta"]:
        pid = (
            await s.execute(
                text(
                    "INSERT INTO productos (nombre, categoria, unidad_medida, precio_venta, iva, "
                    "tipo_impuesto, permite_fraccion, activo, zona_comanda_id) "
                    "VALUES (:n, 'Almuerzos', 'plato', :p, 8, 'inc', false, true, :z) RETURNING id"
                ),
                {"n": item["nombre"], "p": item["precio"], "z": zona_por_nombre.get(item["nombre"])},
            )
        ).scalar_one()
        ids[item["nombre"]] = pid
        for orden, grupo in enumerate(item.get("modificadores") or []):
            gid = (
                await s.execute(
                    text(
                        "INSERT INTO modificador_grupos (producto_id, nombre, min_sel, max_sel, "
                        "obligatorio, orden, activo) VALUES (:p, :n, :mn, :mx, :ob, :o, true) "
                        "RETURNING id"
                    ),
                    {
                        "p": pid, "n": grupo["grupo"], "mn": grupo["min"] or 0,
                        "mx": grupo["max"], "ob": bool(grupo["obligatorio"]), "o": orden,
                    },
                )
            ).scalar_one()
            for opcion in grupo["opciones"]:
                await s.execute(
                    text(
                        "INSERT INTO modificador_opciones (grupo_id, nombre, delta_precio, activo) "
                        "VALUES (:g, :n, :d, true)"
                    ),
                    {"g": gid, "n": opcion["nombre"], "d": opcion["delta_precio"]},
                )

    # Insumos con inventario + BOM del plato fuerte (el E2E de la DoD exige recetas descontando).
    for nombre, stock, costo in (("Arroz (insumo)", "20", "2000"), ("Proteína (insumo)", "10", "9000")):
        iid = (
            await s.execute(
                text(
                    "INSERT INTO productos (nombre, categoria, unidad_medida, precio_venta, "
                    "costo_promedio, iva, permite_fraccion, activo) "
                    "VALUES (:n, 'Insumos', 'kg', 1, :c, 0, true, true) RETURNING id"
                ),
                {"n": nombre, "c": costo},
            )
        ).scalar_one()
        await s.execute(
            text("INSERT INTO inventario (producto_id, stock_actual, stock_minimo) VALUES (:p, :s, 0)"),
            {"p": iid, "s": stock},
        )
        ids[nombre] = iid
    for insumo, cantidad in (("Arroz (insumo)", "0.2"), ("Proteína (insumo)", "0.25")):
        await s.execute(
            text("INSERT INTO recetas (producto_id, insumo_id, cantidad) VALUES (:p, :i, :c)"),
            {"p": ids["Plato fuerte del día"], "i": ids[insumo], "c": cantidad},
        )

    ids["usuario"] = (
        await s.execute(
            text("INSERT INTO usuarios (nombre, rol) VALUES ('Siriuss','vendedor') RETURNING id")
        )
    ).scalar_one()
    await s.commit()
    ids["precio_plato"] = Decimal("19000")
    ids["precio_sopa"] = Decimal("14000")
    return ids
