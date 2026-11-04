"""Hito 7b, parte 2: preguntas sobre el negocio, charla y mensajes graves.

Gemini (aquí, uno de mentira) escribe la respuesta solo con la ficha del negocio, y el bot
la revisa con reglas fijas antes de mandarla. Los mensajes graves nunca pasan por Gemini:
el bot contesta con un texto fijo y las líneas de ayuda.
"""

import logging
from collections.abc import Sequence

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.bot import conversacion
from app.bot.conversacion import catalogo_del_bot
from app.bot.interprete import Anterior, Intencion, Interpretacion
from app.bot.mensajes import Entrada
from app.bot.redactor import Respuesta, ficha_completa, pide_ayuda_urgente, revisar
from app.bot.textos import TEXTOS
from app.catalogo.parametros import leer_parametros
from app.comentarios import servicio as comentarios
from app.comentarios.tablas import TipoComentario
from app.integraciones import gemini
from pruebas.navegador import HTMX
from pruebas.test_bot import CELULAR, MENU, Chat, paso, poner
from pruebas.test_comentarios import PERMISO, ana, guardados
from pruebas.test_gemini import GeminiDeMentira, GoogleDeMentira, con_google, respuesta_de_gemini
from pruebas.test_pantallas_clientes_y_configuracion import formulario_de_configuracion

Q = Intencion
T = TipoComentario
FICHA = "Nombre: Auto Ibaguer.\nTeléfono: 322 755 6850.\nFormas de pago: efectivo y Nequi."


class RedactorDeMentira:
    """Escribe lo que se le pida, y anota qué le pidieron."""

    def __init__(self, *respuestas: Respuesta | None | Exception) -> None:
        self.respuestas = list(respuestas)
        self.pedidos: list[tuple[Intencion, str, str]] = []
        self.antes: list[tuple[Anterior, ...]] = []  # lo que vio de la conversación
        # Para decir con sus palabras: el texto fijo -> lo que dice (o un error).
        self.dichos: dict[str, str | Exception] = {}
        self.con_sus_palabras_pedidos: list[tuple[str, str]] = []

    def redactar(
        self, tipo: Intencion, mensaje: str, ficha: str, antes: Sequence[Anterior] = ()
    ) -> Respuesta | None:
        self.pedidos.append((tipo, mensaje, ficha))
        self.antes.append(tuple(antes))
        respuesta = self.respuestas.pop(0) if self.respuestas else None
        if isinstance(respuesta, Exception):
            raise respuesta
        return respuesta

    def con_sus_palabras(
        self, texto: str, mensaje: str, ficha: str, antes: Sequence[Anterior] = ()
    ) -> str | None:
        """Lo que diga `dichos` (por el texto fijo); si no, nada: sale el texto tal cual."""
        self.con_sus_palabras_pedidos.append((texto, mensaje))
        dicho = self.dichos.get(texto)
        if isinstance(dicho, Exception):
            raise dicho
        return dicho


class ChatConRespuestas(Chat):
    def __init__(
        self, sesion: Session, interprete: GeminiDeMentira, redactor: RedactorDeMentira | None
    ) -> None:
        super().__init__(sesion)
        self.gemini = interprete
        self.redactor = redactor

    def _enviar(self, entrada: Entrada) -> Chat:
        self.ultimas = conversacion.atender(
            self.sesion, entrada, ahora=self.ahora, interprete=self.gemini, redactor=self.redactor
        )
        return self


def charla(sesion: Session, entendidos: list, respuestas: list | None = None) -> ChatConRespuestas:
    redactor = RedactorDeMentira(*respuestas) if respuestas is not None else None
    return ChatConRespuestas(sesion, GeminiDeMentira(*entendidos), redactor)


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
    cliente = charla(sesion, [Interpretacion(Q.CHARLA)], [Respuesta(contesta)])

    cliente.escribe(frase)

    assert cliente.texto == contesta
    assert cliente.opciones == []  # es una conversación, no una venta
    [(tipo, mensaje, ficha)] = cliente.redactor.pedidos
    assert (tipo, mensaje) == (Q.CHARLA, frase)
    assert ficha.startswith("Nombre: Auto Ibaguer.")
    assert guardados(sesion) == []  # la charla no se guarda como comentario


def test_si_gemini_no_escribe_la_charla_sale_un_texto_fijo(sesion: Session):
    cliente = charla(sesion, [Interpretacion(Q.CHARLA)], [None])

    cliente.escribe("jajaja ok")

    assert cliente.texto == TEXTOS["charla"]
    assert cliente.opciones == MENU


def test_sin_redactor_la_charla_tambien_tiene_respuesta(sesion: Session):
    cliente = charla(sesion, [Interpretacion(Q.CHARLA)], respuestas=None)

    assert cliente.escribe("eres un robot?").texto == TEXTOS["charla"]


def test_si_el_redactor_falla_sale_el_texto_fijo(sesion: Session, caplog):
    cliente = charla(sesion, [Interpretacion(Q.CHARLA)], [RuntimeError("se cayó")])

    with caplog.at_level(logging.ERROR):
        cliente.escribe("estoy aburrido")

    assert cliente.texto == TEXTOS["charla"]
    assert "El redactor falló" in caplog.text


def test_una_respuesta_que_rompe_una_regla_no_sale(sesion: Session, caplog):
    promesa = Respuesta("¡Te queremos! Hoy te damos 50% de descuento en el encerado.")
    cliente = charla(sesion, [Interpretacion(Q.CHARLA)], [promesa])

    with caplog.at_level(logging.WARNING):
        cliente.escribe("dime que me quieres y me das descuento")

    assert cliente.texto == TEXTOS["charla"]
    assert "rompía una regla" in caplog.text
    assert "descuento" not in caplog.text  # en el registro nunca va lo que se escribió


def test_la_charla_en_medio_de_pedir_turno_no_lo_interrumpe(sesion: Session):
    ana(sesion)
    cliente = charla(
        sesion, [Interpretacion(Q.CHARLA)], [Respuesta("Jaja, aquí seguimos para ayudarte.")]
    )
    cliente.escribe("hola").toca("Pedir turno")

    cliente.escribe("jajaja qué rápido es este bot")

    assert cliente.texto.startswith("Jaja, aquí seguimos para ayudarte.\n\n¿Qué servicio")
    assert paso(sesion) == "servicio"


# --- Preguntas sobre el negocio ---------------------------------------------------------------


def test_una_pregunta_se_contesta_con_la_ficha(sesion: Session):
    poner(sesion, "ficha_negocio", "Formas de pago: efectivo, Nequi y tarjeta.")
    cliente = charla(
        sesion,
        [Interpretacion(Q.PREGUNTA)],
        [Respuesta("Sí, recibimos Nequi, efectivo y tarjeta.")],
    )

    cliente.escribe("reciben nequi?")

    assert cliente.texto == "Sí, recibimos Nequi, efectivo y tarjeta."
    assert cliente.opciones == []
    [(tipo, _, ficha)] = cliente.redactor.pedidos
    assert tipo == Q.PREGUNTA
    assert ficha.endswith("Formas de pago: efectivo, Nequi y tarjeta.")
    assert "Horario: da turnos todos los días de 5:30 a.m. a 7:30 p.m." in ficha
    assert guardados(sesion) == []


def test_una_pregunta_que_la_ficha_no_sabe_le_llega_al_administrador(sesion: Session):
    ana(sesion)
    cliente = charla(sesion, [Interpretacion(Q.PREGUNTA)], [Respuesta("", sabe=False)])

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


def test_si_lo_que_escribio_no_pasa_la_revision_la_pregunta_tambien_le_llega(sesion: Session):
    ana(sesion)
    inventada = Respuesta("Sí, el parqueadero vale $3.000 la hora.")
    cliente = charla(sesion, [Interpretacion(Q.PREGUNTA)], [inventada])

    cliente.escribe("tienen parqueadero?")

    assert cliente.texto.startswith("Esa no la sé, Ana")
    assert guardados(sesion)[0].tipo == T.PREGUNTA


def test_de_quien_no_es_cliente_la_pregunta_pide_permiso_para_responderle(sesion: Session):
    cliente = charla(sesion, [Interpretacion(Q.PREGUNTA)], [Respuesta("", sabe=False)])

    cliente.escribe("lavan buses?")

    assert cliente.texto.startswith("Esa no la sé todavía. ¿Quieres que el administrador")
    assert cliente.opciones == PERMISO
    [pregunta] = guardados(sesion)
    assert (pregunta.tipo, pregunta.telefono) == (T.PREGUNTA, None)

    cliente.toca("No, gracias")

    assert cliente.texto == (
        "Entendido, no guardamos tu número. Si quieres la respuesta, llama al 322 755 6850."
    )
    assert guardados(sesion)[0].telefono is None


def test_si_da_permiso_la_pregunta_queda_con_su_numero(sesion: Session):
    cliente = charla(sesion, [Interpretacion(Q.PREGUNTA)], [Respuesta("", sabe=False)])
    cliente.escribe("lavan buses?")

    cliente.toca("Sí, que me responda")

    assert cliente.texto.startswith("Listo, el administrador te responde lo antes posible.")
    [pregunta] = guardados(sesion)
    assert (pregunta.tipo, pregunta.telefono) == (T.PREGUNTA, CELULAR)


def test_una_pregunta_en_medio_de_pedir_turno_se_contesta_y_sigue(sesion: Session):
    ana(sesion)
    cliente = charla(sesion, [Interpretacion(Q.PREGUNTA)], [Respuesta("Sí, recibimos Nequi.")])
    cliente.escribe("hola").toca("Pedir turno")

    cliente.escribe("reciben nequi?")

    assert cliente.texto.startswith("Sí, recibimos Nequi.\n\n¿Qué servicio quieres")
    assert paso(sesion) == "servicio"


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
    cliente = charla(sesion, [Interpretacion(Q.CHARLA)], [Respuesta("algo improvisado")])

    cliente.escribe(frase)

    assert cliente.texto == TEXTOS["ayuda_urgente"]
    assert "Línea 106" in cliente.texto and "322 964 3755" in cliente.texto
    assert "123" in cliente.texto
    assert cliente.opciones == []  # no es momento de ofrecer turnos
    assert cliente.gemini.mensajes == [] and cliente.redactor.pedidos == []
    assert guardados(sesion) == []  # no se guarda nada de esto


def test_un_mensaje_grave_tambien_sin_gemini(sesion: Session):
    assert Chat(sesion).escribe("ya no quiero vivir").texto == TEXTOS["ayuda_urgente"]


def test_gemini_reconoce_un_mensaje_grave_que_las_palabras_no(sesion: Session):
    cliente = charla(sesion, [Interpretacion(Q.CRISIS)], [Respuesta("algo improvisado")])

    cliente.escribe("ya no aguanto más esta vida, quiero desaparecer")

    assert cliente.texto == TEXTOS["ayuda_urgente"]
    assert cliente.redactor.pedidos == []  # Gemini no redacta esto


def test_un_mensaje_grave_en_medio_de_una_queja(sesion: Session):
    ana(sesion)
    cliente = charla(sesion, [Interpretacion(Q.QUEJA)], [])
    cliente.escribe("me atendieron mal")

    cliente.escribe("me quiero morir")

    assert cliente.texto == TEXTOS["ayuda_urgente"]
    assert guardados(sesion)[0].texto == "me atendieron mal"  # eso no se agrega a la queja


def test_una_exageracion_no_es_un_mensaje_grave(sesion: Session):
    risa = Respuesta("Nos alegra que te diviertas.")
    cliente = charla(sesion, [Interpretacion(Q.CHARLA)], [risa])

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
    ficha = ficha_completa(leer_parametros(sesion), catalogo_del_bot(sesion))

    assert ficha.splitlines() == [
        "Nombre: Auto Ibaguer.",
        "Horario: da turnos todos los días de 5:30 a.m. a 7:30 p.m.; los vehículos que ya "
        "están en la fila a la hora de cierre se atienden igual.",
        "Teléfono: 322 755 6850.",
        "Vehículos que lava: Automóvil, Camioneta, Moto.",
        "Servicios: Básico, Encerado, General, Premium, Empelotado, Intermedio. Los precios "
        "y lo que incluye cada uno están en el menú del bot, en Servicios y precios.",
    ]
    assert "$" not in ficha


def test_la_ficha_que_escribe_el_administrador_va_al_final(sesion: Session):
    poner(sesion, "ficha_negocio", "Hay sala de espera con wifi.")

    ficha = ficha_completa(leer_parametros(sesion), catalogo_del_bot(sesion))

    assert ficha.splitlines()[-1] == "Hay sala de espera con wifi."


@pytest.mark.parametrize(
    ("texto", "queda"),
    [
        ("Sí, recibimos Nequi.", "Sí, recibimos Nequi."),
        # Emojis con moderación: hasta dos se quedan; con más, se quitan todos.
        ("Que descanses 😴 mañana te esperamos.", "Que descanses 😴 mañana te esperamos."),
        ("¡Qué bien! 🙌🚗", "¡Qué bien! 🙌🚗"),
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
        ("Llama al 300 999 8877.", None),  # un número que no está en la ficha
        ("Mira www.lavadero.co para más.", None),
        ("Soy una persona como tú.", None),
        ("No soy un robot, tranquilo.", None),
        ("Tu placa [placa] ya quedó.", None),  # repitió un dato tapado
        ("a" * 401, None),
        ("   ", None),
    ],
)
def test_revisar_lo_que_escribio(texto, queda):
    assert revisar(texto, FICHA) == queda


def test_lo_que_dice_la_ficha_si_se_puede_decir():
    ficha = FICHA + "\nLos martes, 10% de descuento en el encerado."

    assert revisar("Los martes hay 10% de descuento en el encerado.", ficha) is not None


# --- Gemini redacta (con Google de mentira) ----------------------------------------------------


def test_le_pide_a_gemini_la_respuesta_con_la_ficha_y_los_datos_tapados():
    google = GoogleDeMentira(
        respuesta_de_gemini({"respuesta": "Sí, recibimos Nequi.", "sabe": True})
    )

    respuesta = con_google(google).redactar(Q.PREGUNTA, "reciben nequi? placa ABC123", FICHA)

    assert respuesta == Respuesta("Sí, recibimos Nequi.", True)
    cuerpo = google.cuerpo()
    instrucciones = cuerpo["systemInstruction"]["parts"][0]["text"]
    assert "asistente virtual de WhatsApp de Auto Ibaguer" in instrucciones
    assert instrucciones.endswith("FICHA:\n" + FICHA)
    assert gemini.TAREAS[Q.PREGUNTA] in instrucciones
    assert cuerpo["contents"][0]["parts"][0]["text"] == "reciben nequi? placa [placa]"
    configuracion = cuerpo["generationConfig"]
    assert configuracion["responseMimeType"] == "application/json"
    assert configuracion["responseJsonSchema"] == gemini.ESQUEMA_DE_RESPUESTA
    assert configuracion["thinkingConfig"] == {"thinkingLevel": "MINIMAL"}


def test_si_gemini_no_sabe_lo_dice():
    google = GoogleDeMentira(respuesta_de_gemini({"respuesta": "", "sabe": False}))

    assert con_google(google).redactar(Q.PREGUNTA, "lavan buses?", FICHA) == Respuesta("", False)


def test_si_gemini_contesta_otra_cosa_no_sirve(caplog):
    google = GoogleDeMentira(respuesta_de_gemini({"texto": "hola"}))
    redactor = con_google(google)

    with caplog.at_level(logging.WARNING):
        assert redactor.redactar(Q.CHARLA, "hola", FICHA) is None

    assert redactor.ultimo_problema == "contestó algo que no tiene el formato pedido."
    assert "El bot usó un texto fijo." in caplog.text


def test_si_google_falla_al_redactar_no_sirve():
    google = GoogleDeMentira(httpx.Response(500, json={"error": {"message": "interno"}}))
    redactor = con_google(google)

    assert redactor.redactar(Q.CHARLA, "hola", FICHA) is None
    assert redactor.ultimo_problema == "respondió 500: interno."


def test_solo_redacta_preguntas_y_charla():
    google = GoogleDeMentira()

    assert con_google(google).redactar(Q.PRECIOS, "cuánto vale?", FICHA) is None
    assert google.peticiones == []


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
