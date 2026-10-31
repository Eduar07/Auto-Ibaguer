"""Ver la fila y "mi turno": lo que el bot le muestra al cliente (solo cantidades)."""

from sqlalchemy.orm import Session

from app.clientes import servicio as clientes
from app.turnos import consultas, datos
from app.turnos.estados import EstadoTurno
from pruebas.fabricas import (
    LUNES_8AM,
    cliente_con_vehiculo,
    fila,
    minutos,
    ocupar_puestos,
    por_whatsapp,
    servicio,
    tipo_vehiculo,
)

E = EstadoTurno


def resumen(sesion: Session, ahora=LUNES_8AM):
    return consultas.resumen_de_la_fila(sesion, fila(sesion, "Carros").id, ahora=ahora)


def test_hay_puesto_libre_puede_venir_ya(sesion: Session):
    fila_de_carros = resumen(sesion)
    assert fila_de_carros.puede_venir_ya
    assert (fila_de_carros.puestos_libres, fila_de_carros.en_espera) == (2, 0)
    assert fila_de_carros.espera_min == 0


def test_puestos_ocupados_y_sin_fila_dice_cuando_se_libera_uno(sesion: Session):
    ocupar_puestos(sesion, "C1", ahora=LUNES_8AM)  # sale a las 9:00
    ocupar_puestos(sesion, "C2", ahora=LUNES_8AM + minutos(30))  # sale a las 9:30
    fila_de_carros = resumen(sesion, ahora=LUNES_8AM + minutos(40))
    assert not fila_de_carros.puede_venir_ya
    assert fila_de_carros.en_espera == 0
    assert fila_de_carros.espera_min == 20


def test_con_fila_dice_cuantos_hay_adelante_y_la_espera(sesion: Session):
    ocupar_puestos(sesion, "C1", ahora=LUNES_8AM)  # 9:00
    ocupar_puestos(sesion, "C2", ahora=LUNES_8AM + minutos(30))  # 9:30
    for _ in range(3):
        por_whatsapp(sesion, ahora=LUNES_8AM + minutos(35))
    fila_de_carros = resumen(sesion, ahora=LUNES_8AM + minutos(40))
    assert fila_de_carros.en_espera == 3
    # Entran a las 9:00, 9:30 y 10:00; uno nuevo entraría a las 10:30: 110 min desde las 8:40.
    assert fila_de_carros.espera_min == 110


def test_la_espera_usa_la_duracion_de_cada_servicio(sesion: Session):
    automovil = tipo_vehiculo(sesion, "Automóvil").id
    datos.precio_vigente(sesion, servicio(sesion, "Básico").id, automovil).duracion_min = 30
    datos.precio_vigente(sesion, servicio(sesion, "Empelotado").id, automovil).duracion_min = 240
    sesion.flush()
    ocupar_puestos(sesion, "C1", "C2")  # servicio por definir: cuenta como Básico, 30 min
    assert resumen(sesion).espera_min == 30
    por_whatsapp(sesion, servicio_id=servicio(sesion, "Empelotado").id)  # entra a las 8:30
    por_whatsapp(sesion)  # entra a las 8:30 en el otro puesto
    # El siguiente espera a que el básico termine (9:00), no al empelotado (12:30).
    assert resumen(sesion).espera_min == 60


def test_mi_turno_muestra_el_de_carros_y_el_de_motos(sesion: Session):
    carro = cliente_con_vehiculo(sesion)
    moto = clientes.registrar_vehiculo(
        sesion, cliente_id=carro.cliente_id, tipo_vehiculo_id=tipo_vehiculo(sesion, "Moto").id
    )
    ocupar_puestos(sesion, "C1", "C2")
    por_whatsapp(sesion, vehiculo=carro)
    por_whatsapp(sesion, vehiculo=moto)

    posiciones = consultas.turnos_activos_del_cliente(sesion, carro.cliente_id, ahora=LUNES_8AM)

    assert [(p.numero, p.estado, p.adelante) for p in posiciones] == [
        ("C-03", E.EN_FILA, 0),
        ("M-01", E.LLAMADO, 0),
    ]
    carro_en_fila, moto_llamada = posiciones
    assert carro_en_fila.espera_min == 60
    assert carro_en_fila.placa_pendiente
    assert carro_en_fila.servicio_por_definir
    assert carro_en_fila.gracia_hasta is None
    assert moto_llamada.gracia_hasta == LUNES_8AM + minutos(15)
