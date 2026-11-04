"""Hito 7b, parte 2 (y 7c): preguntas sobre el negocio, charla y mensajes graves.

Gemini (aquí, uno de mentira) escribe la respuesta en la misma consulta en que entiende el
mensaje, solo con la ficha del negocio, y el bot la revisa con reglas fijas antes de
mandarla. Los mensajes graves nunca pasan por Gemini: el bot contesta con un texto fijo y
las líneas de ayuda.
"""

import logging

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.bot.conversacion import catalogo_del_bot, servicios_de_la_ficha
from app.bot.interprete import Intencion, Interpretacion
from app.bot.redactor import ficha_completa, pide_ayuda_urgente, revisar, revisar_con_motivo
from app.bot.textos import TEXTOS
from app.catalogo.parametros import leer_parametros
from app.comentarios import servicio as comentarios
from app.comentarios.tablas import TipoComentario
from pruebas.navegador import HTMX
from pruebas.test_bot import CELULAR, MENU, Chat, paso, poner
from pruebas.test_comentarios import PERMISO, ana, guardados
from pruebas.test_gemini import ChatConGemini, chat
from pruebas.test_pantallas_clientes_y_configuracion import formulario_de_configuracion

Q = Intencion
T = TipoComentario
FICHA = "Nombre: Auto Ibaguer.\nTeléfono: 322 755 6850.\nFormas de pago: efectivo y Nequi."


def charla(sesion: Session, *entendidos: Interpretacion | None | Exception) -> ChatConGemini:
    return chat(sesion, *entendidos)


def dice(intencion: Intencion, texto: str, **datos) -> Interpretacion:
    """Lo que entendió Gemini y lo que escribió, en la misma consulta."""
    return Interpretacion(intencion, respuesta=texto, **datos)


# --- La charla ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("frase", "contesta"),
    [
        ("estoy cansado", "Se entiende. Si quieres, nos dejas el carro y descansas mientras."),
        ("te amo", "¡Qué lindo mensaje! Aquí estamos para consentir tu carro cuando quieras."),
        ("me quiero ir a dormir", "¡Que descanses! Mañana te ayudamos con tu turno."),
    ],
)
def test_la_charla_la_contesta_gemini_sin_ofrecer_el_menu(sesion: Session, frase, contesta):
    cliente = charla(sesion, dice(Q.CHARLA, contesta))

    cliente.escribe(frase)

    assert cliente.texto == contesta
    assert cliente.opciones == []  # es una conversación, no una venta
    assert cliente.gemini.mensajes == [frase]  # una sola consulta: entiende y escribe
    [contexto] = cliente.gemini.contextos
    assert contexto.ficha.startswith("Nombre: Auto Ibaguer.")
    assert guardados(sesion) == []  # la charla no se guarda como comentario


def test_si_gemini_no_escribe_la_charla_sale_un_texto_fijo(sesion: Session):
    cliente = charla(sesion, Interpretacion(Q.CHARLA))

    cliente.escribe("jajaja ok")

    assert cliente.texto == TEXTOS["charla"]
    assert cliente.opciones == MENU
    assert "Salió el texto fijo «charla»." in cliente.traza.texto()


def test_si_gemini_falla_tampoco_se_queda_sin_respuesta(sesion: Session, caplog):
    cliente = charla(sesion, None, RuntimeError("se cayó"))
    cliente.escribe("hola")

    with caplog.at_level(logging.ERROR):
        cliente.escribe("estoy aburrido")

    assert cliente.texto.startswith("Uy, no alcancé a leer bien tu mensaje.")
    assert "Gemini falló" in caplog.text


def test_una_respuesta_que_rompe_una_regla_no_sale(sesion: Session, caplog):
    promesa = "¡Te queremos! Hoy te damos 50% de descuento en el encerado."
    cliente = charla(sesion, dice(Q.CHARLA, promesa))

    with caplog.at_level(logging.WARNING):
        cliente.escribe("dime que me quieres y me das descuento")

    assert cliente.texto == TEXTOS["charla"]
    assert "rompía una regla" in caplog.text
    assert "descuento" not in caplog.text  # en el registro nunca va lo que se escribió
    assert "Lo que escribió no pasó la revisión: traía un precio o un porcentaje." in (
        cliente.traza.texto()
    )


def test_la_charla_en_medio_de_pedir_turno_no_lo_interrumpe(sesion: Session):
    ana(sesion)
    cliente = charla(sesion, None, dice(Q.CHARLA, "Jaja, aquí seguimos para ayudarte."))
    cliente.escribe("hola").toca("Pedir turno")

    cliente.escribe("jajaja qué rápido es este bot")

    assert cliente.texto.startswith("Jaja, aquí seguimos para ayudarte.\n\n¿Qué servicio")
    assert paso(sesion) == "servicio"


# --- Preguntas sobre el negocio ---------------------------------------------------------------


def test_una_pregunta_se_contesta_con_la_ficha(sesion: Session):
    poner(sesion, "ficha_negocio", "Formas de pago: efectivo, Nequi y tarjeta.")
    cliente = charla(sesion, dice(Q.PREGUNTA, "Sí, recibimos Nequi, efectivo y tarjeta."))

    cliente.escribe("reciben nequi?")

    assert cliente.texto == "Sí, recibimos Nequi, efectivo y tarjeta."
    assert cliente.opciones == []
    [contexto] = cliente.gemini.contextos
    assert contexto.ficha.endswith("Formas de pago: efectivo, Nequi y tarjeta.")
    assert "Horario: da turnos todos los días de 5:30 a.m. a 7:30 p.m." in contexto.ficha
    assert guardados(sesion) == []


def test_una_pregunta_que_la_ficha_no_sabe_le_llega_al_administrador(sesion: Session):
    ana(sesion)
    cliente = charla(sesion, Interpretacion(Q.PREGUNTA, sabe=False))  # no escribió nada

    cliente.escribe("lavan buses?")

    assert cliente.texto == (
        "Esa no la sé, Ana, pero ya se la pasé al administrador para que te responda lo "
        "antes posible."
    )
    assert cliente.opciones == MENU
    [pregunta] = guardados(sesion)
    assert (pregunta.tipo, pregunta.texto, pregunta.telefono) == (
        T.PREGUNTA,
        "lavan buses?",
        CELULAR,
    )


def test_si_gemini_dice_lo_que_si_sabe_la_pregunta_igual_le_llega(sesion: Session):
    ana(sesion)
    loque = "Por ahora lavamos automóviles, camionetas y motos; buses no lo tengo confirmado."
    cliente = charla(sesion, dice(Q.PREGUNTA, loque, sabe=False))

    cliente.escribe("lavan buses?")

    assert cliente.texto == (
        f"{loque} Ya se la pasé al administrador, Ana, para que te responda por aquí."
    )
    assert cliente.opciones == []
    assert guardados(sesion)[0].tipo == T.PREGUNTA


def test_si_lo_que_escribio_no_pasa_la_revision_la_pregunta_tambien_le_llega(sesion: Session):
    ana(sesion)
    cliente = charla(sesion, dice(Q.PREGUNTA, "Sí, el parqueadero vale $3.000 la hora."))

    cliente.escribe("tienen parqueadero?")

    assert cliente.texto.startswith("Esa no la sé, Ana")
    assert guardados(sesion)[0].tipo == T.PREGUNTA


def test_de_quien_no_es_cliente_la_pregunta_pide_permiso_para_responderle(sesion: Session):
    cliente = charla(sesion, Interpretacion(Q.PREGUNTA, sabe=False))

    cliente.escribe("lavan buses?")

    assert cliente.texto.startswith("Esa no la sé todavía. ¿Quieres que el administrador")
    assert cliente.opciones == PERMISO
    [pregunta] = guardados(sesion)
    assert (pregunta.tipo, pregunta.telefono) == (T.PREGUNTA, None)

    cliente.toca("No, gracias")

    # Sin "no guardamos tu número": lo que importa es que puede llamar.
    assert cliente.texto == (
        "Listo, sin problema. Si después quieres saberlo, puedes llamar al 322 755 6850."
    )
    assert guardados(sesion)[0].telefono is None


def test_con_lo_que_dijo_gemini_el_permiso_sigue_a_su_respuesta(sesion: Session):
    loque = "Eso no lo tengo confirmado por aquí."
    cliente = charla(sesion, dice(Q.PREGUNTA, loque, sabe=False))

    cliente.escribe("lavan buses?")

    assert cliente.texto == (
        f"{loque}\n\n¿Quieres que el administrador te responda? Para eso guardamos tu número "
        "de WhatsApp, solo para responderte."
    )
    assert cliente.opciones == PERMISO


def test_si_da_permiso_la_pregunta_queda_con_su_numero(sesion: Session):
    cliente = charla(sesion, Interpretacion(Q.PREGUNTA, sabe=False))
    cliente.escribe("lavan buses?")

    cliente.toca("Sí, que me responda")

    assert cliente.texto.startswith("Listo, el administrador te responde lo antes posible.")
    [pregunta] = guardados(sesion)
    assert (pregunta.tipo, pregunta.telefono) == (T.PREGUNTA, CELULAR)


def test_si_pregunta_por_sus_datos_mientras_le_piden_permiso_se_le_contesta(sesion: Session):
    datos = "Solo lo guardamos si aceptas que el administrador te responda."
    cliente = charla(sesion, Interpretacion(Q.PREGUNTA, sabe=False), dice(Q.PREGUNTA, datos))
    cliente.escribe("lavan buses?")

    cliente.escribe("¿y tampoco guardan el número?")

    assert cliente.texto.startswith(f"{datos}\n\n¿Quieres que el administrador te responda?")
    assert cliente.opciones == PERMISO  # no lo tomó como un no
    assert paso(sesion) == "permiso_contacto"
    [contexto] = cliente.gemini.contextos[1:]
    assert "Datos personales: Para darte turno guardamos tu nombre" in contexto.ficha
    assert contexto.en_que_va.startswith("El bot le preguntó si quiere que el administrador")


def test_una_pregunta_en_medio_de_pedir_turno_se_contesta_y_sigue(sesion: Session):
    ana(sesion)
    cliente = charla(sesion, None, dice(Q.PREGUNTA, "Sí, recibimos Nequi."))
    cliente.escribe("hola").toca("Pedir turno")

    cliente.escribe("reciben nequi?")

    assert cliente.texto.startswith("Sí, recibimos Nequi.\n\n¿Qué servicio quieres")
    assert paso(sesion) == "servicio"


def test_una_pregunta_que_no_sabe_en_medio_del_turno_no_lo_interrumpe(sesion: Session):
    ana(sesion)
    cliente = charla(sesion, None, Interpretacion(Q.PREGUNTA, sabe=False))
    cliente.escribe("hola").toca("Pedir turno")

    cliente.escribe("me lo pueden recoger en la casa?")

    assert cliente.texto.startswith(
        "Esa no la sé todavía, pero se la dejé anotada al administrador.\n\n¿Qué servicio"
    )
    assert paso(sesion) == "servicio"
    assert guardados(sesion)[0].texto == "me lo pueden recoger en la casa?"


# --- Mensajes graves ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "frase",
    [
        "ya no quiero vivir",
        "a veces pienso en quitarme la vida",
        "Me quiero morir",
        "me voy a matar",
    ],
)
def test_un_mensaje_grave_recibe_ayuda_sin_pasar_por_gemini(sesion: Session, frase):
    cliente = charla(sesion, dice(Q.CHARLA, "algo improvisado"))

    cliente.escribe(frase)

    assert cliente.texto == TEXTOS["ayuda_urgente"]
    assert "Línea 106" in cliente.texto and "322 964 3755" in cliente.texto
    assert "123" in cliente.texto
    assert cliente.opciones == []  # no es momento de ofrecer turnos
    assert cliente.gemini.mensajes == []
    assert guardados(sesion) == []  # no se guarda nada de esto


def test_un_mensaje_grave_tambien_sin_gemini(sesion: Session):
    assert Chat(sesion).escribe("ya no quiero vivir").texto == TEXTOS["ayuda_urgente"]


def test_gemini_reconoce_un_mensaje_grave_que_las_palabras_no(sesion: Session):
    cliente = charla(sesion, dice(Q.CRISIS, "algo improvisado"))

    cliente.escribe("ya no aguanto más esta vida, quiero desaparecer")

    assert cliente.texto == TEXTOS["ayuda_urgente"]  # lo que escribió no sale


def test_un_mensaje_grave_en_medio_de_una_queja(sesion: Session):
    ana(sesion)
    cliente = charla(sesion, Interpretacion(Q.QUEJA))
    cliente.escribe("me atendieron mal")

    cliente.escribe("me quiero morir")

    assert cliente.texto == TEXTOS["ayuda_urgente"]
    assert guardados(sesion)[0].texto == "me atendieron mal"  # eso no se agrega a la queja


def test_una_exageracion_no_es_un_mensaje_grave(sesion: Session):
    cliente = charla(sesion, dice(Q.CHARLA, "Nos alegra que te diviertas."))

    cliente.escribe("jajaja me quiero morir de la risa")

    assert cliente.texto == "Nos alegra que te diviertas."


@pytest.mark.parametrize(
    ("normal", "grave"),
    [
        ("ya no quiero vivir", True),
        ("pienso en suicidarme", True),
        ("quiero morirme", True),
        ("me voy a matar", True),
        ("no quiero hacerme dano", False),
        ("nunca me voy a matar", False),
        ("no quiero morir", False),
        ("me quiero morir de la risa", False),
        ("quiero morirme de la pena", False),
        ("me muero de hambre", False),
        ("estoy cansado", False),
        ("me quiero ir a dormir", False),
    ],
)
def test_pide_ayuda_urgente(normal, grave):
    assert pide_ayuda_urgente(normal) is grave


# --- La ficha y la revisión -------------------------------------------------------------------


def test_la_ficha_trae_los_datos_del_sistema_sin_precios(sesion: Session):
    ficha = ficha_completa(
        leer_parametros(sesion), catalogo_del_bot(sesion), servicios_de_la_ficha(sesion)
    )

    lineas = ficha.splitlines()
    assert lineas[:5] == [
        "Nombre: Auto Ibaguer.",
        "Horario: da turnos todos los días de 5:30 a.m. a 7:30 p.m. (abre lunes, martes, "
        "miércoles, jueves, viernes, sábado y domingo). Los vehículos que ya están en la fila "
        "a la hora de cierre se atienden igual.",
        "Turnos: se piden por WhatsApp para el mismo día y se atienden en el orden de la fila; "
        "no se apartan para otro día ni para una hora exacta. Cuando llaman al cliente, tiene "
        "15 minutos para llegar; si no llega, pasa al final de la fila, y la segunda vez "
        "pierde el turno.",
        "Teléfono: 322 755 6850.",
        "Vehículos que lava: Automóvil, Camioneta y Moto. Para otros vehículos no hay "
        "servicios ni precios.",
    ]
    assert lineas[5] == "Servicios y qué incluyen (los precios los da el bot):"
    assert lineas[6].startswith("- Automóvil y Camioneta: Básico (")
    assert lineas[7].startswith("- Moto: ")
    # El aviso de datos, sin el "¿Aceptas?": en la ficha es información.
    assert lineas[8] == (
        "Datos personales: Para darte turno guardamos tu nombre, tu celular y la placa de tu "
        "vehículo. Los usamos solo para atenderte y avisarte. Puedes pedir que los borremos "
        "cuando quieras. Si alguien que no es cliente deja una queja o una pregunta, su número "
        "solo se guarda si acepta que el administrador le responda."
    )
    assert "$" not in ficha


def test_la_ficha_dice_que_dias_no_abre(sesion: Session):
    poner(sesion, "dias_atencion", [1, 2, 3, 4, 5, 6])

    ficha = ficha_completa(leer_parametros(sesion), catalogo_del_bot(sesion))

    assert "(abre lunes, martes, miércoles, jueves, viernes y sábado; no abre domingo)" in ficha


def test_la_ficha_que_escribe_el_administrador_va_al_final(sesion: Session):
    poner(sesion, "ficha_negocio", "Hay sala de espera con wifi.")

    ficha = ficha_completa(leer_parametros(sesion), catalogo_del_bot(sesion))

    assert ficha.splitlines()[-2:] == [
        "Lo que escribió el administrador:",
        "Hay sala de espera con wifi.",
    ]


@pytest.mark.parametrize(
    ("texto", "queda"),
    [
        ("Sí, recibimos Nequi.", "Sí, recibimos Nequi."),
        # Emojis con moderación: uno se queda; con más, se quitan todos.
        ("Que descanses 😴 mañana te esperamos.", "Que descanses 😴 mañana te esperamos."),
        ("¡Qué bien! 🙌🚗", "¡Qué bien!"),
        ("¡Listo! 🚗✨🧼 Te esperamos 😊", "¡Listo! Te esperamos"),
        ("👍🏻", None),  # solo un emoji no es una respuesta
        ("Llámanos al 322 755 6850.", "Llámanos al 322 755 6850."),  # está en la ficha
        ("Llámanos al 322-755-6850.", "Llámanos al 322-755-6850."),
        (
            "No soy una persona, soy el asistente virtual.",
            "No soy una persona, soy el asistente virtual.",
        ),
        ("Cuesta $40.000.", None),  # un precio
        ("Hoy tienes 20% menos.", None),
        ("Te lo dejamos gratis.", None),
        ("Te sale en cuarenta mil pesos.", None),  # un precio con palabras
        ("Son 40 lucas nada más.", None),
        ("Tenemos descuentos los martes.", None),
        ("En 15 minutos te atendemos.", None),  # una espera que no promete el sistema
        ("En media hora te atendemos.", None),
        ("Los festivos también abrimos.", None),  # la ficha no habla de festivos
        ("Llama al 300 999 8877.", None),  # un número que no está en la ficha
        ("Mira www.lavadero.co para más.", None),
        ("Soy una persona como tú.", None),
        ("No soy un robot, tranquilo.", None),
        ("¡Listo, ya te aparté el turno!", None),  # eso solo lo hace el bot
        ("Tu turno quedó cancelado.", None),
        ("Tu placa [placa] ya quedó.", None),  # repitió un dato tapado
        ("a" * 401, None),
        ("   ", None),
    ],
)
def test_revisar_lo_que_escribio(texto, queda):
    assert revisar(texto, FICHA) == queda


def test_la_revision_dice_por_que_no_sirve():
    revision = revisar_con_motivo("En 15 minutos te atendemos.", FICHA)

    assert revision.texto is None
    assert revision.motivo == "traía un tiempo de espera que no está en la ficha."


def test_lo_que_dice_la_ficha_si_se_puede_decir():
    ficha = FICHA + "\nLos martes, 10% de descuento en el encerado.\nCuando te llaman tienes "
    ficha += "15 minutos para llegar. Los festivos abrimos medio día."

    assert revisar("Los martes hay 10% de descuento en el encerado.", ficha) is not None
    assert revisar("Cuando te llamen tienes 15 minutos para llegar.", ficha) is not None
    assert revisar("Los festivos abrimos medio día.", ficha) is not None


# --- La ficha en Configuración y las preguntas en Comentarios ------------------------------------


def test_la_ficha_se_guarda_desde_configuracion(adentro: TestClient, sesion: Session):
    formulario = formulario_de_configuracion(
        sesion, ficha_negocio="  Formas de pago:   efectivo y Nequi.  \r\n\r\n\r\n\r\nHay wifi.  "
    )

    respuesta = adentro.post("/configuracion", data=formulario, headers=HTMX)

    assert respuesta.status_code == 200
    assert leer_parametros(sesion).ficha_negocio == "Formas de pago: efectivo y Nequi.\n\nHay wifi."
    assert 'id="ficha_negocio"' in adentro.get("/configuracion").text


def test_una_ficha_muy_larga_no_se_guarda(adentro: TestClient, sesion: Session):
    formulario = formulario_de_configuracion(sesion, ficha_negocio="x" * 2001)

    respuesta = adentro.post("/configuracion", data=formulario, headers=HTMX)

    assert "La ficha puede tener hasta 2000 caracteres; esta tiene 2001." in respuesta.text
    assert leer_parametros(sesion).ficha_negocio == ""


def test_una_pregunta_sale_en_comentarios_con_la_pista_para_la_ficha(
    adentro: TestClient, sesion: Session
):
    comentarios.registrar(sesion, T.PREGUNTA, "lavan buses?")

    pagina = adentro.get("/comentarios").text

    assert "Pregunta que el bot no supo" in pagina
    assert 'href="/configuracion#ficha"' in pagina
