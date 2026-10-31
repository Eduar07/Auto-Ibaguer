"""Reglas de la fila, sin base de datos ni internet: corren en milisegundos."""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.formato import hora, hora_con_articulo, pesos
from app.turnos import reglas
from app.turnos.errores import PrecioInvalido
from app.turnos.estados import Actor, EstadoTurno

E = EstadoTurno
BOGOTA = ZoneInfo("America/Bogota")
AHORA = datetime(2026, 10, 26, 8, 0, tzinfo=BOGOTA)
HORA = timedelta(hours=1)
TODOS_LOS_DIAS = frozenset(range(1, 8))


def esperando(*estados: EstadoTurno, avisados: frozenset[int] = frozenset()):
    """Arma una fila de espera en orden. Los turnos se llaman 1, 2, 3..."""
    return [reglas.TurnoEnEspera(i, estado, i in avisados) for i, estado in enumerate(estados, 1)]


def min_(cantidad: int) -> timedelta:
    return timedelta(minutes=cantidad)


# --- Quién puede cambiar qué -----------------------------------------------------------


@pytest.mark.parametrize(
    ("actual", "nuevo", "actor"),
    [
        (E.EN_FILA, E.CANCELADO, Actor.CLIENTE),
        (E.LLAMADO, E.CANCELADO, Actor.CLIENTE),
        (E.EN_FILA, E.LLEGO, Actor.CLIENTE),  # llega antes de que lo llamen
        (E.LLAMADO, E.LLEGO, Actor.CLIENTE),
        (E.LLAMADO, E.EN_LAVADO, Actor.ADMINISTRADOR),
        (E.LLEGO, E.EN_LAVADO, Actor.ADMINISTRADOR),
        (E.LLEGO, E.CANCELADO, Actor.ADMINISTRADOR),
        (E.EN_LAVADO, E.LISTO, Actor.ADMINISTRADOR),
        (E.LISTO, E.PAGADO, Actor.ADMINISTRADOR),
        (E.EN_FILA, E.LLAMADO, Actor.SISTEMA),
        (E.LLAMADO, E.EN_FILA, Actor.SISTEMA),
        (E.LLAMADO, E.NO_LLEGO, Actor.SISTEMA),
    ],
)
def test_cambios_permitidos(actual, nuevo, actor):
    assert reglas.puede_cambiar(actual, nuevo, actor)


@pytest.mark.parametrize(
    ("actual", "nuevo", "actor"),
    [
        (E.LLEGO, E.CANCELADO, Actor.CLIENTE),  # por WhatsApp solo En fila o Llamado
        (E.EN_LAVADO, E.CANCELADO, Actor.CLIENTE),
        (E.EN_LAVADO, E.CANCELADO, Actor.ADMINISTRADOR),
        (E.EN_FILA, E.EN_LAVADO, Actor.ADMINISTRADOR),  # primero debe llegar o ser llamado
        (E.EN_FILA, E.LLAMADO, Actor.ADMINISTRADOR),  # llamar lo hace el sistema
        (E.LLAMADO, E.NO_LLEGO, Actor.ADMINISTRADOR),
        (E.EN_LAVADO, E.PAGADO, Actor.ADMINISTRADOR),  # se cobra cuando está listo
        (E.PAGADO, E.CANCELADO, Actor.ADMINISTRADOR),
        (E.NO_LLEGO, E.EN_FILA, Actor.SISTEMA),
    ],
)
def test_cambios_no_permitidos(actual, nuevo, actor):
    assert not reglas.puede_cambiar(actual, nuevo, actor)


def test_pagado_cancelado_y_no_llego_son_finales():
    salidas = {actual for actual, _ in reglas.TRANSICIONES}
    assert salidas.isdisjoint({E.PAGADO, E.CANCELADO, E.NO_LLEGO})


# --- Número de turno y horario ---------------------------------------------------------


@pytest.mark.parametrize(
    ("prefijo", "consecutivo", "numero"), [("C", 14, "C-14"), ("M", 3, "M-03"), ("C", 120, "C-120")]
)
def test_numero_de_turno(prefijo, consecutivo, numero):
    assert reglas.numero_de_turno(prefijo, consecutivo) == numero


@pytest.mark.parametrize(
    ("hora_local", "abierto"),
    [
        (time(5, 29), False),
        (time(5, 30), True),
        (time(12, 0), True),
        (time(19, 29), True),
        (time(19, 30), False),
        (time(23, 0), False),
    ],
)
def test_horario_de_turnos_por_whatsapp(hora_local, abierto):
    momento = datetime.combine(date(2026, 10, 26), hora_local, tzinfo=BOGOTA)
    assert reglas.esta_en_horario(momento, time(5, 30), time(19, 30), TODOS_LOS_DIAS) is abierto


def test_un_dia_sin_atencion_no_da_turnos():
    domingo = datetime(2026, 11, 1, 9, 0, tzinfo=BOGOTA)
    lunes_a_sabado = frozenset(range(1, 7))
    assert not reglas.esta_en_horario(domingo, time(5, 30), time(19, 30), lunes_a_sabado)


# --- Llamados y avisos -----------------------------------------------------------------


def test_un_puesto_libre_llama_al_primero_y_avisa_a_los_que_estan_cerca():
    decision = reglas.decidir_llamados(esperando(E.EN_FILA, E.EN_FILA, E.EN_FILA, E.EN_FILA), 1, 2)
    assert decision.llamar == (1,)
    # El 2 tiene 1 adelante y el 3 tiene 2: salen ya. El 4 tiene 3: todavía no.
    assert decision.avisar == ((2, 1), (3, 2))


def test_cada_puesto_libre_llama_a_uno():
    decision = reglas.decidir_llamados(esperando(*[E.EN_FILA] * 5), 4, 2)
    assert decision.llamar == (1, 2, 3, 4)
    assert decision.avisar == ()  # el 5 tiene 4 adelante


def test_sin_puestos_libres_no_se_llama_a_nadie():
    decision = reglas.decidir_llamados(esperando(E.EN_FILA, E.EN_FILA), 0, 2)
    assert decision.llamar == ()
    assert decision.avisar == ((1, 0), (2, 1))


def test_no_se_llama_ni_se_avisa_dos_veces():
    fila = esperando(E.LLAMADO, E.LLEGO, E.EN_FILA, E.EN_FILA, avisados=frozenset({3}))
    decision = reglas.decidir_llamados(fila, 2, 2)
    assert decision.llamar == ()  # los dos puestos ya tienen a quién esperar
    assert decision.avisar == ()  # el 3 ya sabe; el 4 tiene 3 adelante


def test_a_quien_ya_esta_en_el_local_no_se_le_avisa_que_salga():
    decision = reglas.decidir_llamados(esperando(E.LLEGO, E.LLEGO), 0, 2)
    assert decision == reglas.Llamados(llamar=(), avisar=())


def test_el_puesto_no_espera_al_llamado_si_alguien_de_atras_ya_llego():
    assert reglas.elegir_siguiente_para_puesto(esperando(E.LLAMADO, E.EN_FILA, E.LLEGO)) == 3


def test_entre_los_que_llegaron_entra_el_primero_de_la_fila():
    assert reglas.elegir_siguiente_para_puesto(esperando(E.EN_FILA, E.LLEGO, E.LLEGO)) == 2


def test_si_nadie_ha_llegado_el_puesto_espera_al_llamado():
    assert reglas.elegir_siguiente_para_puesto(esperando(E.LLAMADO, E.EN_FILA)) is None


def test_tiempo_de_gracia():
    assert reglas.al_vencer_la_gracia(0) == E.EN_FILA  # primera vez: al final
    assert reglas.al_vencer_la_gracia(1) == E.NO_LLEGO  # segunda vez: pierde el turno


# --- Espera estimada -------------------------------------------------------------------


def test_con_puestos_libres_y_sin_fila_no_hay_espera():
    assert reglas.estimar_fila([AHORA, AHORA], [], AHORA).siguiente == AHORA


def test_suma_lo_de_adelante_y_lo_reparte_entre_los_puestos():
    # 2 puestos libres y 4 carros de 1 hora en la fila: el quinto entra en 2 horas.
    estimacion = reglas.estimar_fila([AHORA, AHORA], [HORA] * 4, AHORA)
    assert estimacion.inicios == (AHORA, AHORA, AHORA + HORA, AHORA + HORA)
    assert estimacion.siguiente == AHORA + 2 * HORA


def test_cuenta_lo_que_les_falta_a_los_que_se_estan_lavando():
    # C1 se libera en 20 min y C2 en 45 min.
    puestos = [AHORA + min_(20), AHORA + min_(45)]
    assert reglas.estimar_fila(puestos, [], AHORA).siguiente == AHORA + min_(20)
    con_uno_esperando = reglas.estimar_fila(puestos, [HORA], AHORA)
    assert con_uno_esperando.inicios == (AHORA + min_(20),)
    assert con_uno_esperando.siguiente == AHORA + min_(45)


def test_un_lavado_que_se_paso_de_tiempo_cuenta_como_por_terminar():
    assert reglas.estimar_fila([AHORA - min_(10)], [], AHORA).siguiente == AHORA


def test_sin_puestos_activos_no_se_puede_estimar():
    assert reglas.estimar_fila([], [HORA], AHORA) == reglas.Estimacion(inicios=(), siguiente=None)


def test_minutos_hasta():
    assert reglas.minutos_hasta(AHORA + timedelta(seconds=61), AHORA) == 2  # redondea hacia arriba
    assert reglas.minutos_hasta(AHORA - min_(5), AHORA) == 0
    assert reglas.minutos_hasta(None, AHORA) is None


# --- Cobro y formato -------------------------------------------------------------------


def test_el_precio_del_menu_no_necesita_motivo():
    assert reglas.motivo_del_cobro(40_000, 40_000, None) is None


@pytest.mark.parametrize("motivo", [None, "", "   "])
def test_otro_precio_exige_motivo(motivo):
    with pytest.raises(PrecioInvalido, match=r"\$35\.000.*\$40\.000"):
        reglas.motivo_del_cobro(40_000, 35_000, motivo)


def test_el_motivo_se_guarda_limpio():
    assert reglas.motivo_del_cobro(40_000, 45_000, "  barro extra ") == "barro extra"


def test_el_precio_no_puede_ser_negativo():
    with pytest.raises(PrecioInvalido, match="negativo"):
        reglas.motivo_del_cobro(40_000, -1, "error")


@pytest.mark.parametrize(
    ("valor", "texto"), [(40_000, "$40.000"), (1_250_000, "$1.250.000"), (0, "$0")]
)
def test_pesos(valor, texto):
    assert pesos(valor) == texto


@pytest.mark.parametrize(
    ("momento", "texto"),
    [
        (time(5, 30), "5:30 a.m."),
        (time(19, 30), "7:30 p.m."),
        (time(12, 5), "12:05 p.m."),
        (time(0, 15), "12:15 a.m."),
    ],
)
def test_horas_a_la_colombiana(momento, texto):
    assert hora(momento) == texto


def test_hora_con_articulo():
    assert hora_con_articulo(time(8, 15)) == "las 8:15 a.m."
    assert hora_con_articulo(time(13, 15)) == "la 1:15 p.m."
