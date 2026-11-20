"""Reservar por WhatsApp un día y una hora, y cambiarla.

Lo que pidió el negocio (19 de noviembre de 2026): "Da la cita muy encima y no deja
reagendar... dejar que se pueda seleccionar un horario y reservar". Ahora:
- después del servicio, el bot pregunta cuándo: lo antes posible (si están abiertos) o un
  día de la lista y una hora libre de ese día; también se puede escribir ("mañana a las
  10:30");
- nada se reserva antes de confirmar, ni a menos de media hora;
- "Cambiar hora", o escribir "ya no puedo mañana, mejor el miércoles a las 9:30", mueve el
  turno a otro día u otra hora. Lo que dice que no puede no cuenta.

Las pruebas corren con el horario de prueba (5:30 a.m. a 7:30 p.m., servicios de 1 hora):
los horarios van de 5:30 a.m. a 6:30 p.m. Es lunes 26 de octubre, 8:00 a.m.
"""

from datetime import datetime, time, timedelta

from sqlalchemy.orm import Session

from app.bot.mensajes import Lista
from app.turnos import casos_de_uso as casos
from app.turnos.estados import EstadoTurno, OrigenTurno
from app.turnos.tablas import Turno
from pruebas.fabricas import BOGOTA, LUNES_8AM, cliente_con_vehiculo, estado, minutos, puesto
from pruebas.test_bot import CELULAR, Chat, paso, poner, turnos_de

E = EstadoTurno
HOY = LUNES_8AM.date()
MARTES = HOY + timedelta(days=1)
MIERCOLES = HOY + timedelta(days=2)
MAÑANA_TEMPRANO = ["5:30 a.m.", "6:30 a.m.", "7:30 a.m.", "8:30 a.m.", "9:30 a.m."]


def el(dia, hora: int, minuto: int = 0) -> datetime:
    return datetime.combine(dia, time(hora, minuto), BOGOTA)


def hasta_cuando(sesion: Session, ahora: datetime = LUNES_8AM) -> Chat:
    """Laura pide turno para su carro (Básico): el bot le pregunta cuándo viene."""
    chat = Chat(sesion, ahora=ahora).toca_el_id("menu.pedir").toca("Acepto")
    return chat.escribe("Laura Gómez").toca("Automóvil").toca("Ahora no").toca("Básico")


def la_lista(chat: Chat) -> Lista:
    [lista] = [mensaje.lista for mensaje in chat.ultimas if mensaje.lista is not None]
    return lista


def reserva_de(sesion: Session, momento: datetime, telefono: str | None = None) -> casos.Reserva:
    vehiculo = cliente_con_vehiculo(sesion, telefono=telefono, nombre="Ana Ruiz")
    return casos.reservar(
        sesion,
        vehiculo_id=vehiculo.id,
        hora_elegida=momento,
        origen=OrigenTurno.WHATSAPP,
        ahora=LUNES_8AM,
    )


def hora_reservada(sesion: Session, turno_id: int) -> datetime:
    sesion.expire_all()
    return sesion.get(Turno, turno_id).hora_reservada


# --- Reservar ----------------------------------------------------------------------------------


def test_reservar_mañana_a_una_hora_de_punta_a_punta(sesion: Session):
    chat = hasta_cuando(sesion)

    dias = la_lista(chat)
    assert dias.boton == "Ver días"
    assert [o.titulo for o in dias.opciones] == [
        "Lo antes posible",
        "Hoy",
        "Mañana",
        "Miércoles 28",
        "Jueves 29",
        "Viernes 30",
        "Sábado 31",
        "Domingo 1",
        "Lunes 2",  # hasta 7 días adelante
    ]
    assert dias.opciones[1].descripcion == "Lunes 26 de octubre · 11 horas libres"  # desde 8:30
    assert dias.opciones[2].descripcion == "Martes 27 de octubre · 14 horas libres"

    chat.toca("Mañana")
    # Todas las horas del día de una vez, por mañana, tarde y noche.
    assert chat.texto == (
        "Estas son las horas libres para *mañana, martes 27 de octubre*:\n\n"
        "🌅 *En la mañana:* 5:30 · 6:30 · 7:30 · 8:30 · 9:30 · 10:30 · 11:30\n"
        "☀️ *En la tarde:* 12:30 · 1:30 · 2:30 · 3:30 · 4:30 · 5:30\n"
        "🌙 *En la noche:* 6:30\n\n"
        "Elige la hora en *Ver horas* o escríbemela, por ejemplo: *12:30 pm*."
    )
    # En una lista de WhatsApp caben 10: con 14 horas, primero la parte del día.
    horas = la_lista(chat)
    assert horas.boton == "Ver horas"
    assert [(o.titulo, o.descripcion) for o in horas.opciones] == [
        ("🌅 En la mañana", "5:30 · 6:30 · 7:30 · 8:30 · 9:30 · 10:30 · 11:30"),
        ("☀️ En la tarde", "12:30 · 1:30 · 2:30 · 3:30 · 4:30 · 5:30"),
        ("🌙 En la noche", "6:30"),
        ("Otro día", "Elegir otro día"),
    ]

    chat.toca("🌅 En la mañana")
    assert chat.texto == "Estas son las horas libres para mañana en la mañana. ¿A qué hora vienes?"
    assert chat.opciones == [
        *MAÑANA_TEMPRANO,
        "10:30 a.m.",
        "11:30 a.m.",
        "Ver todo el día",
        "Otro día",
    ]

    chat.toca("10:30 a.m.")
    assert chat.texto == (
        "Revisa tu turno:\n\n*Nombre:* Laura Gómez\n*Vehículo:* Automóvil, placa pendiente\n"
        "*Servicio:* Básico · $40.000\n*Cuándo:* mañana a las 10:30 a.m.\n\n¿Lo reservamos?"
    )
    assert turnos_de(sesion) == []  # nada se reserva antes de confirmar

    chat.toca("Confirmar turno")

    assert chat.texto == (
        "¡Listo, Laura! Tu turno *C-01* quedó reservado para mañana a las 10:30 a.m.\n\n"
        "A esa hora entras de primero. Tienes 15 minutos para llegar; si no llegas, pierdes la "
        "reserva y pasas al final de la fila. Si no puedes venir, toca *Cambiar hora* o "
        "escribe *cancelar*."
    )
    assert chat.opciones == ["Cambiar hora", "Cancelar"]
    [turno] = turnos_de(sesion)
    assert (turno.estado, turno.fecha, turno.hora_reservada) == (
        E.RESERVADO,
        MARTES,
        el(MARTES, 10, 30),
    )
    assert paso(sesion) == "menu"


def test_para_hoy_se_puede_escribir_la_hora_y_se_le_recuerda(sesion: Session):
    chat = hasta_cuando(sesion).escribe("hoy a las 10:30 am")
    assert "*Cuándo:* hoy a las 10:30 a.m.\n" in chat.texto

    chat.toca("Confirmar turno")

    # Una hora antes es dentro de las 24 horas en que WhatsApp deja escribirle.
    assert chat.texto == (
        "¡Listo, Laura! Tu turno *C-01* quedó reservado para hoy a las 10:30 a.m.\n\n"
        "A esa hora entras de primero. Tienes 15 minutos para llegar; si no llegas, pierdes la "
        "reserva y pasas al final de la fila. Una hora antes te lo recordamos por aquí. Si no "
        "puedes venir, toca *Cambiar hora* o escribe *cancelar*."
    )


def test_nunca_da_la_cita_muy_encima(sesion: Session):
    """El problema que encontró el negocio: a las 8:10 no se puede reservar a las 8:30."""
    chat = hasta_cuando(sesion, ahora=LUNES_8AM + minutos(10))
    assert la_lista(chat).opciones[1].descripcion == "Lunes 26 de octubre · 10 horas libres"

    chat.escribe("hoy a las 8:30")

    assert chat.texto == (
        "Para hoy reservamos con al menos 30 minutos de anticipación.\n\n"
        "Estas son las horas libres para hoy en la mañana. ¿A qué hora vienes?"
    )
    assert chat.opciones[:3] == ["9:30 a.m.", "10:30 a.m.", "11:30 a.m."]


def test_si_se_demora_en_elegir_y_la_hora_queda_encima_no_la_reserva(sesion: Session):
    chat = hasta_cuando(sesion).toca("Hoy").toca("🌅 En la mañana").toca("8:30 a.m.")
    assert "*Cuándo:* hoy a las 8:30 a.m." in chat.texto

    chat.pasan(5).toca("Confirmar turno")  # 8:05: ya no hay media hora

    assert chat.texto.startswith("Para hoy reservamos con al menos 30 minutos de anticipación.")
    assert turnos_de(sesion) == []


def test_una_hora_que_no_esta_muestra_las_mas_cercanas(sesion: Session):
    chat = hasta_cuando(sesion).escribe("mañana a las 10")

    # La lista abre en esa parte del día: 9:30 y 10:30 a un toque.
    assert chat.texto == (
        "A esa hora no tenemos turno, pero mira las más cercanas.\n\n"
        "Estas son las horas libres para mañana en la mañana. ¿A qué hora vienes?"
    )
    assert {"9:30 a.m.", "10:30 a.m.", "Ver todo el día", "Otro día"} <= set(chat.opciones)


def test_una_hora_sin_cupo_muestra_las_de_al_lado(sesion: Session):
    reserva_de(sesion, el(MARTES, 10, 30))
    reserva_de(sesion, el(MARTES, 10, 30))  # Carros tiene 2 puestos

    chat = hasta_cuando(sesion).escribe("mañana a las 10:30")

    assert chat.texto == (
        "A esa hora ya no tenemos cupo.\n\n"
        "Estas son las horas libres para mañana en la mañana. ¿A qué hora vienes?"
    )
    assert chat.opciones == [*MAÑANA_TEMPRANO, "11:30 a.m.", "Ver todo el día", "Otro día"]


def test_otra_parte_del_dia_y_otro_dia(sesion: Session):
    chat = hasta_cuando(sesion).toca("Mañana").toca("☀️ En la tarde")
    assert chat.texto == "Estas son las horas libres para mañana en la tarde. ¿A qué hora vienes?"
    assert chat.opciones == [
        "12:30 p.m.",
        "1:30 p.m.",
        "2:30 p.m.",
        "3:30 p.m.",
        "4:30 p.m.",
        "5:30 p.m.",
        "Ver todo el día",
        "Otro día",
    ]

    chat.toca("Ver todo el día")
    assert chat.texto.startswith("Estas son las horas libres para *mañana, martes 27 de octubre*:")
    assert chat.opciones[-1] == "Otro día"

    chat.toca("Otro día")
    assert chat.texto.startswith("¿Cuándo quieres venir?")
    chat.toca("Miércoles 28")
    assert chat.texto.startswith("Estas son las horas libres para *el miércoles 28 de octubre*:")
    chat.toca("🌅 En la mañana").toca("7:30 a.m.").toca("Confirmar turno")

    assert "quedó reservado para el miércoles 28 de octubre a las 7:30 a.m." in chat.texto


def test_escribir_la_hora_despues_de_elegir_el_dia_es_de_ese_dia(sesion: Session):
    """Eligió el miércoles y escribe "a las 3:30": es el miércoles, no hoy."""
    chat = hasta_cuando(sesion).toca("Miércoles 28").escribe("a las 3:30")

    assert "*Cuándo:* el miércoles 28 de octubre a las 3:30 p.m.\n" in chat.texto


def test_con_pocas_horas_libres_se_eligen_de_una(sesion: Session):
    """Hoy a las 2 de la tarde quedan 5 horas: caben en la lista, sin elegir la parte del día.
    La mañana ya pasó: no sale."""
    chat = hasta_cuando(sesion, ahora=LUNES_8AM.replace(hour=14)).toca("Hoy")

    assert chat.texto == (
        "Estas son las horas libres para *hoy, lunes 26 de octubre*:\n\n"
        "☀️ *En la tarde:* 2:30 · 3:30 · 4:30 · 5:30\n"
        "🌙 *En la noche:* 6:30\n\n"
        "Elige la hora en *Ver horas* o escríbemela, por ejemplo: *4:30 pm*."
    )
    assert chat.opciones == [
        "2:30 p.m.",
        "3:30 p.m.",
        "4:30 p.m.",
        "5:30 p.m.",
        "6:30 p.m.",
        "Otro día",
    ]


def test_una_parte_del_dia_llena_dice_sin_cupo(sesion: Session):
    casos.cambiar_puesto(sesion, puesto(sesion, "C2").id, activo=False, ahora=LUNES_8AM)
    for hora_de_la_mañana in range(5, 12):  # un puesto: 7 reservas llenan la mañana
        reserva_de(sesion, el(MARTES, hora_de_la_mañana, 30))

    chat = hasta_cuando(sesion).toca("Mañana")

    assert "🌅 *En la mañana:* sin cupo\n" in chat.texto
    assert chat.opciones[0] == "12:30 p.m."  # quedan 7: caben en la lista


def test_con_horarios_cada_media_hora_la_parte_del_dia_va_por_paginas(sesion: Session):
    poner(sesion, "minutos_entre_horarios", 30)

    chat = hasta_cuando(sesion).toca("Mañana")
    [mañana, *_] = la_lista(chat).opciones
    assert mañana.descripcion == "13 horas libres, de 5:30 a 11:30 a.m."

    chat.toca("🌅 En la mañana")
    assert chat.opciones == [
        "5:30 a.m.",
        "6:00 a.m.",
        "6:30 a.m.",
        "7:00 a.m.",
        "7:30 a.m.",
        "8:00 a.m.",
        "8:30 a.m.",
        "Más tarde",
        "Ver todo el día",
        "Otro día",
    ]
    chat.toca("Más tarde")
    assert chat.opciones[:2] == ["9:00 a.m.", "9:30 a.m."]
    assert chat.opciones[-3:] == ["11:30 a.m.", "Ver todo el día", "Otro día"]


def test_de_madrugada_reserva_desde_que_abren(sesion: Session):
    chat = hasta_cuando(sesion, ahora=LUNES_8AM.replace(hour=4))

    assert chat.texto == (
        "Ahora estamos cerrados (abrimos hoy a las 5:30 a.m.), pero te reservo el turno de una "
        "vez. ¿Qué día quieres venir?"
    )
    assert chat.opciones[:2] == ["Hoy", "Mañana"]  # sin "Lo antes posible"
    chat.toca("Hoy")
    assert "🌅 *En la mañana:* 5:30 · 6:30 · 7:30" in chat.texto


def test_si_cierran_a_las_12_de_la_noche_atiende_hasta_esa_hora(sesion: Session):
    poner(sesion, "hora_apertura", "06:30")
    poner(sesion, "hora_cierre", "00:00")

    chat = Chat(sesion, ahora=LUNES_8AM.replace(hour=23, minute=40)).escribe("hola")
    assert "de 6:30 a.m. a 12 de la noche" in chat.texto

    chat.toca("Pedir turno").toca("Acepto").escribe("Laura").toca("Automóvil")
    chat.toca("Ahora no").toca("Básico")

    # Abierto: puede venir ya. Hoy ya no hay horas para reservar (la última es 10:30 p.m.).
    assert chat.opciones[:2] == ["Lo antes posible", "Mañana"]
    chat.toca("Mañana")
    assert "🌅 *En la mañana:* 6:30 · 7:30 · 8:30 · 9:30 · 10:30 · 11:30\n" in chat.texto
    assert "🌙 *En la noche:* 6:30 · 7:30 · 8:30 · 9:30 · 10:30\n" in chat.texto
    chat.toca("🌙 En la noche")
    assert chat.opciones == [
        "6:30 p.m.",
        "7:30 p.m.",
        "8:30 p.m.",
        "9:30 p.m.",
        "10:30 p.m.",  # a las 11:30 p.m. ya no alcanza
        "Ver todo el día",
        "Otro día",
    ]


# --- Su reserva ------------------------------------------------------------------------------


def test_mi_turno_dice_para_cuando_reservo(sesion: Session):
    reserva_de(sesion, el(MARTES, 10, 30), telefono=CELULAR)

    chat = Chat(sesion).escribe("mi turno")

    assert chat.texto == "Tu turno *C-01* (carros): reservado para mañana a las 10:30 a.m."
    assert chat.opciones == ["Cambiar hora", "Cancelar"]


def test_ya_llegue_con_una_reserva_de_otro_dia_no_lo_marca(sesion: Session):
    reserva = reserva_de(sesion, el(MARTES, 10, 30), telefono=CELULAR)

    chat = Chat(sesion).escribe("ya llegué")

    assert estado(sesion, reserva.turno.id) == E.RESERVADO
    assert chat.texto.startswith("Tu turno *C-01* (carros): reservado para mañana")


def test_ya_llegue_con_la_reserva_de_hoy_lo_marca(sesion: Session):
    reserva = reserva_de(sesion, el(HOY, 9, 30), telefono=CELULAR)

    chat = Chat(sesion).pasan(30).escribe("ya llegué")

    assert chat.texto.startswith("¡Bienvenido! Ya te marcamos como llegado (C-01)")
    assert estado(sesion, reserva.turno.id) == E.LLEGO


def test_pedir_otro_turno_ofrece_cambiar_la_hora_del_que_tiene(sesion: Session):
    reserva_de(sesion, el(MARTES, 10, 30), telefono=CELULAR)

    chat = Chat(sesion).toca_el_id("menu.pedir")

    assert chat.texto == (
        "Ya tienes el turno *C-01* en la fila de carros: reservado para mañana a las 10:30 "
        "a.m. Por WhatsApp se puede tener un turno por fila. ¿Qué hacemos?"
    )
    assert "Cambiar hora" in chat.opciones
    chat.toca("Cambiar hora")
    assert chat.texto == "Claro. ¿Para cuándo movemos tu turno *C-01*?"


# --- Cambiar la hora --------------------------------------------------------------------------


def test_cambiar_hora_con_el_boton(sesion: Session):
    reserva = reserva_de(sesion, el(MARTES, 10, 30), telefono=CELULAR)
    chat = Chat(sesion).escribe("mi turno").toca("Cambiar hora")

    assert chat.texto == "Claro. ¿Para cuándo movemos tu turno *C-01*?"
    assert chat.opciones[0] == "Hoy"  # quien cambia la hora elige una hora
    assert chat.opciones[-1] == "Cancelar el turno"
    chat.toca("Mañana")
    # Su propio cupo de las 10:30 sigue disponible para él.
    assert "🌅 *En la mañana:* 5:30 · 6:30 · 7:30 · 8:30 · 9:30 · 10:30 · 11:30" in chat.texto
    chat.escribe("a las 11:30")  # escrita: es de mañana, el día que eligió

    assert chat.texto == "¡Listo! Tu turno *C-01* quedó para mañana a las 11:30 a.m. Te esperamos."
    assert chat.opciones == ["Cambiar hora", "Cancelar"]
    assert hora_reservada(sesion, reserva.turno.id) == el(MARTES, 11, 30)


def test_ya_no_puedo_ese_dia_y_dice_cuando_si(sesion: Session):
    """Lo que pidió el negocio: "si dicen que ya no pueden ese día, se reagenda según
    ellos". El día que no puede no cuenta."""
    reserva_de(sesion, el(MIERCOLES, 7, 30))  # C-01 del miércoles es de otro cliente
    reserva = reserva_de(sesion, el(MARTES, 10, 30), telefono=CELULAR)

    chat = Chat(sesion).escribe("ya no puedo mañana, mejor el miércoles a las 9:30")

    assert chat.texto == (
        "¡Listo! Movimos tu turno para el miércoles 28 de octubre a las 9:30 a.m. Como cambió "
        "de día, ahora es el *C-02*. Te esperamos."
    )
    assert hora_reservada(sesion, reserva.turno.id) == el(MIERCOLES, 9, 30)


def test_ya_no_puedo_sin_decir_cuando_muestra_los_dias(sesion: Session):
    reserva = reserva_de(sesion, el(MARTES, 10, 30), telefono=CELULAR)

    chat = Chat(sesion).escribe("ya no puedo mañana")

    assert chat.texto == "Claro. ¿Para cuándo movemos tu turno *C-01*?"
    assert chat.opciones[-1] == "Cancelar el turno"
    assert hora_reservada(sesion, reserva.turno.id) == el(MARTES, 10, 30)  # nada cambió


def test_quien_esta_en_la_fila_tambien_puede_pasar_para_otro_dia(sesion: Session):
    chat = hasta_cuando(sesion).toca("Lo antes posible").toca("Confirmar turno")
    [turno] = turnos_de(sesion)
    assert turno.estado == E.LLAMADO  # había puesto libre

    chat.escribe("no alcanzo a llegar, mejor mañana a las 9:30")

    # Mañana también es el primero: el número no cambia.
    assert chat.texto == "¡Listo! Tu turno *C-01* quedó para mañana a las 9:30 a.m. Te esperamos."
    assert estado(sesion, turno.id) == E.RESERVADO


def test_el_boton_del_recordatorio_cambia_la_hora(sesion: Session):
    reserva_de(sesion, el(HOY, 10, 30), telefono=CELULAR)

    chat = Chat(sesion).pasan(90).toca_el_id("turno.cambiar")  # el botón del recordatorio

    assert chat.texto == "Claro. ¿Para cuándo movemos tu turno *C-01*?"
    chat.escribe("a las 2 de la tarde").toca("2:30 p.m.")
    assert chat.texto == "¡Listo! Tu turno *C-01* quedó para hoy a las 2:30 p.m. Te esperamos."
