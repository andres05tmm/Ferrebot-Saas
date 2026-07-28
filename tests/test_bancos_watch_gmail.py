"""Vigencia del watch de Gmail: cuándo renovar y cuándo el poll tiene que cubrir.

El watch de Gmail caduca a los 7 días y, cuando expira, Google simplemente DEJA DE PUBLICAR: no hay
error, no hay reintento, no hay aviso. El único síntoma es que no vuelven a entrar transferencias, y
eso solo se nota cuando alguien lo busca. Estas dos decisiones son las que hacen que no haya que
estar pendiente, así que se prueban aparte del cableado de IO.
"""
from datetime import datetime, timedelta, timezone

from apps.worker.bancolombia import debe_renovar, push_vigente

_AHORA = datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc)
_TOPIC = "projects/x/topics/bancolombia"


class _Cuenta:
    def __init__(self, *, pubsub_topic=None, watch_expira=None):
        self.pubsub_topic = pubsub_topic
        self.watch_expira = watch_expira
        self.empresa_id = 1


def _en(horas: float) -> datetime:
    return _AHORA + timedelta(hours=horas)


# --- ¿el push está realmente vivo? -------------------------------------------

def test_sin_topic_el_push_no_esta_vivo():
    """Una cuenta en modo polling nunca tuvo push: el poll es su única vía."""
    assert not push_vigente(_Cuenta(), _AHORA)


def test_con_topic_y_watch_vigente_el_push_esta_vivo():
    assert push_vigente(_Cuenta(pubsub_topic=_TOPIC, watch_expira=_en(72)), _AHORA)


def test_watch_vencido_apaga_el_push_aunque_haya_topic():
    """El caso que dejaba la ingesta muda: hay topic, pero Gmail ya no publica."""
    assert not push_vigente(_Cuenta(pubsub_topic=_TOPIC, watch_expira=_en(-1)), _AHORA)


def test_topic_recien_puesto_sin_watch_todavia_no_es_push_vivo():
    """Entre setear el topic y activar el watch no llega nada: el poll cubre esa ventana."""
    assert not push_vigente(_Cuenta(pubsub_topic=_TOPIC, watch_expira=None), _AHORA)


def test_una_fecha_sin_zona_se_lee_como_utc_y_no_revienta():
    """Comparar naive contra aware lanza TypeError, y ahí el `except` del barrido se lo tragaría:
    la cuenta quedaría sin push y TAMPOCO sin poll. Es el peor final posible, en silencio."""
    naive = _en(72).replace(tzinfo=None)
    assert push_vigente(_Cuenta(pubsub_topic=_TOPIC, watch_expira=naive), _AHORA)


# --- ¿toca renovar? ----------------------------------------------------------

def test_no_se_renueva_lo_que_tiene_margen_de_sobra():
    assert not debe_renovar(_Cuenta(pubsub_topic=_TOPIC, watch_expira=_en(72)), _AHORA)


def test_se_renueva_anticipadamente_dentro_de_las_48h():
    """Renovar antes de que venza es lo que da varios intentos: con margen de 48h y una corrida
    diaria, hacen falta ~5 fallos seguidos para perder el watch."""
    assert debe_renovar(_Cuenta(pubsub_topic=_TOPIC, watch_expira=_en(47)), _AHORA)


def test_se_activa_el_watch_de_una_cuenta_recien_dada_de_alta():
    """Sin `watch_expira` nunca se activó. Antes había que dispararlo a mano tras provisionar."""
    assert debe_renovar(_Cuenta(pubsub_topic=_TOPIC, watch_expira=None), _AHORA)


def test_una_cuenta_sin_topic_no_se_intenta_renovar():
    """Llamar `watch` sobre un buzón compartido le robaría el push al otro sistema (regla del poll)."""
    assert not debe_renovar(_Cuenta(watch_expira=None), _AHORA)


def test_lo_vencido_se_renueva():
    assert debe_renovar(_Cuenta(pubsub_topic=_TOPIC, watch_expira=_en(-100)), _AHORA)


# --- las dos decisiones no pueden contradecirse ------------------------------

def test_un_push_vivo_nunca_se_renueva_al_mismo_tiempo_que_lo_cubre_el_poll():
    """Lo único que las dos funciones pueden hacer mal juntas es solaparse en el borde.

    Un watch con margen de sobra tiene que estar vivo (el poll lo saltea) Y no tocar la renovación.
    Uno que ya venció, al revés: el poll lo cubre y el cron lo reactiva. La franja de 48h es la
    interesante: ahí el push SIGUE vivo (nadie tiene que pollear) pero ya toca renovar.
    """
    sano = _Cuenta(pubsub_topic=_TOPIC, watch_expira=_en(72))
    assert push_vigente(sano, _AHORA) and not debe_renovar(sano, _AHORA)

    por_vencer = _Cuenta(pubsub_topic=_TOPIC, watch_expira=_en(24))
    assert push_vigente(por_vencer, _AHORA) and debe_renovar(por_vencer, _AHORA)

    vencido = _Cuenta(pubsub_topic=_TOPIC, watch_expira=_en(-1))
    assert not push_vigente(vencido, _AHORA) and debe_renovar(vencido, _AHORA)
