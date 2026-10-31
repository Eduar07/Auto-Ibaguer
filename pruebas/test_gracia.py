"""Tiempo de gracia: 15 minutos para llegar. La primera vez pasa al final; la segunda pierde
el turno. Esto lo hará el reloj solo, cada 30 segundos (hito 6)."""

from sqlalchemy.orm import Session

from app.turnos import casos_de_uso as casos
from app.turnos.estados import Actor, EstadoTurno, TipoAviso
from app.turnos.tablas import Turno
from pruebas.fabricas import (
    LUNES_8AM,
    avisos,
    estado,
    historial,
    minutos,
    ocupar_puestos,
    por_whatsapp,
    tipos_de_aviso,
)

E = EstadoTurno


def test_antes_de_los_15_minutos_no_pasa_nada(sesion: Session):
    nuevo = por_whatsapp(sesion)
    assert casos.vencer_gracias(sesion, ahora=LUNES_8AM + minutos(14)) == []
    assert estado(sesion, nuevo.turno.id) == E.LLAMADO


def test_primera_vez_sin_llegar_pasa_al_final_y_la_fila_avanza(sesion: Session):
    ocupar_puestos(sesion, "C2")  # C-01
    tarde = por_whatsapp(sesion).turno.id  # C-02, llamado para C1 hasta las 8:15
    siguiente = por_whatsapp(sesion).turno.id  # C-03

    resultado = casos.vencer_gracias(sesion, ahora=LUNES_8AM + minutos(15))

    assert resultado == [casos.GraciaVencida(tarde, "C-02", E.EN_FILA)]
    turno = sesion.get(Turno, tarde)
    assert turno.estado == E.EN_FILA
    assert turno.intentos_sin_llegar == 1
    assert turno.en_fila_desde == LUNES_8AM + minutos(15)
    assert turno.gracia_hasta is None
    # La fila avanza: C-03 queda primero y se llama.
    assert estado(sesion, siguiente) == E.LLAMADO
    assert tipos_de_aviso(sesion, siguiente) == [TipoAviso.LLAMADO]
    # Al que llegó tarde se le avisa cuántos tiene adelante; ese aviso ya le pide salir.
    [aviso] = avisos(sesion, turno_id=tarde)
    assert aviso.tipo == TipoAviso.PASA_AL_FINAL
    assert aviso.contenido == {"turno": "C-02", "adelante": 1}


def test_segunda_vez_sin_llegar_pierde_el_turno(sesion: Session):
    nuevo = por_whatsapp(sesion)  # solo en la fila, con puestos libres

    casos.vencer_gracias(sesion, ahora=LUNES_8AM + minutos(15))
    # Pasa al final, pero como no hay nadie más, se llama otra vez de inmediato.
    turno = sesion.get(Turno, nuevo.turno.id)
    assert turno.estado == E.LLAMADO
    assert turno.intentos_sin_llegar == 1
    assert turno.gracia_hasta == LUNES_8AM + minutos(30)
    assert tipos_de_aviso(sesion, nuevo.turno.id) == [TipoAviso.PASA_AL_FINAL, TipoAviso.LLAMADO]

    resultado = casos.vencer_gracias(sesion, ahora=LUNES_8AM + minutos(30))
    assert resultado == [casos.GraciaVencida(nuevo.turno.id, "C-01", E.NO_LLEGO)]
    assert turno.estado == E.NO_LLEGO
    assert turno.intentos_sin_llegar == 2
    assert tipos_de_aviso(sesion, nuevo.turno.id)[-1] == TipoAviso.NO_LLEGO
    ultimo = historial(sesion, nuevo.turno.id)[-1]
    assert (ultimo.estado_anterior, ultimo.estado_nuevo, ultimo.actor) == (
        E.LLAMADO,
        E.NO_LLEGO,
        Actor.SISTEMA,
    )


def test_si_llega_a_tiempo_no_pierde_nada(sesion: Session):
    nuevo = por_whatsapp(sesion)
    casos.marcar_llegada(sesion, nuevo.turno.id, actor=Actor.CLIENTE, ahora=LUNES_8AM + minutos(10))
    assert casos.vencer_gracias(sesion, ahora=LUNES_8AM + minutos(20)) == []
    assert estado(sesion, nuevo.turno.id) == E.LLEGO


def test_quien_paso_al_final_recibe_otra_vez_el_aviso_al_acercarse(sesion: Session):
    ocupar_puestos(sesion, "C2")
    tarde = por_whatsapp(sesion).turno.id  # C-02, llamado para C1
    otros = [por_whatsapp(sesion).turno.id for _ in range(3)]  # C-03, C-04, C-05

    casos.vencer_gracias(sesion, ahora=LUNES_8AM + minutos(15))
    # Quedó con 3 adelante (C-03 llamado, C-04, C-05): solo se le avisa que pasó al final.
    assert tipos_de_aviso(sesion, tarde) == [TipoAviso.PASA_AL_FINAL]
    assert avisos(sesion, turno_id=tarde)[0].contenido["adelante"] == 3

    # Cuando C-03 cancela, le quedan 2 adelante: ahora sí recibe el aviso anticipado.
    casos.cancelar(sesion, otros[0], actor=Actor.CLIENTE, ahora=LUNES_8AM + minutos(20))
    assert tipos_de_aviso(sesion, tarde) == [TipoAviso.PASA_AL_FINAL, TipoAviso.AVISO_ANTICIPADO]
