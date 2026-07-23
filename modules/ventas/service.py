"""Servicio de ventas: lógica de dominio pura y testeable (sin SQL directo).

Depende del protocolo `VentasRepo`; los tests unitarios inyectan un repo falso. Calcula
totales con IVA INCLUIDO en el precio (estándar retail Colombia): el precio del catálogo es
la fuente de verdad (ferrebot-logica-portar.md §1). El consecutivo sale de una SEQUENCE.
"""
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Protocol

from core.config.timezone import rango_dia_co
from core.money import cuantizar as _money, descomponer_iva
from modules.inventario.precios import (
    EsquemaPrecio,
    FraccionPrecio,
    obtener_precio_para_cantidad,
)
from modules.fiados.service import FiadosService
from modules.ventas.errors import (
    IdempotenciaConflicto,
    LineaInvalida,
    OperacionNoAutorizada,
    ProductoNoEncontrado,
    StockInsuficiente,
    VentaConDevolucion,
    VentaConFacturaViva,
    VentaNoEncontrada,
    VentaNoEsDeHoy,
)
from modules.ventas.schemas import PagoParte, VentaConLineas, VentaCrear, VentaLeer


def es_de_hoy_co(fecha: datetime) -> bool:
    """¿El instante `fecha` cae dentro del día de HOY en hora Colombia? (función pura)."""
    inicio, fin = rango_dia_co()
    return inicio <= fecha <= fin


@dataclass(frozen=True, slots=True)
class ProductoPrecio:
    id: int
    nombre: str
    precio_venta: Decimal
    iva: int
    activo: bool
    # Costo de compra AL MOMENTO de vender: se hila hasta el movimiento SALIDA (costo de ventas exacto).
    precio_compra: Decimal | None = None
    # Costo promedio ponderado móvil (ADR 0025): fuente PREFERIDA del snapshot de costo en la SALIDA;
    # cae a `precio_compra` si aún es NULL (producto sin compras registradas tras la migración 0028).
    costo_promedio: Decimal | None = None
    precio_umbral: Decimal | None = None
    precio_bajo_umbral: Decimal | None = None
    precio_sobre_umbral: Decimal | None = None
    fracciones: tuple[FraccionPrecio, ...] = field(default_factory=tuple)
    # Unidad de venta del catálogo: "Unidad" (default) o sub-unidad de granel ("GRM"/"Cms") que el
    # motor de precios usa para cobrar por gramo/cm (puntillas, lija esmeril). Ver precios.py.
    unidad_medida: str = "Unidad"
    # Tipo del impuesto de la tarifa `iva` (ADR 0032 D2): 'iva' (0/5/19) o 'inc' (impoconsumo 8%).
    tipo_impuesto: str = "iva"

    def esquema(self) -> EsquemaPrecio:
        """Arma el esquema que consume el motor de precios (modules.inventario)."""
        return EsquemaPrecio(
            precio_venta=self.precio_venta,
            precio_umbral=self.precio_umbral,
            precio_bajo_umbral=self.precio_bajo_umbral,
            precio_sobre_umbral=self.precio_sobre_umbral,
            fracciones=self.fracciones,
            unidad_medida=self.unidad_medida,
        )


@dataclass(frozen=True, slots=True)
class FraccionBusqueda:
    """Fracción disponible de un producto para la consulta: etiqueta de texto + precio total.

    A diferencia de `FraccionPrecio` (decimal + precio_total, para el motor de precios), aquí importa
    la ETIQUETA tal como está en `productos_fracciones.fraccion` (p. ej. "1/2") para mostrarla al usuario.
    """

    etiqueta: str
    precio_total: Decimal


@dataclass(frozen=True, slots=True)
class ProductoBusqueda:
    """Coincidencia de búsqueda de catálogo para una consulta de SOLO LECTURA (consultar_producto).

    No se reusa `ProductoPrecio`: ese modela el esquema de PRECIOS (umbral/fracciones/iva) y NO lleva
    stock. La consulta necesita precio base, stock, la unidad de empaque y las fracciones (con su
    etiqueta) para que el modelo responda cualquier fracción desde una sola consulta.
    """

    id: int
    nombre: str
    precio: Decimal
    stock: Decimal
    unidad_medida: str = "Unidad"
    fracciones: tuple[FraccionBusqueda, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class LineaResuelta:
    producto_id: int | None
    descripcion: str | None
    cantidad: Decimal
    precio_unitario: Decimal
    iva: int
    total_linea: Decimal
    descontar_stock: bool
    # Costo del producto al vender (None en varia: no hay mercancía → sin movimiento ni costo).
    costo_unitario: Decimal | None = None
    # Snapshot del TIPO del impuesto (ADR 0032 D2): 'iva' | 'inc'. La tarifa vive en `iva`.
    tipo_impuesto: str = "iva"


@dataclass(frozen=True, slots=True)
class VentaHeader:
    consecutivo: int
    cliente_id: int | None
    vendedor_id: int
    subtotal: Decimal
    impuestos: Decimal
    total: Decimal
    metodo_pago: str
    origen: str
    idempotency_key: str | None
    lineas: list[LineaResuelta] = field(default_factory=list)
    # Partes del cobro de una venta MIXTA (F5/0053); vacío en las ventas normales.
    pagos: list[PagoParte] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class EdicionVenta:
    """Cabecera + líneas resueltas de una edición en el lugar (mantiene id/consecutivo/fecha)."""

    cliente_id: int | None
    metodo_pago: str
    subtotal: Decimal
    impuestos: Decimal
    total: Decimal
    lineas: list[LineaResuelta] = field(default_factory=list)


class RetencionesAplicador(Protocol):
    """Puerto del motor de retenciones (lo cumple RetencionesService; los tests lo falsean).

    Estructural: se inyecta OPCIONAL en la venta para calcular/persistir sus retenciones inline (ADR
    0027). `commit=False` mantiene los renglones en la MISMA transacción de la venta (atómico, como el
    cargo de fiado): si la venta se revierte, sus retenciones también.
    """

    async def aplicar_a_venta(self, venta_id: int, *, commit: bool = ...) -> object | None: ...


class VentasRepo(Protocol):
    """Puerto de datos de ventas (lo implementa SqlVentasRepository; los tests lo falsean)."""

    async def buscar_por_idempotency(self, key: str) -> VentaLeer | None: ...
    async def obtener_producto(self, producto_id: int) -> ProductoPrecio | None: ...
    async def lock_inventario(self, producto_id: int) -> Decimal | None: ...
    async def obtener_producto_busqueda(self, producto_id: int) -> "ProductoBusqueda | None": ...
    async def buscar_productos_por_nombre(self, texto: str) -> list[tuple[int, str]]: ...
    async def registrar_alias(
        self, termino: str, reemplazo: str, *, producto_id: int | None = ...
    ) -> bool: ...
    async def listar(
        self, *, desde: date | None = None, hasta: date | None = None, vendedor_id: int | None = None
    ) -> list[VentaLeer]: ...
    async def siguiente_consecutivo(self) -> int: ...
    async def crear_venta(self, header: VentaHeader) -> VentaLeer: ...
    async def pagos_de_venta(self, venta_id: int) -> list[tuple[str, Decimal]]: ...
    async def reemplazar_pagos(self, venta_id: int, pagos: list[PagoParte]) -> None: ...
    async def obtener_cabecera(self, venta_id: int) -> VentaLeer | None: ...
    async def obtener(self, venta_id: int) -> VentaConLineas | None: ...
    async def tiene_factura_viva(self, venta_id: int) -> bool: ...
    async def tiene_devolucion(self, venta_id: int) -> bool: ...
    async def borrar_venta(self, venta_id: int) -> None: ...
    async def revertir_lineas(self, venta_id: int) -> None: ...
    async def aplicar_edicion(self, venta_id: int, edicion: EdicionVenta) -> VentaConLineas | None: ...


@dataclass(frozen=True, slots=True)
class ResultadoVenta:
    venta: VentaLeer
    replay: bool  # True si se devolvió una venta ya existente (idempotencia)


def calcular_totales(lineas: list[LineaResuelta]) -> tuple[Decimal, Decimal, Decimal]:
    """(subtotal, impuestos, total) con IVA incluido en cada total de línea.

    La descomposición base/IVA es LA MISMA de la factura electrónica (`core.money.descomponer_iva`,
    redondeo base-primero): la venta y su documento fiscal no pueden diferir ni un centavo.
    """
    subtotal = impuestos = total = Decimal("0")
    for ln in lineas:
        base, impuesto = descomponer_iva(ln.total_linea, ln.iva)
        subtotal += base
        impuestos += impuesto
        total += ln.total_linea
    return _money(subtotal), _money(impuestos), _money(total)


def _firma_lineas(lineas) -> list[tuple]:
    """Firma comparable de las líneas para el guard de idempotencia: catálogo por (producto, cantidad)
    —el precio NO entra: lo resuelve el catálogo y puede derivar—; varia por (descripción, cantidad,
    precio). Orden-insensible."""
    firma: list[tuple] = []
    for ln in lineas:
        if ln.producto_id is not None:
            firma.append(("cat", ln.producto_id, str(ln.cantidad.normalize())))
        else:
            precio = _money(ln.precio_unitario) if ln.precio_unitario is not None else None
            firma.append(("varia", (ln.descripcion or "").strip().lower(),
                          str(ln.cantidad.normalize()), str(precio)))
    return sorted(firma)


class VentaService:
    def __init__(
        self, repo: VentasRepo, *, fiados: FiadosService | None = None,
        retenciones: RetencionesAplicador | None = None,
    ) -> None:
        self._repo = repo
        self._fiados = fiados
        # Motor de retenciones inline (opt-in, ADR 0027): solo se inyecta si el tenant tiene la feature
        # `retenciones`; None = no se calculan retenciones al vender (tenant sin la capacidad).
        self._retenciones = retenciones

    async def registrar_venta(
        self, datos: VentaCrear, vendedor_id: int, *, control_stock_estricto: bool = False
    ) -> ResultadoVenta:
        """Registra la venta. `control_stock_estricto` (opt-in por empresa) bloquea con
        StockInsuficiente cuando el stock no alcanza; el default PERMISIVO (False) deja pasar la venta y
        el stock baja (puede quedar negativo: negocios informales que no llevan inventario estricto).

        Una venta FIADA exige `cliente_id` y crea su cargo en el ledger de fiados EN LA MISMA
        transacción (key derivada `venta-fiado:{id}`): sin eso la deuda quedaría invisible para
        cobranza y saldo del cliente."""
        if datos.metodo_pago == "fiado":
            if datos.cliente_id is None:
                raise LineaInvalida("Una venta fiada requiere cliente_id (¿a nombre de quién queda la deuda?)")
            if self._fiados is None:
                raise LineaInvalida("Este canal no soporta ventas fiadas")
        if datos.idempotency_key:
            existente = await self._repo.buscar_por_idempotency(datos.idempotency_key)
            if existente is not None:
                if not await self._mismo_payload(existente, datos):
                    raise IdempotenciaConflicto(datos.idempotency_key)
                return ResultadoVenta(venta=existente, replay=True)

        lineas = [await self._resolver_linea(ln, control_stock_estricto) for ln in datos.lineas]
        subtotal, impuestos, total = calcular_totales(lineas)
        if datos.metodo_pago == "mixto":
            # Invariante de dinero (F5/0053): las partes del cobro deben sumar EXACTO el total
            # calculado por el catálogo — el POS no puede cobrar de menos ni inventar dinero.
            suma_pagos = _money(sum((p.monto for p in datos.pagos), Decimal("0")))
            if suma_pagos != total:
                raise LineaInvalida(
                    f"Las partes del pago mixto suman {suma_pagos} pero la venta vale {total}"
                )
        consecutivo = await self._repo.siguiente_consecutivo()
        header = VentaHeader(
            consecutivo=consecutivo,
            cliente_id=datos.cliente_id,
            vendedor_id=vendedor_id,
            subtotal=subtotal,
            impuestos=impuestos,
            total=total,
            metodo_pago=datos.metodo_pago,
            origen=datos.origen,
            idempotency_key=datos.idempotency_key,
            lineas=lineas,
            pagos=datos.pagos,
        )
        venta = await self._repo.crear_venta(header)
        if datos.metodo_pago == "fiado" and self._fiados is not None:
            # Misma transacción/sesión: si el cargo falla (p. ej. cliente inexistente), la venta
            # también se revierte — nunca queda una venta fiada sin deuda en el ledger.
            await self._fiados.crear(
                cliente_id=datos.cliente_id, venta_id=venta.id, monto=venta.total,
                idempotency_key=f"venta-fiado:{venta.id}",
            )
        if self._retenciones is not None:
            # Retenciones inline (ADR 0027): calcula y persiste los renglones tributarios en la MISMA
            # transacción (commit=False) — atómico con la venta, igual que el cargo de fiado. El motor
            # JAMÁS muta el total de la venta; sin config activa no crea renglones (opt-in real).
            await self._retenciones.aplicar_a_venta(venta.id, commit=False)
        return ResultadoVenta(venta=venta, replay=False)

    async def _mismo_payload(self, existente: VentaLeer, datos: VentaCrear) -> bool:
        """¿El payload entrante coincide con la venta ya registrada bajo la misma idempotency_key?

        Compara método de pago, cliente y la firma de líneas (`_firma_lineas`). Reusar una key con
        un payload distinto es un bug del caller → IdempotenciaConflicto (409), como en compras.
        """
        if datos.metodo_pago != existente.metodo_pago or datos.cliente_id != existente.cliente_id:
            return False
        if datos.metodo_pago == "mixto":
            # El desglose del cobro también es parte del payload: la misma key con otras partes
            # (p. ej. menos efectivo y más transferencia) es un bug del caller, no un replay.
            persistidos = sorted(await self._repo.pagos_de_venta(existente.id))
            entrantes = sorted((p.metodo, _money(p.monto)) for p in datos.pagos)
            if entrantes != persistidos:
                return False
        detalle = await self._repo.obtener(existente.id)
        if detalle is None:
            return True   # carrera improbable (venta borrada): el replay de cabecera basta
        return _firma_lineas(datos.lineas) == _firma_lineas(detalle.lineas)

    async def obtener_venta(self, venta_id: int) -> VentaLeer | None:
        """Cabecera de una venta por id (solo lectura; la usa el replay del cobro de cita, ADR 0022)."""
        return await self._repo.obtener_cabecera(venta_id)

    # --- Lecturas para el agente IA (solo lectura; no mutan) -------------------
    async def listar_dia(self, *, vendedor_id: int | None) -> list[VentaLeer]:
        """Ventas de HOY (Colombia). `vendedor_id` acota a un vendedor; None = todas. Solo lectura.

        El rango por defecto (hoy) lo resuelve el repo; el scope RBAC (vendedor → su id, admin → None)
        lo decide el handler de la herramienta, no el servicio.
        """
        return await self._repo.listar(desde=None, hasta=None, vendedor_id=vendedor_id)

    async def buscar_producto_por_nombre(self, texto: str) -> list[ProductoBusqueda]:
        """Coincidencias de catálogo por nombre, enriquecidas (precio, stock, unidad, fracciones).

        Reusa la resolución nombre→producto del catálogo (la misma del resto del sistema) vía el repo;
        cada candidato se surte con `obtener_producto_busqueda` (precio base + stock + unidad de empaque
        + fracciones con etiqueta). El conteo está acotado por el límite del buscador, no es ilimitado.
        """
        candidatos = await self._repo.buscar_productos_por_nombre(texto)
        productos: list[ProductoBusqueda] = []
        for prod_id, _ in candidatos:
            prod = await self._repo.obtener_producto_busqueda(prod_id)
            if prod is not None:
                productos.append(prod)
        return productos

    async def registrar_alias(self, termino: str, reemplazo: str) -> bool:
        """Aprende un alias de búsqueda (variante/typo → término canónico) para el catálogo del tenant.

        Alias GLOBAL (producto_id NULL): la búsqueda reescribe `termino`→`reemplazo` y luego resuelve
        el producto por su vía normal (exacta/trigram/fuzzy). No liga a un producto_id para no fijar
        una resolución equivocada; el reemplazo es el término canónico que el catálogo sí conoce.
        Sin commit: la sesión del turno commitea al cierre (igual que registrar_venta). True si es nuevo.
        """
        return await self._repo.registrar_alias(
            termino.strip().lower(), reemplazo.strip(), producto_id=None
        )

    async def _guard_modificacion(
        self, venta_id: int, *, user_id: int, es_admin: bool, accion: str
    ) -> VentaLeer:
        """Guards compartidos de borrar/editar una venta; devuelve la cabecera si todos pasan.

        En orden: existe (VentaNoEncontrada/404) → es de HOY Colombia (VentaNoEsDeHoy/409) → permiso,
        admin o vendedor dueño (OperacionNoAutorizada/403) → sin factura electrónica viva
        (VentaConFacturaViva/409) → sin devolución registrada (VentaConDevolucion/409, ADR 0026: la
        devolución ya movió stock y dinero; borrar/reescribir la venta dejaría eso colgando).
        `accion` ("borrar"/"editar") solo ajusta el mensaje del error.
        """
        venta = await self._repo.obtener_cabecera(venta_id)
        if venta is None:
            raise VentaNoEncontrada(venta_id)
        if not es_de_hoy_co(venta.fecha):
            raise VentaNoEsDeHoy(venta_id, accion=accion)
        if not (es_admin or venta.vendedor_id == user_id):
            raise OperacionNoAutorizada(venta_id, accion=accion)
        if await self._repo.tiene_factura_viva(venta_id):
            raise VentaConFacturaViva(venta_id, accion=accion)
        if await self._repo.tiene_devolucion(venta_id):
            raise VentaConDevolucion(venta_id, accion=accion)
        return venta

    async def borrar_venta(self, venta_id: int, *, user_id: int, es_admin: bool) -> int:
        """Borra una venta de HOY (Colombia) restaurando stock. Devuelve el `venta_id` borrado.

        Aplica los guards comunes (404/409/403/409) y, si pasa, delega el borrado físico transaccional
        (reversión de stock + movimientos) al repositorio.
        """
        await self._guard_modificacion(venta_id, user_id=user_id, es_admin=es_admin, accion="borrar")
        await self._repo.borrar_venta(venta_id)
        return venta_id

    async def editar_venta(
        self, venta_id: int, datos: VentaCrear, *, user_id: int, es_admin: bool,
        control_stock_estricto: bool = False,
    ) -> VentaConLineas:
        """Edita una venta de HOY EN EL LUGAR: mantiene id, consecutivo y fecha original.

        Mismos guards que el borrado (404/409/403/409, mensajes con verbo "editar"). Luego, en la misma
        transacción: revierte el stock de las líneas viejas (reusa la reversión del borrado), resuelve
        las líneas nuevas contra el stock YA restaurado (reusa `_resolver_linea`, respetando
        `control_stock_estricto`), recalcula totales y aplica el detalle nuevo + sus SALIDA. Devuelve la
        venta con sus líneas (`venta_editada` emitido por el repositorio).
        """
        await self._guard_modificacion(venta_id, user_id=user_id, es_admin=es_admin, accion="editar")
        # Revertir ANTES de resolver: así `lock_inventario` ve el stock ya restaurado (el modo estricto
        # cuenta como disponible lo que liberan las líneas viejas).
        await self._repo.revertir_lineas(venta_id)
        lineas = [await self._resolver_linea(ln, control_stock_estricto) for ln in datos.lineas]
        subtotal, impuestos, total = calcular_totales(lineas)
        if datos.metodo_pago == "mixto":
            # Mismo invariante que al registrar: el desglose nuevo debe sumar el total nuevo.
            suma_pagos = _money(sum((p.monto for p in datos.pagos), Decimal("0")))
            if suma_pagos != total:
                raise LineaInvalida(
                    f"Las partes del pago mixto suman {suma_pagos} pero la venta vale {total}"
                )
        edicion = EdicionVenta(
            cliente_id=datos.cliente_id, metodo_pago=datos.metodo_pago,
            subtotal=subtotal, impuestos=impuestos, total=total, lineas=lineas,
        )
        venta = await self._repo.aplicar_edicion(venta_id, edicion)
        if venta is None:  # carrera improbable: la venta se borró entre el guard y el apply
            raise VentaNoEncontrada(venta_id)
        # El desglose del cobro sigue al método editado: partes nuevas si quedó mixta, ninguna si no
        # (una mixta editada a efectivo no puede dejar partes viejas colgando).
        await self._repo.reemplazar_pagos(venta_id, datos.pagos)
        return venta

    async def _resolver_linea(self, ln, control_stock_estricto: bool) -> LineaResuelta:
        if ln.producto_id is None:
            return self._linea_varia(ln)
        return await self._linea_catalogo(ln, control_stock_estricto)

    def _linea_varia(self, ln) -> LineaResuelta:
        if ln.precio_unitario is None or not ln.descripcion:
            raise LineaInvalida("Venta varia sin precio_unitario o descripcion")
        total = _money(ln.precio_unitario * ln.cantidad)
        return LineaResuelta(
            producto_id=None, descripcion=ln.descripcion, cantidad=ln.cantidad,
            precio_unitario=ln.precio_unitario, iva=ln.iva or 0,
            total_linea=total, descontar_stock=False,
            tipo_impuesto=getattr(ln, "tipo_impuesto", None) or "iva",
        )

    async def _linea_catalogo(self, ln, control_stock_estricto: bool) -> LineaResuelta:
        prod = await self._repo.obtener_producto(ln.producto_id)
        if prod is None or not prod.activo:
            raise ProductoNoEncontrado(ln.producto_id)
        # Siempre se bloquea el inventario (FOR UPDATE) para descontar en la misma tx, aun en modo
        # permisivo. Solo el modo ESTRICTO (opt-in por empresa) rechaza cuando el stock no alcanza;
        # en permisivo se deja pasar y el stock baja (crear_venta descuenta sin clamp → negativo OK).
        disponible = await self._repo.lock_inventario(ln.producto_id)
        disponible = disponible if disponible is not None else Decimal("0")
        if control_stock_estricto and disponible < ln.cantidad:
            raise StockInsuficiente(ln.producto_id, disponible, ln.cantidad)
        # Precio declarado (override explícito) gana; si no, el motor de precios es la fuente
        # de verdad: escalonado por umbral → fracción → simple (ferrebot-logica-portar.md §3).
        if ln.precio_unitario is not None:
            precio = ln.precio_unitario
            total = _money(precio * ln.cantidad)
        else:
            total, precio = obtener_precio_para_cantidad(prod.esquema(), ln.cantidad)
            if _money(precio * ln.cantidad) != total:
                # Fracción: el motor cobra `precio_total` de la fracción pero devuelve el precio del
                # paquete COMPLETO como unitario. El detalle persiste solo (cantidad, precio_unitario),
                # y la factura DIAN y los reportes reconstruyen la línea multiplicándolos: se guarda
                # el precio EFECTIVO por unidad para que el documento cuadre con lo cobrado.
                precio = total / ln.cantidad
        # Snapshot del costo: promedio ponderado móvil (ADR 0025); fallback al precio_compra si el
        # promedio aún es NULL (producto sin compras tras la migración 0028).
        costo = prod.costo_promedio if prod.costo_promedio is not None else prod.precio_compra
        return LineaResuelta(
            producto_id=prod.id, descripcion=ln.descripcion or prod.nombre, cantidad=ln.cantidad,
            precio_unitario=precio, iva=prod.iva, total_linea=total, descontar_stock=True,
            costo_unitario=costo, tipo_impuesto=prod.tipo_impuesto,
        )
