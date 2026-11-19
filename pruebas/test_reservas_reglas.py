"""Las reglas de las reservas, sin base de datos: los horarios del día, los cupos, los
puestos que se guardan para quien reservó y cuándo entra cada uno a un puesto.

Lo que tiene que cumplirse siempre:
- los horarios van cada hora desde que abren, mientras el servicio alcance a terminar
  antes del cierre (con cierre a las 12 de la noche, el último es 10:30 p.m.);
- en ningún momento hay más reservas que puestos;
- a quien reservó no le quita el puesto alguien de la fila que entró justo antes;
- a su hora, quien reservó va de primero.
"""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.turnos import reglas
from app.turnos.estados import EstadoTurno
from app.turnos.reglas import TurnoEnEspera

BOGOTA = ZoneInfo("America/Bogota")
JUEVES = date(2026, 11, 19)
TODOS = frozenset(range(1, 8))
HORA = timedelta(hours=1)
LAVADO = timedelta(minutes=45)


def a_las(hora: int, minuto: int = 0, dia: date = JUEVES) -> datetime:
    return datetime.combine(dia, time(hora, minuto), BOGOTA)


# --- El horario ------------------------------------------------------------------------------


def test_los_horarios_van_cada_hora_desde_que_abren_hasta_las_10_30_pm():
    horas = reglas.horarios_del_dia(
        JUEVES,
        apertura=time(6, 30),
        cierre=time(0),  # a las 12 de la noche
        dias=TODOS,
        cada=HORA,
        duracion=LAVADO,
        zona=BOGOTA,
    )

    assert horas[0] == a_las(6, 30) and horas[1] == a_las(7, 30)
    assert horas[-1] == a_las(22, 30)  # a las 11:30 p.m. ya no alcanza a terminar
    assert len(horas) == 17


def test_un_dia_que_no_abren_no_tiene_horarios():
    sin_jueves = frozenset({1, 2, 3, 5, 6, 7})

    horas = reglas.horarios_del_dia(
        JUEVES,
        apertura=time(6, 30),
        cierre=time(0),
        dias=sin_jueves,
        cada=HORA,
        duracion=LAVADO,
        zona=BOGOTA,
    )

    assert horas == []


def test_cada_media_hora_si_asi_se_configura():
    horas = reglas.horarios_del_dia(
        JUEVES,
        apertura=time(8),
        cierre=time(10),
        dias=TODOS,
        cada=timedelta(minutes=30),
        duracion=LAVADO,
        zona=BOGOTA,
    )

    assert horas == [a_las(8), a_las(8, 30), a_las(9)]  # 9:30 + 45 min pasa de las 10


@pytest.mark.parametrize(
    ("momento", "abierto"),
    [
        (a_las(6, 29), False),
        (a_las(6, 30), True),
        (a_las(23, 59), True),  # cierra a las 12 de la noche
        (a_las(0, 10), False),  # ya es otro día y no han abierto
    ],
)
def test_cerrar_a_las_12_de_la_noche_es_al_final_del_dia(momento, abierto):
    assert reglas.esta_en_horario(momento, time(6, 30), time(0), TODOS) is abierto


def test_el_cierre_a_las_12_es_el_comienzo_del_dia_siguiente():
    assert reglas.cierre_del_dia(JUEVES, time(0), BOGOTA) == a_las(0, dia=JUEVES + timedelta(1))
    assert reglas.cierre_del_dia(JUEVES, time(19, 30), BOGOTA) == a_las(19, 30)


# --- Los cupos -------------------------------------------------------------------------------


def test_en_cada_horario_caben_tantos_como_puestos():
    dos_a_las_10 = [(a_las(10, 30), LAVADO), (a_las(10, 30), LAVADO)]

    assert reglas.cabe_otra_reserva(a_las(10, 30), LAVADO, dos_a_las_10[:1], capacidad=2)
    assert not reglas.cabe_otra_reserva(a_las(10, 30), LAVADO, dos_a_las_10, capacidad=2)
    # La de las 11:30 no se cruza: las de las 10:30 terminan a las 11:15.
    assert reglas.cabe_otra_reserva(a_las(11, 30), LAVADO, dos_a_las_10, capacidad=2)


def test_las_que_se_cruzan_cuentan_aunque_no_empiecen_a_la_misma_hora():
    # Cada media hora, servicios de 45 minutos: la de las 10:00 sigue a las 10:30.
    reservas = [(a_las(10), LAVADO), (a_las(10, 30), LAVADO)]

    assert not reglas.cabe_otra_reserva(a_las(10, 15), LAVADO, reservas, capacidad=2)
    assert reglas.cabe_otra_reserva(a_las(10, 45), LAVADO, reservas, capacidad=2)


def test_sin_puestos_no_cabe_ninguna():
    assert not reglas.cabe_otra_reserva(a_las(10), LAVADO, [], capacidad=0)


# --- Los puestos que se guardan --------------------------------------------------------------


def test_un_puesto_libre_se_guarda_para_la_reserva_que_viene():
    assert reglas.puestos_para_reservas([], [a_las(10, 30)], libres=1) == 1


def test_si_un_puesto_ocupado_se_libera_antes_no_hace_falta_guardar_otro():
    termina_a_las_10 = [a_las(10)]

    assert reglas.puestos_para_reservas(termina_a_las_10, [a_las(10, 30)], libres=1) == 0
    # Uno ocupado alcanza para una reserva, no para dos.
    assert reglas.puestos_para_reservas(termina_a_las_10, [a_las(10, 30)] * 2, libres=2) == 1


def test_nunca_se_guardan_mas_puestos_de_los_que_hay_libres():
    assert reglas.puestos_para_reservas([], [a_las(10, 30)] * 3, libres=1) == 1


def en_fila(turno_id: int, *, prioridad: bool = False) -> TurnoEnEspera:
    return TurnoEnEspera(turno_id, EstadoTurno.EN_FILA, False, prioridad)


def test_con_un_puesto_guardado_nadie_de_la_fila_entra():
    decision = reglas.decidir_llamados([en_fila(1), en_fila(2)], 1, 2, guardados=1)

    assert decision.llamar == ()
    assert [turno for turno, _ in decision.avisar] == [1, 2]


def test_a_su_hora_quien_reservo_usa_el_puesto_guardado():
    espera = [en_fila(9, prioridad=True), en_fila(1)]

    # Uno libre y otro guardado para una reserva que viene: el de prioridad entra.
    assert reglas.decidir_llamados(espera, 1, 2, guardados=1).llamar == (9,)
    # Con dos libres y uno guardado, entra también el primero de la fila.
    assert reglas.decidir_llamados(espera, 3, 2, guardados=1).llamar == (9, 1)


# --- La espera ---------------------------------------------------------------------------------


def test_la_espera_cuenta_las_reservas_que_vienen():
    ahora = a_las(10)
    # Un puesto libre; una reserva a las 10:30. El de la fila no alcanza a terminar antes
    # de la reserva: entra después de ella.
    estimacion = reglas.estimar_fila(
        [ahora], [LAVADO], ahora, reservas=[(a_las(10, 30), LAVADO)], duracion_nueva=LAVADO
    )

    assert estimacion.inicios == (a_las(11, 15),)
    assert estimacion.siguiente == a_las(12)


def test_sin_reservas_la_espera_es_la_de_siempre():
    ahora = a_las(10)

    estimacion = reglas.estimar_fila([ahora, ahora], [LAVADO, LAVADO, LAVADO], ahora)

    assert estimacion.inicios == (ahora, ahora, a_las(10, 45))
