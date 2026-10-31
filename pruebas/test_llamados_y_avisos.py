"""Llamados y avisos: la fila avanza sola y le avisa a cada cliente en el momento justo."""

import pytest
from sqlalchemy.orm import Session

from app.turnos import casos_de_uso as casos
from app.turnos import consultas
from app.turnos.errores import CambioNoPermitido, PuestoNoDisponible
from app.turnos.estados import Actor, EstadoTurno, TipoAviso
from app.turnos.tablas import Turno
from pruebas.fabricas import (
    LUNES_8AM,
    avisos,
    en_el_local,
    estado,
    fila,
    historial,
    minutos,
    ocupar_puestos,
    por_whatsapp,
    puesto,
    tipos_de_aviso,
)

E = EstadoTurno


def test_la_fila_avanza_con_los_avisos_del_documento(sesion: Session):
    c01, c02 = ocupar_puestos(sesion, "C1", "C2")  # lavándose desde las 8:00
    c03, c04, c05, c06 = [por_whatsapp(sesion).turno.id for _ in range(4)]
    # Al pedir, C-03, C-04 y C-05 quedaron cerca: la confirmación ya les dijo que salieran.
    assert avisos(sesion) == []

    # 9:00 — C-01 queda listo: se le avisa, el puesto se libera y se llama a C-03.
    casos.marcar_listo(sesion, c01, ahora=LUNES_8AM + minutos(60))
    assert tipos_de_aviso(sesion, c01) == [TipoAviso.VEHICULO_LISTO]
    assert estado(sesion, c03) == E.LLAMADO
    assert tipos_de_aviso(sesion, c03) == [TipoAviso.LLAMADO]
    assert tipos_de_aviso(sesion, c06) == []  # todavía tiene 3 adelante

    # 9:05 — C-03 pasa a C1: a C-06 le faltan 2 turnos.
    casos.pasar_a_puesto(sesion, c03, puesto(sesion, "C1").id, ahora=LUNES_8AM + minutos(65))
    [aviso] = avisos(sesion, turno_id=c06)
    assert aviso.tipo == TipoAviso.AVISO_ANTICIPADO
    assert aviso.contenido == {"turno": "C-06", "adelante": 2}
    assert estado(sesion, c04) == E.EN_FILA  # no hay puesto libre: nadie más es llamado
    assert estado(sesion, c02) == E.EN_LAVADO


def test_el_aviso_de_llamado_trae_la_hora_limite(sesion: Session):
    (c01,) = ocupar_puestos(sesion, "C1")
    por_whatsapp(sesion)  # C-02: llamado para C2 en la confirmación
    c03 = por_whatsapp(sesion).turno.id
    casos.marcar_listo(sesion, c01, ahora=LUNES_8AM + minutos(40))
    [aviso] = avisos(sesion, turno_id=c03, tipo=TipoAviso.LLAMADO)
    assert aviso.contenido["turno"] == "C-03"
    assert aviso.contenido["gracia_hasta"] == (LUNES_8AM + minutos(55)).isoformat()
    assert sesion.get(Turno, c03).gracia_hasta == LUNES_8AM + minutos(55)


def test_cancelar_llama_al_siguiente_de_inmediato(sesion: Session):
    ocupar_puestos(sesion, "C2")
    primero = por_whatsapp(sesion)  # llamado para C1
    segundo = por_whatsapp(sesion)
    assert primero.llamado and not segundo.llamado

    casos.cancelar(sesion, primero.turno.id, actor=Actor.CLIENTE, ahora=LUNES_8AM + minutos(5))
    assert estado(sesion, segundo.turno.id) == E.LLAMADO
    assert sesion.get(Turno, segundo.turno.id).gracia_hasta == LUNES_8AM + minutos(20)
    assert tipos_de_aviso(sesion, segundo.turno.id) == [TipoAviso.LLAMADO]


def test_por_whatsapp_no_se_cancela_un_turno_que_ya_llego(sesion: Session):
    nuevo = por_whatsapp(sesion)
    casos.marcar_llegada(sesion, nuevo.turno.id, actor=Actor.CLIENTE, ahora=LUNES_8AM)
    with pytest.raises(CambioNoPermitido, match="se resuelve en el local"):
        casos.cancelar(sesion, nuevo.turno.id, actor=Actor.CLIENTE, ahora=LUNES_8AM)
    casos.cancelar(sesion, nuevo.turno.id, actor=Actor.ADMINISTRADOR, ahora=LUNES_8AM)
    assert estado(sesion, nuevo.turno.id) == E.CANCELADO


def test_el_puesto_no_espera_al_llamado(sesion: Session):
    (c01,) = ocupar_puestos(sesion, "C2")
    llamado = por_whatsapp(sesion)  # C-02, llamado para C1, todavía no llega
    del_local = en_el_local(sesion)  # C-03, ya está en el local
    carros = fila(sesion, "Carros").id
    assert consultas.siguiente_para_puesto(sesion, carros) == del_local.turno.id

    casos.pasar_a_puesto(sesion, del_local.turno.id, puesto(sesion, "C1").id, ahora=LUNES_8AM)
    # El llamado conserva su tiempo de gracia y entra al siguiente puesto libre.
    assert estado(sesion, llamado.turno.id) == E.LLAMADO
    assert sesion.get(Turno, llamado.turno.id).gracia_hasta == LUNES_8AM + minutos(15)

    casos.marcar_llegada(
        sesion, llamado.turno.id, actor=Actor.CLIENTE, ahora=LUNES_8AM + minutos(10)
    )
    casos.marcar_listo(sesion, c01, ahora=LUNES_8AM + minutos(50))
    assert consultas.siguiente_para_puesto(sesion, carros) == llamado.turno.id
    casos.pasar_a_puesto(
        sesion, llamado.turno.id, puesto(sesion, "C2").id, ahora=LUNES_8AM + minutos(51)
    )
    assert estado(sesion, llamado.turno.id) == E.EN_LAVADO


def test_si_nadie_ha_llegado_el_puesto_espera_al_llamado(sesion: Session):
    por_whatsapp(sesion)
    assert consultas.siguiente_para_puesto(sesion, fila(sesion, "Carros").id) is None


def test_un_puesto_no_recibe_dos_vehiculos(sesion: Session):
    uno, dos = en_el_local(sesion), en_el_local(sesion)
    c1 = puesto(sesion, "C1").id
    casos.pasar_a_puesto(sesion, uno.turno.id, c1, ahora=LUNES_8AM)
    with pytest.raises(PuestoNoDisponible, match="C1 está ocupado con el turno C-01"):
        casos.pasar_a_puesto(sesion, dos.turno.id, c1, ahora=LUNES_8AM)


def test_no_se_pasa_a_un_puesto_apagado_ni_de_la_otra_fila(sesion: Session):
    carro = en_el_local(sesion).turno.id
    puesto(sesion, "C1").activo = False
    sesion.flush()
    with pytest.raises(PuestoNoDisponible, match="C1 está apagado"):
        casos.pasar_a_puesto(sesion, carro, puesto(sesion, "C1").id, ahora=LUNES_8AM)
    with pytest.raises(PuestoNoDisponible, match="no es de la fila"):
        casos.pasar_a_puesto(sesion, carro, puesto(sesion, "M1").id, ahora=LUNES_8AM)


def test_un_puesto_apagado_no_llama_a_nadie(sesion: Session):
    puesto(sesion, "C1").activo = False
    puesto(sesion, "C2").activo = False
    sesion.flush()
    nuevo = por_whatsapp(sesion)
    assert not nuevo.llamado
    assert nuevo.espera_min is None  # sin puestos activos no se puede estimar


def test_primero_debe_llegar_o_ser_llamado(sesion: Session):
    ocupar_puestos(sesion, "C1")
    por_whatsapp(sesion)  # C-02: llamado para C2
    en_fila = por_whatsapp(sesion)  # C-03: sigue En fila
    with pytest.raises(CambioNoPermitido, match="«En fila» no puede pasar a «En lavado»"):
        casos.pasar_a_puesto(sesion, en_fila.turno.id, puesto(sesion, "C2").id, ahora=LUNES_8AM)


def test_llegar_antes_de_que_lo_llamen(sesion: Session):
    ocupar_puestos(sesion, "C1", "C2")
    nuevo = por_whatsapp(sesion)
    casos.marcar_en_camino(sesion, nuevo.turno.id)
    casos.marcar_llegada(sesion, nuevo.turno.id, actor=Actor.CLIENTE, ahora=LUNES_8AM)
    turno = sesion.get(Turno, nuevo.turno.id)
    assert turno.estado == E.LLEGO
    assert not turno.en_camino
    # Escribir "Ya estoy aquí" otra vez no cambia nada ni deja otro registro.
    pasos = len(historial(sesion, nuevo.turno.id))
    casos.marcar_llegada(sesion, nuevo.turno.id, actor=Actor.CLIENTE, ahora=LUNES_8AM)
    assert len(historial(sesion, nuevo.turno.id)) == pasos


def test_en_camino_es_una_marca_y_no_mueve_la_fila(sesion: Session):
    ocupar_puestos(sesion, "C1", "C2")
    primero, segundo = por_whatsapp(sesion), por_whatsapp(sesion)
    casos.marcar_en_camino(sesion, segundo.turno.id)
    turno = sesion.get(Turno, segundo.turno.id)
    assert turno.en_camino
    assert turno.estado == E.EN_FILA
    posicion = consultas.posicion_del_turno(sesion, segundo.turno.id, ahora=LUNES_8AM)
    assert posicion.adelante == 1
    assert posicion.en_camino

    casos.marcar_llegada(sesion, primero.turno.id, actor=Actor.CLIENTE, ahora=LUNES_8AM)
    with pytest.raises(CambioNoPermitido, match="En fila o Llamado"):
        casos.marcar_en_camino(sesion, primero.turno.id)


def test_sin_celular_no_hay_avisos(sesion: Session):
    sin_celular = en_el_local(sesion, sin_telefono=True).turno.id
    casos.pasar_a_puesto(sesion, sin_celular, puesto(sesion, "C1").id, ahora=LUNES_8AM)
    casos.marcar_listo(sesion, sin_celular, ahora=LUNES_8AM + minutos(45))
    assert avisos(sesion, turno_id=sin_celular) == []
    assert estado(sesion, sin_celular) == E.LISTO


def test_cada_cambio_queda_en_el_historial(sesion: Session):
    nuevo = en_el_local(sesion)
    turno = nuevo.turno.id
    casos.pasar_a_puesto(sesion, turno, puesto(sesion, "C2").id, ahora=LUNES_8AM + minutos(1))
    casos.marcar_listo(sesion, turno, ahora=LUNES_8AM + minutos(53))
    pasos = historial(sesion, turno)
    assert [(p.estado_anterior, p.estado_nuevo) for p in pasos] == [
        (None, E.LLEGO),
        (E.LLEGO, E.EN_LAVADO),
        (E.EN_LAVADO, E.LISTO),
    ]
    assert pasos[1].detalle == "Puesto C2"
    assert pasos[2].detalle == "Lavado en 52 min"
    assert pasos[2].fecha_hora == LUNES_8AM + minutos(53)
