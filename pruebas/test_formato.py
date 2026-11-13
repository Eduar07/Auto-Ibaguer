"""Cómo se ven los datos en el dashboard: miles, fechas, duraciones y celulares."""

from datetime import date, datetime

import pytest

from app.formato import (
    cuando_fue,
    cuando_paso,
    cuando_sera,
    duracion,
    fecha_larga,
    miles,
    telefono_oculto,
)


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
        (23 * 60 + 59, "23 h 59 min"),
        (24 * 60, "1 día"),
        (24 * 60 + 5, "1 día 5 min"),
        (24 * 60 + 60, "1 día 1 h"),
        (4 * 24 * 60 + 2 * 60 + 1, "4 días 2 h 01 min"),
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


@pytest.mark.parametrize(
    ("momento", "texto"),
    [
        (datetime(2026, 10, 26, 16, 15), "las 4:15 p.m."),  # hoy: no hace falta decirlo
        (datetime(2026, 10, 26, 13, 5), "la 1:05 p.m."),
        (datetime(2026, 10, 27, 7, 50), "mañana a las 7:50 a.m."),
        (datetime(2026, 11, 2, 8, 0), "el 2 de noviembre a las 8:00 a.m."),
        (datetime(2027, 1, 4, 9, 30), "el 4 de enero de 2027 a las 9:30 a.m."),
    ],
)
def test_cuando_sera_como_se_dice_despues_de_hasta(momento, texto):
    assert cuando_sera(momento, date(2026, 10, 26)) == texto  # un lunes


@pytest.mark.parametrize(
    ("momento", "texto"),
    [
        (datetime(2026, 10, 26, 19, 44), "hoy a las 7:44 p.m."),
        (datetime(2026, 10, 25, 13, 5), "ayer a la 1:05 p.m."),
        (datetime(2026, 10, 22, 8, 0), "el jueves a las 8:00 a.m."),
        (datetime(2026, 10, 3, 6, 30), "el 3 de octubre a las 6:30 a.m."),
    ],
)
def test_cuando_paso_como_se_dice_en_una_frase(momento, texto):
    assert cuando_paso(momento, date(2026, 10, 26)) == texto  # un lunes
