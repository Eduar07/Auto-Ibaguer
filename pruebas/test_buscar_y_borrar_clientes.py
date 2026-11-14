"""Clientes: buscarlos, su ficha, corregir sus datos y borrarlos a pedido (Ley 1581)."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.bot.tablas import SesionBot
from app.clientes import consultas
from app.clientes import servicio as clientes
from app.clientes.errores import NoExiste, TelefonoDeOtroCliente, TieneTurnoActivo
from app.clientes.tablas import Cliente, Consulta, TipoConsulta, Vehiculo
from app.integraciones.tablas import MensajeEntrante
from app.turnos import casos_de_uso as casos
from app.turnos.estados import Actor, EstadoTurno, MedioPago
from app.turnos.tablas import Pago, Turno, TurnoHistorial
from pruebas.fabricas import (
    LUNES_8AM,
    cliente_con_vehiculo,
    debe_rechazar,
    en_el_local,
    minutos,
    por_whatsapp,
    puesto,
    servicio,
)


def cobrado(sesion: Session, vehiculo: Vehiculo, *, nombre_servicio="Básico", **cobro) -> int:
    """Un turno de ese vehículo, lavado y cobrado. Devuelve el id del turno."""
    nuevo = en_el_local(sesion, vehiculo=vehiculo, servicio_id=servicio(sesion, nombre_servicio).id)
    casos.pasar_a_puesto(sesion, nuevo.turno.id, puesto(sesion, "C1").id, ahora=LUNES_8AM)
    casos.marcar_listo(sesion, nuevo.turno.id, ahora=LUNES_8AM + minutos(40))
    casos.cobrar(
        sesion,
        nuevo.turno.id,
        medio_pago=MedioPago.EFECTIVO,
        ahora=LUNES_8AM + minutos(45),
        **cobro,
    )
    return nuevo.turno.id


# --- Buscar -------------------------------------------------------------------------------


def test_busca_por_nombre_sin_importar_tildes_ni_mayusculas(sesion: Session):
    cliente_con_vehiculo(sesion, nombre="Andrés Pérez")
    cliente_con_vehiculo(sesion, nombre="Laura Gómez")

    nombres = [c.nombre for c in consultas.buscar_clientes(sesion, "ANDRES").clientes]

    assert nombres == ["Andrés Pérez"]


def test_busca_por_parte_del_celular_o_de_la_placa(sesion: Session):
    felipe = cliente_con_vehiculo(sesion, nombre="Felipe", placa="JKL221", telefono="3214564410")
    cliente_con_vehiculo(sesion, nombre="Otro", placa="ABC123")

    por_celular = consultas.buscar_clientes(sesion, "456 4410").clientes
    por_placa = consultas.buscar_clientes(sesion, "jkl-2").clientes

    assert [c.id for c in por_celular] == [felipe.cliente_id]
    assert [c.id for c in por_placa] == [felipe.cliente_id]
    assert por_placa[0].placas == ["JKL221"]


def test_los_de_visita_mas_reciente_van_primero_y_se_pagina(sesion: Session):
    sin_visitas = cliente_con_vehiculo(sesion, nombre="Sin Visitas")
    con_visita = cliente_con_vehiculo(sesion, nombre="Con Visita")
    en_el_local(sesion, vehiculo=con_visita)

    pagina = consultas.buscar_clientes(sesion, por_pagina=1)

    assert pagina.total == 2
    assert pagina.clientes[0].nombre == "Con Visita"
    assert pagina.clientes[0].visitas == 1
    assert pagina.hay_siguiente and not pagina.hay_anterior
    segunda = consultas.buscar_clientes(sesion, pagina=2, por_pagina=1)
    assert [c.id for c in segunda.clientes] == [sin_visitas.cliente_id]
    assert segunda.clientes[0].ultima_visita is None


def test_un_turno_cancelado_no_cuenta_como_visita(sesion: Session):
    vehiculo = cliente_con_vehiculo(sesion)
    nuevo = por_whatsapp(sesion, vehiculo=vehiculo)
    casos.cancelar(sesion, nuevo.turno.id, actor=Actor.CLIENTE, ahora=LUNES_8AM)

    assert consultas.buscar_clientes(sesion).clientes[0].visitas == 0


# --- Ficha ----------------------------------------------------------------------------------


@pytest.mark.usefixtures("caja_del_lavadero")
def test_la_ficha_suma_visitas_lo_pagado_y_lo_que_pide_casi_siempre(sesion: Session):
    vehiculo = cliente_con_vehiculo(sesion, nombre="Felipe", placa="JKL221")
    cobrado(sesion, vehiculo, nombre_servicio="Encerado")
    cobrado(sesion, vehiculo, nombre_servicio="Encerado")
    cobrado(sesion, vehiculo, nombre_servicio="Básico")
    activo = en_el_local(sesion, vehiculo=vehiculo)

    ficha = consultas.ficha_del_cliente(sesion, vehiculo.cliente_id)

    assert ficha.visitas == 4
    assert ficha.total_pagado == 60_000 + 60_000 + 40_000
    assert ficha.servicio_frecuente == "Encerado"
    assert [(v.tipo, v.placa) for v in ficha.vehiculos] == [("Automóvil", "JKL221")]
    assert ficha.turno_activo == activo.numero
    assert ficha.ultimas_visitas[0].numero == activo.numero  # lo más reciente primero
    assert ficha.ultimas_visitas[-1].cobrado == 60_000
    assert ficha.escribio_por_whatsapp is False


def test_la_ficha_sabe_si_el_cliente_escribio_por_whatsapp(sesion: Session):
    nuevo = por_whatsapp(sesion)

    ficha = consultas.ficha_del_cliente(sesion, nuevo.turno.cliente_id)

    assert ficha.escribio_por_whatsapp is True


# --- Editar ---------------------------------------------------------------------------------


def test_editar_nombre_y_celular(sesion: Session):
    vehiculo = cliente_con_vehiculo(sesion, nombre="felipe", telefono="3001112233")

    cliente = clientes.editar_cliente(
        sesion, vehiculo.cliente_id, nombre="  felipe  OSORIO ", telefono="321 456 4410"
    )

    assert (cliente.nombre, cliente.telefono) == ("Felipe Osorio", "573214564410")


def test_editar_sin_celular_lo_deja_sin_avisos(sesion: Session):
    vehiculo = cliente_con_vehiculo(sesion, telefono="3001112233")

    cliente = clientes.editar_cliente(sesion, vehiculo.cliente_id, nombre="Ana", telefono=" ")

    assert cliente.telefono is None


def test_un_celular_no_puede_quedar_en_dos_clientes(sesion: Session):
    cliente_con_vehiculo(sesion, nombre="Laura", telefono="3001112233")
    otro = cliente_con_vehiculo(sesion, nombre="Paola", telefono="3004445566")

    with pytest.raises(TelefonoDeOtroCliente, match="Laura"):
        clientes.editar_cliente(sesion, otro.cliente_id, nombre="Paola", telefono="300 111 2233")


# --- Borrar datos (Ley 1581) -----------------------------------------------------------------


def test_no_se_borran_los_datos_de_quien_tiene_un_turno_en_la_fila(sesion: Session):
    nuevo = por_whatsapp(sesion)

    with pytest.raises(TieneTurnoActivo, match=nuevo.numero):
        clientes.borrar_datos(sesion, nuevo.turno.cliente_id)


@pytest.mark.usefixtures("caja_del_lavadero")
def test_borrar_datos_quita_lo_personal_y_deja_la_contabilidad(sesion: Session):
    vehiculo = cliente_con_vehiculo(
        sesion, nombre="Felipe Osorio", placa="JKL221", telefono="3214564410"
    )
    cliente_id = vehiculo.cliente_id
    telefono = sesion.get(Cliente, cliente_id).telefono
    turno_id = cobrado(sesion, vehiculo, precio_cobrado=35_000, motivo="Felipe es frecuente")
    sesion.add(
        TurnoHistorial(
            turno_id=turno_id,
            estado_anterior=EstadoTurno.PAGADO,
            estado_nuevo=EstadoTurno.PAGADO,
            actor=Actor.ADMINISTRADOR,
            detalle="Placa: JKL221",
        )
    )
    sesion.add(
        MensajeEntrante(
            id_meta="wamid.1", telefono=telefono, tipo="text", contenido={"texto": "hola"}
        )
    )
    sesion.add(
        SesionBot(
            telefono=telefono,
            cliente_id=cliente_id,
            paso_actual="menu",
            expira_en=datetime.now(UTC),
        )
    )
    sesion.add(Consulta(cliente_id=cliente_id, tipo=TipoConsulta.PRECIOS))
    sesion.flush()

    nombre = clientes.borrar_datos(sesion, cliente_id, ahora=LUNES_8AM)
    sesion.expire_all()

    cliente = sesion.get(Cliente, cliente_id)
    assert nombre == "Felipe Osorio"
    assert (cliente.nombre, cliente.telefono, cliente.acepta_datos) == (
        "Cliente borrado",
        None,
        False,
    )
    assert cliente.datos_borrados_en == LUNES_8AM
    assert sesion.get(Vehiculo, vehiculo.id).placa is None
    # La contabilidad sigue: el turno y el cobro quedan, sin los datos.
    assert sesion.get(Turno, turno_id).estado == EstadoTurno.PAGADO
    pago = sesion.scalars(select(Pago).where(Pago.turno_id == turno_id)).one()
    assert (pago.precio_cobrado, pago.motivo_ajuste) == (35_000, "[borrado] es frecuente")
    detalles = sesion.scalars(
        select(TurnoHistorial.detalle).where(TurnoHistorial.turno_id == turno_id)
    ).all()
    assert "Placa: [borrado]" in detalles
    assert not any("JKL221" in (detalle or "") for detalle in detalles)
    # Lo que se escribió por WhatsApp se va; las consultas quedan sin saber de quién.
    assert sesion.scalars(select(MensajeEntrante)).first() is None
    assert sesion.scalars(select(SesionBot)).first() is None
    assert sesion.scalars(select(Consulta.cliente_id)).one() is None


def test_un_cliente_borrado_ya_no_aparece_ni_se_puede_abrir(sesion: Session):
    vehiculo = cliente_con_vehiculo(sesion, nombre="Felipe", placa="JKL221")
    clientes.borrar_datos(sesion, vehiculo.cliente_id)

    assert consultas.buscar_clientes(sesion).total == 0
    assert consultas.contar_clientes(sesion) == 0
    with pytest.raises(NoExiste):
        consultas.ficha_del_cliente(sesion, vehiculo.cliente_id)
    # Su placa queda libre: si vuelve, se registra como cliente nuevo.
    nuevo = cliente_con_vehiculo(sesion, nombre="Felipe", placa="JKL221")
    assert nuevo.cliente_id != vehiculo.cliente_id


def test_la_base_no_acepta_un_cliente_borrado_con_celular(sesion: Session):
    vehiculo = cliente_con_vehiculo(sesion, telefono="3001112233")
    cliente = sesion.get(Cliente, vehiculo.cliente_id)

    with debe_rechazar(sesion, "ck_cliente_borrado_sin_telefono"):
        cliente.datos_borrados_en = LUNES_8AM
