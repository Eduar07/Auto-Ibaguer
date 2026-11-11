"""Las reglas de plata del parqueadero, sin base de datos.

Tarifa de Eduar: carro $3.000 la hora, moto $1.500, y 15 minutos de gracia después de cada
hora antes de cobrar la siguiente.
"""

from datetime import UTC, date, datetime, timedelta

import pytest

from app.parqueadero.errores import PlacaInvalida, TarifaInvalida, ValorInvalido
from app.parqueadero.reglas import (
    Cuadre,
    Tarifa,
    calcular_cobro,
    fin_de_la_mensualidad,
    horas_a_cobrar,
    minutos_adentro,
    motivo_del_ajuste,
    placa_o_ticket,
    revisar_periodo,
    revisar_recibido,
    validar_placa,
    validar_tarifa,
)
from app.turnos.estados import MedioPago

CARRO = Tarifa(valor_hora=3000, gracia_minutos=15)
MOTO = Tarifa(valor_hora=1500, gracia_minutos=15)


@pytest.mark.parametrize(
    ("minutos", "horas"),
    [
        (0, 1),  # mínimo una hora
        (10, 1),
        (60, 1),
        (75, 1),  # 1 h 15 min: todavía está en la gracia
        (76, 2),  # 1 h 16 min: se pasó, otra hora
        (120, 2),
        (135, 2),
        (136, 3),
        (24 * 60, 24),
        (-5, 1),  # un reloj desfasado no da un cobro negativo
    ],
)
def test_hora_o_fraccion_con_15_minutos_de_gracia(minutos, horas):
    assert horas_a_cobrar(minutos, gracia_minutos=15) == horas


@pytest.mark.parametrize(("minutos", "horas"), [(60, 1), (61, 2), (120, 2), (121, 3)])
def test_sin_gracia_cada_minuto_extra_es_otra_hora(minutos, horas):
    assert horas_a_cobrar(minutos, gracia_minutos=0) == horas


@pytest.mark.parametrize(
    ("tarifa", "minutos", "valor"),
    [
        (CARRO, 120, 6000),  # el otro sistema cobraba $14.400: todo por minuto
        (CARRO, 76, 6000),
        (CARRO, 75, 3000),
        (CARRO, 24 * 60, 72000),
        (MOTO, 50, 1500),
        (MOTO, 76, 3000),
    ],
)
def test_lo_que_paga_cada_vehiculo(tarifa, minutos, valor):
    assert calcular_cobro(minutos, tarifa).valor == valor


def test_la_fraccion_de_los_primeros_15_minutos_es_opcional():
    con_fraccion = Tarifa(valor_hora=3000, gracia_minutos=15, valor_fraccion=1000)

    assert calcular_cobro(10, con_fraccion).valor == 1000
    assert calcular_cobro(15, con_fraccion).fraccion is True
    assert calcular_cobro(16, con_fraccion).valor == 3000  # desde el minuto 16, la hora
    assert calcular_cobro(10, CARRO).valor == 3000  # sin fracción: la hora completa


def test_los_minutos_adentro_son_minutos_completos():
    entrada = datetime(2026, 11, 12, 8, 0, tzinfo=UTC)

    assert minutos_adentro(entrada, entrada + timedelta(minutes=75, seconds=50)) == 75
    assert minutos_adentro(entrada, entrada - timedelta(minutes=3)) == 0


def test_cobrar_distinto_a_la_tarifa_pide_motivo():
    assert motivo_del_ajuste(6000, 6000, "") is None
    assert motivo_del_ajuste(6000, 5000, "  cliente frecuente ") == "cliente frecuente"
    with pytest.raises(ValorInvalido, match="escribe el motivo"):
        motivo_del_ajuste(6000, 5000, "")
    with pytest.raises(ValorInvalido, match="negativo"):
        motivo_del_ajuste(6000, -1, "error")


def test_el_billete_que_entregan_solo_sirve_para_el_vuelto():
    assert revisar_recibido(6000, MedioPago.EFECTIVO, 50000) == 50000
    assert revisar_recibido(6000, MedioPago.EFECTIVO, None) is None
    assert revisar_recibido(6000, MedioPago.NEQUI, 50000) is None  # por Nequi no hay vuelto
    with pytest.raises(ValorInvalido, match="no alcanza"):
        revisar_recibido(6000, MedioPago.EFECTIVO, 5000)


def test_la_mensualidad_son_30_dias_contando_el_primero():
    assert fin_de_la_mensualidad(date(2026, 11, 10)) == date(2026, 12, 9)
    assert fin_de_la_mensualidad(date(2026, 1, 31)) == date(2026, 3, 1)
    revisar_periodo(date(2026, 11, 10), date(2026, 12, 9))
    with pytest.raises(ValorInvalido, match="antes de la inicial"):
        revisar_periodo(date(2026, 11, 10), date(2026, 11, 9))


@pytest.mark.parametrize(
    ("escrito", "placa"),
    [(" abc-123 ", "ABC123"), ("abc12d", "ABC12D"), ("ABC12", "ABC12"), ("r12345", "R12345")],
)
def test_placas_validas(escrito, placa):
    assert validar_placa(escrito) == placa


@pytest.mark.parametrize("escrito", ["", "123", "ABCDEF", "AB", "ABC1234X"])
def test_lo_que_no_es_una_placa(escrito):
    with pytest.raises(PlacaInvalida):
        validar_placa(escrito)


def test_buscar_por_placa_o_por_ticket():
    assert placa_o_ticket("123") == (None, 123)
    assert placa_o_ticket(" #45 ") == (None, 45)
    assert placa_o_ticket("abc-123") == ("ABC123", None)
    assert placa_o_ticket("  ") == (None, None)


def test_tarifas_que_no_tienen_sentido():
    with pytest.raises(TarifaInvalida):
        validar_tarifa(Tarifa(valor_hora=0, gracia_minutos=15))
    with pytest.raises(TarifaInvalida):
        validar_tarifa(Tarifa(valor_hora=3000, gracia_minutos=60))
    with pytest.raises(TarifaInvalida):
        validar_tarifa(Tarifa(valor_hora=3000, gracia_minutos=15, valor_fraccion=-1))


def test_el_cuadre_de_caja_cuenta_solo_el_efectivo_en_el_cajon():
    cuadre = Cuadre(
        base=50000,
        parqueos={MedioPago.EFECTIVO: 6000, MedioPago.NEQUI: 3000},
        mensualidades={MedioPago.EFECTIVO: 120000},
    )

    assert cuadre.por_medio(MedioPago.EFECTIVO) == 126000
    assert cuadre.efectivo_esperado == 176000  # base + efectivo
    assert cuadre.total == 129000  # lo que entró, sin la base
