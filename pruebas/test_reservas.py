"""Reservar un día y una hora (casos de uso, el reloj y los avisos).

Lo que tiene que cumplirse siempre:
- en cada horario caben tantas reservas como puestos prendidos tenga la fila;
- nunca se reserva "muy encima" (media hora antes, mínimo), fuera del horario ni más allá
  de los días que deja Configuración;
- a su hora, la reserva entra a la fila de primero; si hay puesto, se le llama;
- el puesto que necesita una reserva que viene no se lo lleva alguien de la fila;
- si no llega a su hora, pierde la reserva y pasa al final, como cualquiera;
- se puede cambiar la hora o el día (también quien estaba en la fila) hasta que llega;
- el recordatorio sale una vez, solo si WhatsApp lo deja.
"""

import importlib
from datetime import datetime, time, timedelta

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.bot.avisos import mensaje_del_aviso
from app.catalogo.parametros import leer_parametros
from app.catalogo.tablas import Configuracion
from app.integraciones.tablas import MensajeEntrante, MensajeSaliente
from app.turnos import avisos, consultas
from app.turnos import casos_de_uso as casos
from app.turnos.errores import CambioNoPermitido, HorarioNoDisponible, YaTieneTurnoActivo
from app.turnos.estados import Actor, EstadoTurno, OrigenTurno, TipoAviso
from app.turnos.tablas import Turno, TurnoHistorial
from pruebas.fabricas import (
    BOGOTA,
    LUNES_8AM,
    cliente_con_vehiculo,
    fila,
    ocupar_puestos,
    por_whatsapp,
    puesto,
)

E = EstadoTurno
CELULAR = "573001234567"
MARTES = LUNES_8AM.date() + timedelta(days=1)


def el(dia, hora: int, minuto: int = 0) -> datetime:
    return datetime.combine(dia, time(hora, minuto), BOGOTA)


def hoy_a_las(hora: int, minuto: int = 0) -> datetime:
    return el(LUNES_8AM.date(), hora, minuto)


def reservar(sesion: Session, hora: datetime, *, vehiculo=None, tipo="Automóvil", ahora=LUNES_8AM):
    vehiculo = vehiculo or cliente_con_vehiculo(sesion, tipo=tipo)
    return casos.reservar(
        sesion,
        vehiculo_id=vehiculo.id,
        hora_elegida=hora,
        origen=OrigenTurno.WHATSAPP,
        ahora=ahora,
    )


def libres(sesion: Session, dia, *, ahora=LUNES_8AM, fila_nombre="Carros") -> list[datetime]:
    return casos.horarios_libres(
        sesion,
        fila_id=fila(sesion, fila_nombre).id,
        fecha=dia,
        duracion=timedelta(hours=1),
        ahora=ahora,
    )


def historial(sesion: Session, turno_id: int) -> list[str]:
    consulta = (
        select(TurnoHistorial.detalle)
        .where(TurnoHistorial.turno_id == turno_id)
        .order_by(TurnoHistorial.id)
    )
    return [detalle or "" for detalle in sesion.scalars(consulta)]


def leer(sesion: Session, turno_id: int) -> Turno:
    consulta = select(Turno).where(Turno.id == turno_id)
    return sesion.scalars(consulta.execution_options(populate_existing=True)).one()


# --- Reservar --------------------------------------------------------------------------------


def test_reservar_deja_el_turno_reservado_para_esa_hora(sesion: Session):
    reserva = reservar(sesion, el(MARTES, 10, 30))

    turno = leer(sesion, reserva.turno.id)
    assert (turno.estado, turno.fecha, turno.hora_reservada) == (
        E.RESERVADO,
        MARTES,
        el(MARTES, 10, 30),
    )
    assert reserva.numero == "C-01"  # el primero del martes
    assert historial(sesion, turno.id) == ["Turno C-01, reservado para mañana a las 10:30 a.m."]
    # Mientras no llega su hora, no está en la fila.
    assert consultas.resumen_de_la_fila(sesion, fila(sesion).id, ahora=LUNES_8AM).en_espera == 0


def test_en_cada_horario_caben_tantos_como_puestos(sesion: Session):
    reservar(sesion, el(MARTES, 10, 30))
    reservar(sesion, el(MARTES, 10, 30))  # Carros tiene 2 puestos

    with pytest.raises(HorarioNoDisponible, match="ya no hay cupo"):
        reservar(sesion, el(MARTES, 10, 30))
    assert el(MARTES, 10, 30) not in libres(sesion, MARTES)
    assert el(MARTES, 11, 30) in libres(sesion, MARTES)


def test_con_un_puesto_apagado_hay_un_cupo_menos(sesion: Session):
    casos.cambiar_puesto(sesion, puesto(sesion, "C2").id, activo=False, ahora=LUNES_8AM)
    reservar(sesion, el(MARTES, 10, 30))

    assert el(MARTES, 10, 30) not in libres(sesion, MARTES)


def test_nunca_muy_encima_ni_fuera_del_horario_ni_muy_lejos(sesion: Session):
    hoy = libres(sesion, LUNES_8AM.date())  # son las 8:00 a.m.

    assert hoy[0] == hoy_a_las(8, 30)  # media hora antes, mínimo
    assert hoy[-1] == hoy_a_las(18, 30)  # termina a las 7:30 p.m., cuando cierran
    with pytest.raises(HorarioNoDisponible):
        reservar(sesion, hoy_a_las(9, 10))  # no es un horario
    assert libres(sesion, LUNES_8AM.date() + timedelta(days=7))  # hasta 7 días adelante
    assert libres(sesion, LUNES_8AM.date() + timedelta(days=8)) == []


def test_un_telefono_tiene_una_reserva_por_fila(sesion: Session):
    carro = cliente_con_vehiculo(sesion)
    reservar(sesion, el(MARTES, 10, 30), vehiculo=carro)

    with pytest.raises(YaTieneTurnoActivo):
        reservar(sesion, el(MARTES, 12, 30), vehiculo=carro)
    reservar(sesion, el(MARTES, 12, 30), tipo="Moto")  # en la otra fila, sí


# --- A su hora -------------------------------------------------------------------------------


def test_a_su_hora_entra_a_la_fila_de_primero(sesion: Session):
    ocupar_puestos(sesion, "C1", "C2")
    de_la_fila = por_whatsapp(sesion).turno
    reserva = reservar(sesion, hoy_a_las(8, 30))

    entran = casos.activar_reservas(sesion, ahora=hoy_a_las(8, 30))

    assert [r.numero for r in entran] == [reserva.numero]
    turno = leer(sesion, reserva.turno.id)
    assert (turno.estado, turno.prioridad) == (E.EN_FILA, True)
    orden = [
        t.id
        for t in consultas.foto_de_la_fila(
            sesion, fila(sesion).id, hoy_a_las(8, 30), leer_parametros(sesion)
        ).espera
    ]
    assert orden == [turno.id, de_la_fila.id]  # de primero, aunque el otro llegó antes
    # Se libera un puesto: lo llaman a él.
    [lavando, _] = sorted(sesion.scalars(select(Turno.id).where(Turno.estado == E.EN_LAVADO)).all())
    casos.marcar_listo(sesion, lavando, ahora=hoy_a_las(8, 40))
    assert leer(sesion, turno.id).estado == E.LLAMADO
    assert leer(sesion, de_la_fila.id).estado == E.EN_FILA


def test_el_puesto_libre_se_guarda_para_la_reserva_que_viene(sesion: Session):
    ocupar_puestos(sesion, "C1")  # termina a las 9:00
    reserva = reservar(sesion, hoy_a_las(8, 30))

    nuevo = por_whatsapp(sesion, ahora=hoy_a_las(8, 5))  # alguien de la fila

    assert not nuevo.llamado  # el C2 es de la reserva de las 8:30
    resumen = consultas.resumen_de_la_fila(sesion, fila(sesion).id, ahora=hoy_a_las(8, 5))
    assert not resumen.puede_venir_ya
    casos.activar_reservas(sesion, ahora=hoy_a_las(8, 30))
    assert leer(sesion, reserva.turno.id).estado == E.LLAMADO
    avisos_del_llamado = sesion.scalars(
        select(MensajeSaliente).where(
            MensajeSaliente.turno_id == reserva.turno.id,
            MensajeSaliente.tipo == TipoAviso.LLAMADO.value,
        )
    ).all()
    assert len(avisos_del_llamado) == 1  # "¡Es tu turno!"


def test_si_no_llega_a_su_hora_pierde_la_reserva_y_pasa_al_final(sesion: Session):
    [lavando, _] = ocupar_puestos(sesion, "C1", "C2")
    de_la_fila = por_whatsapp(sesion).turno
    reserva = reservar(sesion, hoy_a_las(8, 30))
    casos.activar_reservas(sesion, ahora=hoy_a_las(8, 30))
    casos.marcar_listo(sesion, lavando, ahora=hoy_a_las(8, 31))
    assert leer(sesion, reserva.turno.id).estado == E.LLAMADO  # 15 minutos para llegar

    casos.vencer_gracias(sesion, ahora=hoy_a_las(8, 47))

    turno = leer(sesion, reserva.turno.id)
    assert (turno.estado, turno.prioridad, turno.hora_reservada) == (E.EN_FILA, False, None)
    assert leer(sesion, de_la_fila.id).estado == E.LLAMADO  # el puesto pasa al siguiente
    assert historial(sesion, turno.id)[-1] == (
        "No llegó a la hora de su reserva: la pierde y pasa al final"
    )


def test_si_llega_antes_espera_y_a_su_hora_va_de_primero(sesion: Session):
    ocupar_puestos(sesion, "C1", "C2")
    reserva = reservar(sesion, hoy_a_las(9, 30))

    casos.marcar_llegada(sesion, reserva.turno.id, actor=Actor.CLIENTE, ahora=hoy_a_las(8, 40))
    turno = leer(sesion, reserva.turno.id)
    assert (turno.estado, turno.prioridad) == (E.LLEGO, False)

    casos.activar_reservas(sesion, ahora=hoy_a_las(9, 30))
    assert leer(sesion, turno.id).prioridad
    assert historial(sesion, turno.id)[-1] == "Llegó la hora de su reserva: pasa de primero"


def test_una_reserva_de_un_dia_que_ya_paso_no_se_llama_al_otro_dia(sesion: Session):
    """De noche, en el plan gratis de Render, el servidor se apaga y el reloj no corre a la
    hora de la reserva. Al prender al otro día no se le dice "¡Es tu turno!" por la reserva
    de ayer: queda en No llegó, sin mensaje."""
    reserva = reservar(sesion, hoy_a_las(18, 30))

    casos.activar_reservas(sesion, ahora=el(MARTES, 6, 30))

    turno = leer(sesion, reserva.turno.id)
    assert turno.estado == E.NO_LLEGO
    assert historial(sesion, turno.id)[-1] == "Su reserva era de un día que ya pasó"
    avisos_del_turno = sesion.scalars(
        select(MensajeSaliente.tipo).where(MensajeSaliente.turno_id == turno.id)
    ).all()
    assert TipoAviso.LLAMADO.value not in avisos_del_turno
    assert TipoAviso.NO_LLEGO.value not in avisos_del_turno


# --- Cambiar la hora o cancelar --------------------------------------------------------------


def test_cambiar_la_hora_libera_el_cupo_de_antes(sesion: Session):
    reservar(sesion, el(MARTES, 10, 30))
    mia = reservar(sesion, el(MARTES, 10, 30))

    cambio = casos.reagendar(
        sesion, mia.turno.id, hora_elegida=el(MARTES, 11, 30), actor=Actor.CLIENTE, ahora=LUNES_8AM
    )

    assert (cambio.numero, cambio.numero_anterior) == (mia.numero, None)  # el mismo día
    assert leer(sesion, mia.turno.id).hora_reservada == el(MARTES, 11, 30)
    assert el(MARTES, 10, 30) in libres(sesion, MARTES)
    assert historial(sesion, mia.turno.id)[-1] == "Cambió la hora: mañana a las 11:30 a.m."


def test_cambiar_de_dia_le_da_un_numero_de_ese_dia(sesion: Session):
    miercoles = MARTES + timedelta(days=1)
    reservar(sesion, el(miercoles, 9, 30))  # C-01 del miércoles
    mia = reservar(sesion, el(MARTES, 10, 30))  # C-01 del martes

    cambio = casos.reagendar(
        sesion,
        mia.turno.id,
        hora_elegida=el(miercoles, 10, 30),
        actor=Actor.CLIENTE,
        ahora=LUNES_8AM,
    )

    assert (cambio.numero, cambio.numero_anterior) == ("C-02", "C-01")
    assert leer(sesion, mia.turno.id).fecha == miercoles


def test_quien_estaba_en_la_fila_puede_venir_otro_dia_y_la_fila_avanza(sesion: Session):
    ocupar_puestos(sesion, "C1")
    llamado = por_whatsapp(sesion).turno  # llamado para el C2
    siguiente = por_whatsapp(sesion).turno
    assert leer(sesion, llamado.id).estado == E.LLAMADO

    casos.reagendar(
        sesion, llamado.id, hora_elegida=el(MARTES, 10, 30), actor=Actor.CLIENTE, ahora=LUNES_8AM
    )

    turno = leer(sesion, llamado.id)
    assert (turno.estado, turno.gracia_hasta, turno.fecha) == (E.RESERVADO, None, MARTES)
    assert leer(sesion, siguiente.id).estado == E.LLAMADO  # el puesto pasa al siguiente


def test_la_reserva_empieza_de_cero_aunque_antes_no_hubiera_llegado(sesion: Session):
    """El mensaje de la reserva dice "si no llegas, pasas al final": así tiene que ser,
    aunque hoy ya lo hubieran llamado una vez sin que llegara."""
    turno_id = por_whatsapp(sesion).turno.id  # llamado hasta las 8:15
    casos.vencer_gracias(sesion, ahora=hoy_a_las(8, 16))  # primera vez que no llega
    casos.reagendar(
        sesion,
        turno_id,
        hora_elegida=hoy_a_las(10, 30),
        actor=Actor.CLIENTE,
        ahora=hoy_a_las(8, 20),
    )
    casos.activar_reservas(sesion, ahora=hoy_a_las(10, 30))

    casos.vencer_gracias(sesion, ahora=hoy_a_las(10, 46))  # tampoco llega a su reserva

    assert leer(sesion, turno_id).estado != E.NO_LLEGO  # pasa al final; no pierde el turno
    assert "No llegó a la hora de su reserva: la pierde y pasa al final" in historial(
        sesion, turno_id
    )


def test_ya_en_el_local_no_se_cambia_la_hora(sesion: Session):
    reserva = reservar(sesion, hoy_a_las(9, 30))
    casos.marcar_llegada(sesion, reserva.turno.id, actor=Actor.CLIENTE, ahora=hoy_a_las(8, 40))

    with pytest.raises(CambioNoPermitido):
        casos.reagendar(
            sesion,
            reserva.turno.id,
            hora_elegida=el(MARTES, 10, 30),
            actor=Actor.CLIENTE,
            ahora=hoy_a_las(8, 45),
        )


def test_cancelar_una_reserva_deja_libre_su_cupo(sesion: Session):
    reservar(sesion, el(MARTES, 10, 30))
    mia = reservar(sesion, el(MARTES, 10, 30))

    casos.cancelar(sesion, mia.turno.id, actor=Actor.CLIENTE, ahora=LUNES_8AM)

    assert leer(sesion, mia.turno.id).estado == E.CANCELADO
    assert el(MARTES, 10, 30) in libres(sesion, MARTES)


# --- El recordatorio -------------------------------------------------------------------------


def le_escribio(sesion: Session, telefono: str, cuando: datetime) -> None:
    sesion.add(
        MensajeEntrante(
            id_meta=f"wamid.{telefono}.{cuando.isoformat()}",
            telefono=telefono,
            tipo="text",
            contenido={"texto": "hola"},
            recibido_en=cuando,
            procesado_en=cuando,
        )
    )
    sesion.flush()


def recordatorios(sesion: Session) -> list[MensajeSaliente]:
    consulta = select(MensajeSaliente).where(MensajeSaliente.tipo == TipoAviso.RECORDATORIO.value)
    return list(sesion.scalars(consulta))


def test_una_hora_antes_se_le_recuerda_una_vez(sesion: Session):
    vehiculo = cliente_con_vehiculo(sesion, telefono=CELULAR)
    reserva = reservar(sesion, hoy_a_las(10, 30), vehiculo=vehiculo)
    le_escribio(sesion, CELULAR, LUNES_8AM)

    assert casos.recordar_reservas(sesion, ahora=hoy_a_las(9, 29)) == 0
    assert casos.recordar_reservas(sesion, ahora=hoy_a_las(9, 30)) == 1
    assert casos.recordar_reservas(sesion, ahora=hoy_a_las(9, 40)) == 0  # una sola vez

    [aviso] = recordatorios(sesion)
    assert aviso.contenido == {
        "turno": reserva.numero,
        "hora_reservada": hoy_a_las(10, 30).isoformat(),
    }


def test_si_whatsapp_no_deja_escribirle_no_se_le_recuerda(sesion: Session):
    vehiculo = cliente_con_vehiculo(sesion, telefono=CELULAR)
    reserva = reservar(sesion, el(MARTES, 10, 30), vehiculo=vehiculo)
    le_escribio(sesion, CELULAR, LUNES_8AM)  # hace más de 24 horas

    assert casos.recordar_reservas(sesion, ahora=el(MARTES, 9, 30)) == 0
    assert recordatorios(sesion) == []
    assert leer(sesion, reserva.turno.id).recordado_en is not None  # no se vuelve a revisar


def test_si_cambio_la_hora_el_recordatorio_viejo_ya_no_sale(sesion: Session):
    vehiculo = cliente_con_vehiculo(sesion, telefono=CELULAR)
    reserva = reservar(sesion, hoy_a_las(10, 30), vehiculo=vehiculo)
    le_escribio(sesion, CELULAR, LUNES_8AM)
    casos.recordar_reservas(sesion, ahora=hoy_a_las(9, 30))
    [aviso] = recordatorios(sesion)
    assert avisos.sigue_vigente(sesion, aviso, hoy_a_las(9, 31))

    casos.reagendar(
        sesion,
        reserva.turno.id,
        hora_elegida=hoy_a_las(12, 30),
        actor=Actor.CLIENTE,
        ahora=hoy_a_las(9, 35),
    )

    assert not avisos.sigue_vigente(sesion, aviso, hoy_a_las(9, 36))


def test_el_recordatorio_dice_la_hora_y_deja_cambiarla(sesion: Session, monkeypatch):
    monkeypatch.setattr("app.tiempo.ahora", lambda: hoy_a_las(9, 30))
    contenido = {"turno": "C-03", "hora_reservada": hoy_a_las(10, 30).isoformat()}

    mensaje = mensaje_del_aviso(TipoAviso.RECORDATORIO.value, contenido, leer_parametros(sesion))

    assert mensaje.texto == (
        "¡Hola! Te recordamos tu turno *C-03*: te esperamos hoy a las 10:30 a.m. Si no puedes "
        "venir, toca *Cambiar hora* o *Cancelar*."
    )
    assert [b.titulo for b in mensaje.botones] == ["Cambiar hora", "Cancelar"]


def test_el_reloj_revisa_las_reservas_en_cada_vuelta():
    from app.reloj import tareas

    assert tareas.del_servidor(None)[0] == tareas.RESERVAS


# --- El horario del negocio (migración 0022) ---------------------------------------------------

migracion = importlib.import_module("migraciones.versions.0022_reservas_por_horario")


def aplicar_el_horario(sesion: Session) -> None:
    sesion.flush()
    with Operations.context(MigrationContext.configure(sesion.connection())):
        migracion._horario(migracion.HORARIO_ANTES, migracion.HORARIO_AHORA)
    sesion.expire_all()


def test_el_horario_de_antes_pasa_a_6_30_am_hasta_las_12_de_la_noche(sesion: Session):
    aplicar_el_horario(sesion)

    parametros = leer_parametros(sesion)
    assert (parametros.hora_apertura, parametros.hora_cierre) == (time(6, 30), time(0))


def test_si_el_administrador_ya_lo_habia_cambiado_se_respeta(sesion: Session):
    sesion.get(Configuracion, "hora_apertura").valor = "07:00"
    sesion.flush()

    aplicar_el_horario(sesion)

    assert leer_parametros(sesion).hora_apertura == time(7)
