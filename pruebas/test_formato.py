"""Cómo se ven los datos en el dashboard: miles, fechas, duraciones y celulares."""

from datetime import date

import pytest

from app.formato import cuando_fue, duracion, fecha_larga, miles, telefono_oculto


@pytest.mark.parametrize(
    ("valor", "texto"), [(0, "0"), (950, "950"), (6_000, "6.000"), (1_250_000, "1.250.000")]
)
def test_miles_con_punto(valor, texto):
    assert miles(valor) == texto


@pytest.mark.parametrize(
    ("dia", "texto"),
    [
        (date(2026, 10, 31), "Sábado 31 de octubre"),
        (date(2026, 10, 26), "Lunes 26 de octubre"),
        (date(2027, 1, 3), "Domingo 3 de enero"),
    ],
)
def test_fecha_larga(dia, texto):
    assert fecha_larga(dia) == texto


@pytest.mark.parametrize(
    ("minutos", "texto"),
    [
        (0, "0 min"),
        (45, "45 min"),
        (60, "1 h"),
        (65, "1 h 05 min"),
        (130, "2 h 10 min"),
        (-3, "0 min"),
    ],
)
def test_duracion(minutos, texto):
    assert duracion(minutos) == texto


@pytest.mark.parametrize(
    ("telefono", "texto"),
    [
        ("573214564410", "321 *** 4410"),
        ("3214564410", "321 *** 4410"),
        (None, "Sin celular"),
        ("", "Sin celular"),
    ],
)
def test_telefono_oculto(telefono, texto):
    assert telefono_oculto(telefono) == texto


@pytest.mark.parametrize(
    ("dia", "texto"),
    [
        (date(2026, 10, 26), "hoy"),
        (date(2026, 10, 25), "ayer"),
        (date(2026, 10, 24), "el sábado"),
        (date(2026, 10, 20), "el martes"),  # hace 6 días: todavía es "esta semana"
        (date(2026, 10, 19), "el 19 de octubre"),
        (date(2025, 12, 30), "el 30 de diciembre de 2025"),
    ],
)
def test_cuando_fue_como_se_dice_en_una_frase(dia, texto):
    assert cuando_fue(dia, date(2026, 10, 26)) == texto  # un lunes
