"""Pedir turno: horario, número del día y un turno activo por teléfono en cada fila."""

from datetime import date, datetime, time, timedelta

import pytest
from sqlalchemy.orm import Session

from app.catalogo.tablas import Configuracion
from app.clientes import servicio as clientes
from app.turnos import casos_de_uso as casos
from app.turnos.errores import FueraDeHorario, ServicioNoDisponible, YaTieneTurnoActivo
from app.turnos.estados import Actor, EstadoTurno
from pruebas.fabricas import (
    BOGOTA,
    LUNES_8AM,
    avisos,
    cliente_con_vehiculo,
    en_el_local,
    historial,
    minutos,
    ocupar_puestos,
    por_whatsapp,
    servicio,
    tipo_vehiculo,
)

E = EstadoTurno


def lunes_a_las(hora_local: time) -> datetime:
    return datetime.combine(date(2026, 10, 26), hora_local, tzinfo=BOGOTA)


def test_con_puesto_libre_se_llama_de_una_vez(sesion: Session):
    nuevo = por_whatsapp(sesion)
    assert nuevo.numero == "C-01"
    assert nuevo.adelante == 0
    assert nuevo.llamado
    assert nuevo.gracia_hasta == LUNES_8AM + minutos(15)
    assert nuevo.espera_min == 0
    # La confirmación del bot ya le dice que venga: no se le manda un aviso aparte.
    assert avisos(sesion) == []


def test_si_esta_cerca_la_confirmacion_le_pide_salir_ya(sesion: Session):
    ocupar_puestos(sesion, "C1", "C2")  # los dos carros se liberan a las 9:00
    nuevo = por_whatsapp(sesion, ahora=LUNES_8AM + minutos(10))
    assert not nuevo.llamado
    assert nuevo.salir_ya
    assert nuevo.adelante == 0
    assert nuevo.espera_min == 50
    assert nuevo.turno.aviso_anticipado_en is not None  # no se le repite el aviso
    assert avisos(sesion, turno_id=nuevo.turno.id) == []


def test_si_esta_lejos_se_le_avisara_despues(sesion: Session):
    ocupar_puestos(sesion, "C1", "C2")
    turnos = [por_whatsapp(sesion) for _ in range(4)]
    assert [t.adelante for t in turnos] == [0, 1, 2, 3]
    assert [t.salir_ya for t in turnos] == [True, True, True, False]


def test_los_numeros_siguen_el_orden_y_cada_fila_lleva_el_suyo(sesion: Session):
    assert por_whatsapp(sesion).numero == "C-01"
    assert por_whatsapp(sesion).numero == "C-02"
    assert por_whatsapp(sesion, tipo="Moto").numero == "M-01"
    assert por_whatsapp(sesion, tipo="Camioneta").numero == "C-03"


def test_el_numero_vuelve_a_1_cada_dia(sesion: Session):
    por_whatsapp(sesion)
    assert por_whatsapp(sesion, ahora=LUNES_8AM + timedelta(days=1)).numero == "C-01"


def test_el_dia_del_turno_es_el_de_bogota(sesion: Session):
    # 9:00 p.m. del lunes en Bogotá ya es martes en UTC; el turno es del lunes.
    nuevo = en_el_local(sesion, ahora=lunes_a_las(time(21, 0)))
    assert nuevo.turno.fecha == date(2026, 10, 26)


@pytest.mark.parametrize("hora_local", [time(5, 29), time(19, 30), time(23, 0)])
def test_por_whatsapp_no_se_dan_turnos_fuera_de_horario(sesion: Session, hora_local: time):
    with pytest.raises(FueraDeHorario, match="de 5:30 a.m. a 7:30 p.m."):
        por_whatsapp(sesion, ahora=lunes_a_las(hora_local))


def test_el_horario_sale_de_la_configuracion(sesion: Session):
    sesion.get(Configuracion, "hora_cierre").valor = "20:00"
    sesion.flush()
    assert por_whatsapp(sesion, ahora=lunes_a_las(time(19, 45))).numero == "C-01"


def test_desde_el_dashboard_si_se_da_turno_despues_del_cierre(sesion: Session):
    assert en_el_local(sesion, ahora=lunes_a_las(time(19, 45))).numero == "C-01"


def test_un_turno_activo_por_telefono_en_cada_fila(sesion: Session):
    carro = cliente_con_vehiculo(sesion)
    primero = por_whatsapp(sesion, vehiculo=carro)
    with pytest.raises(YaTieneTurnoActivo) as error:
        por_whatsapp(sesion, vehiculo=carro)
    assert error.value.numero == "C-01"
    assert error.value.turno_id == primero.turno.id


def test_con_carro_y_moto_puede_tener_un_turno_en_cada_fila(sesion: Session):
    carro = cliente_con_vehiculo(sesion)
    moto = clientes.registrar_vehiculo(
        sesion, cliente_id=carro.cliente_id, tipo_vehiculo_id=tipo_vehiculo(sesion, "Moto").id
    )
    assert por_whatsapp(sesion, vehiculo=carro).numero == "C-01"
    assert por_whatsapp(sesion, vehiculo=moto).numero == "M-01"


def test_despues_de_cancelar_puede_pedir_otro(sesion: Session):
    carro = cliente_con_vehiculo(sesion)
    primero = por_whatsapp(sesion, vehiculo=carro)
    casos.cancelar(sesion, primero.turno.id, actor=Actor.CLIENTE, ahora=LUNES_8AM)
    assert por_whatsapp(sesion, vehiculo=carro).numero == "C-02"


def test_el_administrador_si_le_puede_dar_otro_turno(sesion: Session):
    carro = cliente_con_vehiculo(sesion)
    por_whatsapp(sesion, vehiculo=carro)
    assert en_el_local(sesion, vehiculo=carro).numero == "C-02"


def test_el_turno_creado_en_el_local_nace_como_llego(sesion: Session):
    nuevo = en_el_local(sesion)
    assert nuevo.turno.estado == E.LLEGO
    assert not nuevo.llamado  # ya está en el local: no hay que llamarlo


def test_servicio_por_definir_o_del_menu(sesion: Session):
    assert por_whatsapp(sesion).turno.servicio_id is None
    encerado = servicio(sesion, "Encerado")
    assert por_whatsapp(sesion, servicio_id=encerado.id).turno.servicio_id == encerado.id


def test_no_se_puede_pedir_un_servicio_de_la_otra_fila(sesion: Session):
    intermedio_de_motos = servicio(sesion, "Intermedio", "Motos")
    with pytest.raises(ServicioNoDisponible):
        por_whatsapp(sesion, servicio_id=intermedio_de_motos.id)


def test_queda_en_el_historial(sesion: Session):
    nuevo = por_whatsapp(sesion)
    pasos = historial(sesion, nuevo.turno.id)
    assert [(p.estado_anterior, p.estado_nuevo, p.actor) for p in pasos] == [
        (None, E.EN_FILA, Actor.CLIENTE),
        (E.EN_FILA, E.LLAMADO, Actor.SISTEMA),
    ]
    assert pasos[0].detalle == "Turno C-01"
    assert pasos[1].detalle == "Tiene hasta las 8:15 a.m."
