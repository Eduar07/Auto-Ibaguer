"""Marcas pendientes y correcciones: el administrador completa el turno al recibir el vehículo."""

import pytest
from sqlalchemy.orm import Session

from app.clientes import servicio as clientes
from app.clientes.errores import PlacaDeOtroCliente, PlacaInvalida
from app.clientes.tablas import Vehiculo
from app.turnos import casos_de_uso as casos
from app.turnos.errores import CambioNoPermitido, VehiculoDeOtraFila
from app.turnos.estados import Actor, EstadoTurno, MedioPago
from app.turnos.tablas import Turno
from pruebas.fabricas import (
    LUNES_8AM,
    cliente_con_vehiculo,
    en_el_local,
    historial,
    minutos,
    por_whatsapp,
    puesto,
    servicio,
    tipo_vehiculo,
)


def test_completar_placa_y_servicio_al_recibir(sesion: Session):
    nuevo = por_whatsapp(sesion)  # placa pendiente y servicio por definir
    encerado = servicio(sesion, "Encerado")
    casos.corregir_turno(
        sesion, nuevo.turno.id, servicio_id=encerado.id, placa="abc123", ahora=LUNES_8AM
    )
    turno = sesion.get(Turno, nuevo.turno.id)
    assert turno.servicio_id == encerado.id
    assert sesion.get(Vehiculo, turno.vehiculo_id).placa == "ABC123"
    ultimo = historial(sesion, turno.id)[-1]
    assert ultimo.estado_anterior == ultimo.estado_nuevo == EstadoTurno.LLAMADO
    assert ultimo.detalle == "Placa: ABC123; Servicio: por definir → Encerado"


def test_corregir_automovil_por_camioneta_cambia_el_precio(sesion: Session):
    nuevo = en_el_local(sesion, placa="ABC123", servicio_id=servicio(sesion).id)
    turno = nuevo.turno.id
    casos.corregir_turno(
        sesion, turno, tipo_vehiculo_id=tipo_vehiculo(sesion, "Camioneta").id, ahora=LUNES_8AM
    )
    casos.pasar_a_puesto(sesion, turno, puesto(sesion, "C1").id, ahora=LUNES_8AM)
    casos.marcar_listo(sesion, turno, ahora=LUNES_8AM + minutos(55))
    pago = casos.cobrar(sesion, turno, medio_pago=MedioPago.EFECTIVO, ahora=LUNES_8AM)
    assert pago.precio_menu == 50_000
    assert "Vehículo: Automóvil → Camioneta" in [p.detalle for p in historial(sesion, turno)]


def test_un_carro_no_se_puede_pasar_a_la_fila_de_motos(sesion: Session):
    nuevo = por_whatsapp(sesion)
    with pytest.raises(VehiculoDeOtraFila, match="otra fila"):
        casos.corregir_turno(
            sesion, nuevo.turno.id, tipo_vehiculo_id=tipo_vehiculo(sesion, "Moto").id
        )


def test_la_placa_debe_tener_el_formato_del_vehiculo(sesion: Session):
    nuevo = por_whatsapp(sesion)
    with pytest.raises(PlacaInvalida):
        casos.corregir_turno(sesion, nuevo.turno.id, placa="ABC12D")  # placa de moto


def test_la_placa_de_otro_cliente_se_rechaza(sesion: Session):
    cliente_con_vehiculo(sesion, placa="ABC123")
    nuevo = por_whatsapp(sesion)
    with pytest.raises(PlacaDeOtroCliente):
        casos.corregir_turno(sesion, nuevo.turno.id, placa="ABC123")


def test_si_la_placa_es_de_otro_carro_del_mismo_cliente_se_usa_ese(sesion: Session):
    con_placa = cliente_con_vehiculo(sesion, placa="ABC123")
    sin_placa = clientes.registrar_vehiculo(
        sesion,
        cliente_id=con_placa.cliente_id,
        tipo_vehiculo_id=tipo_vehiculo(sesion, "Camioneta").id,
    )
    nuevo = por_whatsapp(sesion, vehiculo=sin_placa)
    casos.corregir_turno(sesion, nuevo.turno.id, placa="ABC123")
    assert sesion.get(Turno, nuevo.turno.id).vehiculo_id == con_placa.id


def test_cambiar_la_placa_no_reescribe_los_turnos_viejos(sesion: Session):
    viejo = cliente_con_vehiculo(sesion, placa="ABC123")
    nuevo = en_el_local(sesion, vehiculo=viejo)
    casos.corregir_turno(sesion, nuevo.turno.id, placa="XYZ789")
    assert sesion.get(Vehiculo, viejo.id).placa == "ABC123"
    assert sesion.get(Vehiculo, sesion.get(Turno, nuevo.turno.id).vehiculo_id).placa == "XYZ789"


def test_un_turno_terminado_no_se_corrige(sesion: Session):
    nuevo = por_whatsapp(sesion)
    casos.cancelar(sesion, nuevo.turno.id, actor=Actor.CLIENTE, ahora=LUNES_8AM)
    with pytest.raises(CambioNoPermitido, match="ya terminó"):
        casos.corregir_turno(sesion, nuevo.turno.id, servicio_id=servicio(sesion).id)
