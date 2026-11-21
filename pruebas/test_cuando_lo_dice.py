"""Cuándo quiere venir, dicho con sus palabras (app.bot.cuando). El bot lo entiende solo,
sin Gemini, para reservar o cambiar la hora: "mañana a las 10", "el sábado en la tarde"."""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.bot.cuando import Dicho, entender

JUEVES = date(2026, 11, 19)


@pytest.mark.parametrize(
    ("escrito", "fecha", "hora"),
    [
        ("mañana a las 10", date(2026, 11, 20), time(10)),
        ("mañana a las 10:30 am", date(2026, 11, 20), time(10, 30)),
        ("hoy a las 3", JUEVES, time(15)),  # "a las 3": de la tarde
        ("hoy a las 3 pm", JUEVES, time(15)),
        ("a las 8 de la noche", None, time(20)),
        ("a las 7 de la mañana", None, time(7)),
        ("a las 4 y media", None, time(16, 30)),
        ("tipo 11", None, time(11)),
        ("a las 12", None, time(12)),
        ("al medio día", None, time(12)),
        ("10:30", None, time(10, 30)),
        ("6:30 p.m.", None, time(18, 30)),
        ("6:30p.m.", None, time(18, 30)),
        ("a las 4pm", None, time(16)),
        ("mañana a las 10.30", date(2026, 11, 20), time(10, 30)),
        ("pasado mañana a las 9", date(2026, 11, 21), time(9)),
        ("el sábado a las 10", date(2026, 11, 21), time(10)),
        ("el lunes", date(2026, 11, 23), None),
        ("el jueves", JUEVES, None),  # hoy es jueves
        ("el próximo jueves", date(2026, 11, 26), None),
        ("el 25", date(2026, 11, 25), None),
        ("el 2", date(2026, 12, 2), None),  # el 2 de este mes ya pasó
        ("el 25 de noviembre a las 9", date(2026, 11, 25), time(9)),
        ("25/11 a las 9", date(2026, 11, 25), time(9)),
        ("el 3 de enero", date(2027, 1, 3), None),
    ],
)
def test_entiende_el_dia_y_la_hora(escrito, fecha, hora):
    dicho = entender(escrito, JUEVES)

    assert dicho is not None
    assert (dicho.fecha, dicho.hora) == (fecha, hora)


def test_mañana_en_la_mañana_es_mañana_temprano():
    dicho = entender("mañana en la mañana", JUEVES)

    assert dicho == Dicho(fecha=date(2026, 11, 20), franja="manana")


def test_en_la_tarde_es_una_parte_del_dia():
    assert entender("el sábado en la tarde", JUEVES) == Dicho(
        fecha=date(2026, 11, 21), franja="tarde"
    )
    assert entender("esta noche", JUEVES) == Dicho(fecha=JUEVES, franja="noche")


@pytest.mark.parametrize(
    ("escrito", "fecha", "hora"),
    [
        # Lo que pidió el negocio: "si dicen que ya no pueden ese día, se reagenda según
        # ellos". El día que no puede no cuenta; cuenta el que propone.
        ("ya no puedo mañana, mejor el sábado a las 10", date(2026, 11, 21), time(10)),
        ("No puedo el sábado. Mejor el domingo", date(2026, 11, 22), None),
        ("para mañana que hoy no puedo", date(2026, 11, 20), None),
        ("hoy no, mañana a las 9", date(2026, 11, 20), time(9)),
        ("el lunes, a las 9:30", date(2026, 11, 23), time(9, 30)),
        # Se corrige a sí mismo: gana lo último que dijo después de "mejor".
        ("mañana a las 10, mejor a las 11", date(2026, 11, 20), time(11)),
        ("el sábado, mejor el domingo", date(2026, 11, 22), None),
        # Dos opciones: la primera es la que prefiere.
        ("mañana a las 10 o el sábado", date(2026, 11, 20), time(10)),
    ],
)
def test_lo_que_ya_no_puede_no_cuenta(escrito, fecha, hora):
    dicho = entender(escrito, JUEVES)

    assert dicho is not None
    assert (dicho.fecha, dicho.hora) == (fecha, hora)


@pytest.mark.parametrize(
    "escrito",
    ["ya no puedo hoy", "no puedo mañana a las 10", "mañana no voy a poder", "ahorita no"],
)
def test_si_solo_dice_lo_que_no_puede_no_hay_dia(escrito):
    """Así el bot le muestra los días, en vez de volver a darle el que no puede."""
    assert entender(escrito, JUEVES) is None


def test_ahorita_es_lo_antes_posible():
    assert entender("ahorita", JUEVES) == Dicho(fecha=JUEVES, ya=True)
    assert entender("lo antes posible", JUEVES) == Dicho(ya=True)


@pytest.mark.parametrize(
    "escrito",
    ["hola", "quiero lavar la moto", "tengo 2 carros", "cuánto vale el básico", ""],
)
def test_lo_que_no_dice_cuando_no_se_inventa(escrito):
    assert entender(escrito, JUEVES) is None


def test_se_guarda_en_la_conversacion_y_se_lee_igual():
    dicho = entender("el sábado a las 10:30", JUEVES)

    assert Dicho.desde_dato(dicho.como_dato()) == dicho
    assert Dicho.desde_dato({"fecha": "no es una fecha"}) is None
    assert Dicho.desde_dato("cualquier cosa") is None


# --- "Ya", "de una", "en 20 minutos": lo que falló en la prueba por WhatsApp (20 nov 2026) ---

A_LAS_9_10 = datetime(2026, 11, 19, 21, 10, tzinfo=ZoneInfo("America/Bogota"))


@pytest.mark.parametrize(
    "escrito",
    [
        "Ya",
        "ya!",
        "ya porfa",
        "de una",
        "de una vez",
        "ahora",
        "ahora mismo",
        "voy ya",
        "ya voy para allá",
        "puedo ir ya?",
        "quiero ir ya",
        "ya llegué",  # está afuera y quiere el turno: lo antes posible
        "Quiero venir ya en 30 hora",  # "30 hora" es un error al escribir: cuenta el "ya"
        "lo más pronto posible",
    ],
)
def test_ya_es_lo_antes_posible(escrito):
    assert entender(escrito, JUEVES, A_LAS_9_10) == Dicho(ya=True)


def test_hoy_ya_es_lo_antes_posible_hoy():
    assert entender("hoy ya", JUEVES, A_LAS_9_10) == Dicho(fecha=JUEVES, ya=True)


@pytest.mark.parametrize(
    "escrito",
    [
        "ya tengo turno",
        "ya lo lavé",
        "ya no puedo",
        "ya más tarde",
        "en un rato",  # cuánto es un rato: mejor preguntar
        "se demora más de una hora?",
        "en 30 hora",
    ],
)
def test_un_ya_que_no_es_venir_ya_no_cuenta(escrito):
    assert entender(escrito, JUEVES, A_LAS_9_10) is None


@pytest.mark.parametrize(
    "escrito", ["en 20 minutos", "en media hora", "ya en 30 minutos", "en 10 minuticos"]
)
def test_hasta_media_hora_es_venir_ya(escrito):
    """Antes de media hora no se reserva: es la fila de hoy, lo antes posible."""
    assert entender(escrito, JUEVES, A_LAS_9_10) == Dicho(ya=True)


@pytest.mark.parametrize(
    ("escrito", "fecha", "hora"),
    [
        ("en una hora", JUEVES, time(22, 10)),
        ("dentro de 45 minutos", JUEVES, time(21, 55)),
        ("en cuarenta y cinco minutos", JUEVES, time(21, 55)),
        ("en hora y media", JUEVES, time(22, 40)),
        ("en 2 horas y media", JUEVES, time(23, 40)),
        ("en 3 horas", date(2026, 11, 20), time(0, 10)),  # pasa de la medianoche
    ],
)
def test_en_un_rato_se_cuenta_desde_ahora(escrito, fecha, hora):
    assert entender(escrito, JUEVES, A_LAS_9_10) == Dicho(fecha=fecha, hora=hora)


def test_la_hora_se_redondea_a_cinco_minutos_hacia_adelante():
    a_las_9_07 = A_LAS_9_10 - timedelta(minutes=3)
    assert entender("en una hora", JUEVES, a_las_9_07) == Dicho(fecha=JUEVES, hora=time(22, 10))


def test_sin_saber_que_hora_es_en_un_rato_no_se_entiende():
    assert entender("en una hora", JUEVES) is None


def test_una_hora_dicha_gana_al_en_un_rato():
    assert entender("en una hora, a las 11 de la noche", JUEVES, A_LAS_9_10) == Dicho(hora=time(23))
