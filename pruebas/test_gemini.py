"""Gemini: una sola consulta por mensaje, con un Gemini de mentira.

Google no se toca: lo que "responde Gemini" lo arma aquí un httpx.MockTransport, y en las
pruebas de la conversación un intérprete de mentira dice qué entendió y qué escribió. Las
frases de prueba contra el modelo de verdad se corren aparte: uv run auto-ibaguer
probar-gemini.
"""

import json
import logging
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app import cli
from app.bot import conversacion
from app.bot.conversacion import Traza, catalogo_del_bot
from app.bot.frases_de_prueba import FRASES
from app.bot.interprete import (
    CARRO,
    NO,
    OTRO_VEHICULO,
    SI,
    Anterior,
    Catalogo,
    Contexto,
    Intencion,
    Interpretacion,
    leer_respuesta,
    ocultar_datos,
)
from app.bot.mensajes import Entrada
from app.catalogo.parametros import leer_parametros
from app.config import obtener_entorno
from app.integraciones import gemini
from pruebas.fabricas import cliente_con_vehiculo, por_whatsapp
from pruebas.test_bot import CELULAR, MENU, Chat, paso, poner

Q = Intencion
CATALOGO = Catalogo(
    ("Automóvil", "Camioneta", "Moto"),
    ("Básico", "Encerado", "General", "Premium", "Empelotado", "Intermedio"),
)
CONTEXTO = Contexto(CATALOGO, ficha="Nombre: Auto Ibaguer.")
# Lo que Gemini contesta cuando no hay nada más que decir (como el JSON de verdad).
NADA = {"vehiculo": "ninguno", "servicio": "ninguno", "si_o_no": "ninguno", "sabe": True}


def lo_que_contesta(intencion: str, respuesta: str = "", **datos: Any) -> dict[str, Any]:
    return {"intencion": intencion, **NADA, "respuesta": respuesta, **datos}


# --- Tapar los datos personales -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("mensaje", "tapado"),
    [
        ("mi placa es ABC123", "mi placa es [placa]"),
        ("la moto abc12d ya está?", "la moto [placa] ya está?"),
        ("turno para ABC-123 y ABC 12D", "turno para [placa] y [placa]"),
        ("llámenme al 300 123 4567", "llámenme al [número]"),
        ("mi número: +57 3001234567", "mi número: [número]"),
        ("escríbanme a ana.ruiz@correo.com", "escríbanme a [correo]"),
        ("el general cuesta 120.000?", "el general cuesta 120.000?"),  # un precio no es de nadie
        ("voy a las 10 y 30", "voy a las 10 y 30"),
    ],
)
def test_los_datos_personales_se_tapan_antes_de_mandarlos(mensaje, tapado):
    assert ocultar_datos(mensaje) == tapado


def test_un_mensaje_muy_largo_se_corta():
    assert len(ocultar_datos("hola " * 300)) == 500


# --- Revisar lo que contesta ---------------------------------------------------------------------


def test_solo_se_cree_lo_que_esta_en_la_lista():
    entendido = leer_respuesta(
        lo_que_contesta("precios", vehiculo="Camioneta", servicio="Encerado"), CATALOGO
    )
    assert entendido == Interpretacion(Q.PRECIOS, "Camioneta", "Encerado")

    inventado = leer_respuesta(
        {"intencion": "precios", "vehiculo": "Bus", "servicio": "Polichado", "si_o_no": "tal vez"},
        CATALOGO,
    )
    assert inventado == Interpretacion(Q.PRECIOS)  # sin vehículo, servicio ni sí inventados
    assert leer_respuesta({"intencion": "precios", "vehiculo": CARRO}, CATALOGO).vehiculo == CARRO
    otro = leer_respuesta({"intencion": "pregunta", "vehiculo": "otro"}, CATALOGO)
    assert otro.vehiculo == OTRO_VEHICULO  # un vehículo que el lavadero no tiene
    assert leer_respuesta({"intencion": "vender_el_carro"}, CATALOGO) is None
    assert leer_respuesta(["precios"], CATALOGO) is None


def test_trae_lo_que_escribio_y_si_lo_sabe():
    entendido = leer_respuesta(
        lo_que_contesta("pregunta", "  Eso no lo tengo confirmado.  ", sabe=False, si_o_no="no"),
        CATALOGO,
    )

    assert entendido == Interpretacion(
        Q.PREGUNTA, si_o_no=NO, respuesta="Eso no lo tengo confirmado.", sabe=False
    )
    # Si falta, se sabe y no escribió nada (lo contesta el sistema).
    assert leer_respuesta({"intencion": "ver_fila"}, CATALOGO) == Interpretacion(Q.VER_FILA)


# --- La API de Gemini (de mentira) --------------------------------------------------------------


def respuesta_de_gemini(contenido: dict | str, *, con_pensamiento: bool = False) -> httpx.Response:
    texto = contenido if isinstance(contenido, str) else json.dumps(contenido)
    partes = [{"text": "pensando en voz alta", "thought": True}] if con_pensamiento else []
    partes.append({"text": texto})
    cuerpo = {"candidates": [{"content": {"role": "model", "parts": partes}}]}
    return httpx.Response(200, json=cuerpo)


class GoogleDeMentira:
    """Hace de la API de Gemini: anota cada petición y contesta lo que se le diga."""

    def __init__(self, *respuestas: httpx.Response | Exception) -> None:
        self.respuestas = list(respuestas)
        self.peticiones: list[httpx.Request] = []

    def __call__(self, peticion: httpx.Request) -> httpx.Response:
        self.peticiones.append(peticion)
        respuesta = self.respuestas.pop(0)
        if isinstance(respuesta, Exception):
            raise respuesta
        return respuesta

    def cuerpo(self, numero: int = -1) -> dict[str, Any]:
        return json.loads(self.peticiones[numero].content)


def con_google(google: GoogleDeMentira, modelo: str = "gemini-3.5-flash-lite") -> gemini.Gemini:
    http = httpx.Client(transport=httpx.MockTransport(google))
    return gemini.Gemini("clave-de-prueba", modelo, negocio="Auto Ibaguer", http=http)


@pytest.fixture(autouse=True)
def sin_esperar(monkeypatch: pytest.MonkeyPatch) -> None:
    """Antes de repetir una petición se espera un momento: en las pruebas, nada."""
    monkeypatch.setattr(gemini, "PAUSA_ANTES_DE_REPETIR", 0)


def test_una_sola_consulta_entiende_y_escribe_con_formato_fijo():
    google = GoogleDeMentira(
        respuesta_de_gemini(lo_que_contesta("pregunta", "Sí, recibimos Nequi."))
    )

    entendido = con_google(google).interpretar("reciben nequi? mi placa es ABC123", CONTEXTO)

    assert entendido == Interpretacion(Q.PREGUNTA, respuesta="Sí, recibimos Nequi.")
    [peticion] = google.peticiones  # una sola consulta
    assert str(peticion.url) == (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        "gemini-3.5-flash-lite:generateContent"
    )
    assert peticion.headers["x-goog-api-key"] == "clave-de-prueba"  # la clave nunca en la URL
    cuerpo = google.cuerpo()
    instrucciones = cuerpo["systemInstruction"]["parts"][0]["text"]
    assert "lavadero de carros y motos" in instrucciones and "Auto Ibaguer" in instrucciones
    # Las reglas van aparte de los datos: la ficha y el mensaje van en el contenido.
    assert "Nombre: Auto Ibaguer." not in instrucciones
    contenido = cuerpo["contents"][0]["parts"][0]["text"]
    assert contenido.startswith("FICHA:\nNombre: Auto Ibaguer.")
    assert contenido.endswith("MENSAJE NUEVO DEL CLIENTE:\nreciben nequi? mi placa es [placa]")
    # Los nombres de la API de AI Studio: "responseFormat" es solo de Vertex AI y el nivel
    # de pensamiento va dentro de thinkingConfig (la librería oficial de Google, google-genai).
    configuracion = cuerpo["generationConfig"]
    assert configuracion["responseMimeType"] == "application/json"
    esquema = configuracion["responseJsonSchema"]
    assert list(esquema["properties"]) == [
        "intencion",
        "vehiculo",
        "servicio",
        "si_o_no",
        "sabe",
        "respuesta",  # al final: escribe ya sabiendo qué entendió
    ]
    assert esquema["properties"]["intencion"]["enum"] == [i.value for i in Intencion]
    assert esquema["properties"]["vehiculo"]["enum"] == [
        "Automóvil",
        "Camioneta",
        "Moto",
        "carro",
        "otro",
        "ninguno",
    ]
    assert esquema["properties"]["si_o_no"]["enum"] == ["si", "no", "ninguno"]
    assert configuracion["thinkingConfig"] == {"thinkingLevel": "MINIMAL"}
    assert "responseFormat" not in configuracion
    assert "thinkingLevel" not in configuracion


def test_lo_que_lee_gemini_trae_cada_dato_con_su_titulo_y_tapado():
    contexto = Contexto(
        CATALOGO,
        ficha="Nombre: Auto Ibaguer.",
        antes=(
            Anterior("cliente", "mi placa es ABC123, llámame al 300 123 4567"),
            Anterior("bot", "¡Listo!\nTe esperamos.", ("Pedir turno", "Ver la fila")),
            Anterior("administrador", "Hola Ana, te habla Eduar."),
        ),
        en_que_va="El bot le preguntó la placa (la ABC123 no sirve).",
        guias=(("saludo", "¡Hola! ¿En qué te ayudamos?"), ("despedida", "¡Con gusto!")),
    )

    assert gemini.contenido("es la XYZ12A", contexto) == (
        "FICHA:\nNombre: Auto Ibaguer.\n\n"
        "GUÍAS (lo que diría el bot; tú lo dices con tus palabras):\n"
        "- saludo: «¡Hola! ¿En qué te ayudamos?»\n"
        "- despedida: «¡Con gusto!»\n\n"
        "LO ÚLTIMO DE LA CONVERSACIÓN (lo más reciente al final):\n"
        "Cliente: mi placa es [placa], llámame al [número]\n"
        "Bot: ¡Listo! Te esperamos. [botones: Pedir turno | Ver la fila]\n"
        "Persona del lavadero: Hola Ana, te habla Eduar.\n\n"
        "EN QUÉ VA: El bot le preguntó la placa (la [placa] no sirve).\n\n"
        "MENSAJE NUEVO DEL CLIENTE:\nes la [placa]"
    )


def test_sin_nada_antes_gemini_sabe_que_es_como_un_primer_mensaje():
    texto = gemini.contenido("hola", Contexto(CATALOGO))

    assert "FICHA:\nSin datos del negocio." in texto
    assert "LO ÚLTIMO DE LA CONVERSACIÓN: nada reciente, es como un primer mensaje." in texto
    assert "EN QUÉ VA: El bot no le está preguntando nada." in texto
    assert "GUÍAS" not in texto


def test_un_mensaje_largo_de_antes_se_recorta():
    contexto = Contexto(CATALOGO, antes=(Anterior("cliente", "a" * 900),))

    texto = gemini.contenido("ok", contexto)

    assert f"Cliente: {'a' * (gemini.LARGO_DE_CADA_ANTERIOR - 1)}…\n" in texto


@pytest.mark.parametrize(
    ("modelo", "pensamiento"),
    [
        ("gemini-2.5-flash-lite", {"thinkingBudget": 0}),
        ("gemini-3.6-flash", {"thinkingLevel": "MINIMAL"}),
        ("gemini-flash-lite-latest", {"thinkingLevel": "MINIMAL"}),
        ("gemini-3.1-pro-preview", {"thinkingLevel": "LOW"}),  # los Pro no bajan de LOW
    ],
)
def test_cada_modelo_piensa_lo_menos_posible_a_su_manera(modelo, pensamiento):
    google = GoogleDeMentira(respuesta_de_gemini(lo_que_contesta("otro")))

    con_google(google, modelo).interpretar("hola", CONTEXTO)

    configuracion = google.cuerpo()["generationConfig"]
    assert configuracion["thinkingConfig"] == pensamiento
    assert "thinkingLevel" not in configuracion


def test_un_modelo_2_x_pro_piensa_lo_que_necesita():
    google = GoogleDeMentira(respuesta_de_gemini(lo_que_contesta("otro")))

    con_google(google, "gemini-2.5-pro").interpretar("hola", CONTEXTO)

    assert "thinkingConfig" not in google.cuerpo()["generationConfig"]  # no se puede apagar


def test_lee_la_respuesta_aunque_traiga_lo_que_penso_o_venga_entre_comillas():
    entre_comillas = f"```json\n{json.dumps(lo_que_contesta('ver_fila'))}\n```"
    google = GoogleDeMentira(
        respuesta_de_gemini(lo_que_contesta("cancelar"), con_pensamiento=True),
        respuesta_de_gemini(entre_comillas),
    )
    interprete = con_google(google)

    assert interprete.interpretar("ya no voy", CONTEXTO) == Interpretacion(Q.CANCELAR)
    assert interprete.interpretar("hay mucha gente?", CONTEXTO) == Interpretacion(Q.VER_FILA)


def cuota(cual: str) -> httpx.Response:
    """Lo que contesta Google cuando se acaba una cuota del plan (429)."""
    detalle = {
        "@type": "type.googleapis.com/google.rpc.QuotaFailure",
        "violations": [{"quotaId": f"GenerateRequests{cual}PerProjectPerModel-FreeTier"}],
    }
    error = {"code": 429, "message": "Resource exhausted", "details": [detalle]}
    return httpx.Response(429, json={"error": error})


@pytest.mark.parametrize(
    ("respuestas", "problema"),
    [
        ([httpx.ReadTimeout("lento")], "no respondió en 5 segundos."),
        (
            [httpx.ConnectError("sin internet"), httpx.ConnectError("sin internet")],
            "no se pudo conectar (ConnectError).",
        ),
        ([httpx.Response(429, json={"error": {"message": "x"}})], "se acabó la cuota (429)."),
        ([cuota("PerMinute")], "llegó al límite de consultas por minuto (429)"),
        ([cuota("PerDay")], "se acabó la cuota del día (429): se renueva en la madrugada"),
        (
            [httpx.Response(400, json={"error": {"message": "API key not valid."}})],
            "no aceptó la clave (400): revisa GEMINI_CLAVE en el .env.",
        ),
        (
            [httpx.Response(404, json={"error": {"message": "models/x is not found."}})],
            "no encontró el modelo (404): revisa el nombre en Configuración.",
        ),
        (
            [httpx.Response(500, text="<html>error</html>")] * 2,
            "respondió 500.",
        ),
        (
            [httpx.Response(200, json={"promptFeedback": {"blockReason": "SAFETY"}})],
            "Google no dejó leer el mensaje: lo bloquearon sus filtros (SAFETY).",
        ),
        (
            [httpx.Response(200, json={"candidates": [{"finishReason": "MAX_TOKENS"}]})],
            "la respuesta se cortó porque era muy larga (MAX_TOKENS).",
        ),
        ([respuesta_de_gemini("no sé qué decir")], "contestó algo que no tiene el formato pedido."),
        ([respuesta_de_gemini({"intencion": "vender"})], "no tiene el formato pedido."),
    ],
)
def test_si_gemini_falla_queda_anotado_por_que(respuestas, problema, caplog):
    interprete = con_google(GoogleDeMentira(*respuestas))

    with caplog.at_level(logging.WARNING, logger="app.integraciones.gemini"):
        assert interprete.interpretar("ya llegué con la moto ABC12D", CONTEXTO) is None

    assert problema in interprete.ultimo_problema
    assert problema in caplog.text and "El bot siguió sin Gemini." in caplog.text
    assert "ABC12D" not in caplog.text and "moto" not in caplog.text  # nunca el mensaje


def test_un_problema_pasajero_de_google_se_intenta_una_vez_mas():
    google = GoogleDeMentira(
        httpx.Response(503, json={"error": {"message": "overloaded"}}),
        respuesta_de_gemini(lo_que_contesta("ver_fila")),
    )
    interprete = con_google(google)

    assert interprete.interpretar("hay mucha gente?", CONTEXTO) == Interpretacion(Q.VER_FILA)
    assert len(google.peticiones) == 2
    assert interprete.ultimo_problema is None


def test_una_cuota_acabada_no_se_repite():
    google = GoogleDeMentira(cuota("PerMinute"))

    assert con_google(google).interpretar("hola", CONTEXTO) is None
    assert len(google.peticiones) == 1  # repetir no sirve: hay que esperar


def test_un_mismo_problema_se_anota_una_vez_y_no_con_cada_mensaje(caplog):
    google = GoogleDeMentira(*[httpx.ReadTimeout("lento")] * 3)
    interprete = con_google(google)

    with caplog.at_level(logging.WARNING, logger="app.integraciones.gemini"):
        for _ in range(3):
            interprete.interpretar("hola", CONTEXTO)

    assert len(caplog.records) == 1


# --- ¿Se usa Gemini? --------------------------------------------------------------------------


@pytest.fixture
def entorno(monkeypatch: pytest.MonkeyPatch) -> Callable[..., None]:
    def poner_en_el_entorno(**valores: Any) -> None:
        for campo, valor in valores.items():
            monkeypatch.setattr(obtener_entorno(), campo, valor)

    return poner_en_el_entorno


def test_sin_clave_no_se_usa(sesion: Session, entorno):
    entorno(gemini_clave="")

    estado = gemini.estado(leer_parametros(sesion))

    assert (estado.usar, estado.texto) == (False, "Falta GEMINI_CLAVE en el archivo .env.")
    assert gemini.para_el_bot(leer_parametros(sesion)) is None


def test_apagado_en_configuracion_no_se_usa(sesion: Session, entorno):
    entorno(gemini_clave="clave")
    poner(sesion, "gemini_activo", False)

    assert gemini.para_el_bot(leer_parametros(sesion)) is None


def test_en_el_computador_el_plan_gratis_sirve_para_probar(sesion: Session, entorno):
    entorno(gemini_clave="clave", gemini_plan_de_pago=False, entorno="desarrollo")

    estado = gemini.estado(leer_parametros(sesion))
    interprete = gemini.para_el_bot(leer_parametros(sesion))

    assert estado.usar and "plan gratis" in estado.texto and "solo para probar" in estado.texto
    assert interprete.modelo == "gemini-3.5-flash-lite"


def test_en_la_nube_el_plan_gratis_no_se_usa_con_clientes(sesion: Session, entorno):
    entorno(gemini_clave="clave", gemini_plan_de_pago=False, entorno="produccion")

    estado = gemini.estado(leer_parametros(sesion))

    assert not estado.usar and "GEMINI_PLAN_DE_PAGO=true" in estado.texto
    entorno(gemini_plan_de_pago=True)
    assert gemini.estado(leer_parametros(sesion)).texto == "Conectado, con el plan de pago."


def test_un_modelo_con_un_nombre_raro_no_se_usa(sesion: Session, entorno):
    entorno(gemini_clave="clave")
    poner(sesion, "modelo_gemini", "../otra-cosa")

    assert gemini.para_el_bot(leer_parametros(sesion)) is None


# --- El bot con Gemini --------------------------------------------------------------------------


class GeminiDeMentira:
    """Dice lo que se le pida que entendió (y lo que escribió), y anota qué le preguntaron."""

    def __init__(self, *entendidos: Interpretacion | None | Exception) -> None:
        self.entendidos = list(entendidos)
        self.mensajes: list[str] = []
        self.contextos: list[Contexto] = []
        self.ultimo_problema: str | None = None

    def interpretar(self, mensaje: str, contexto: Contexto) -> Interpretacion | None:
        self.mensajes.append(mensaje)
        self.contextos.append(contexto)
        entendido = self.entendidos.pop(0) if self.entendidos else None
        if isinstance(entendido, Exception):
            raise entendido
        self.ultimo_problema = None if entendido is not None else "no respondió (de mentira)."
        return entendido

    @property
    def antes(self) -> list[tuple[Anterior, ...]]:
        """Lo que vio de la conversación con cada mensaje (su memoria)."""
        return [contexto.antes for contexto in self.contextos]


class ChatConGemini(Chat):
    def __init__(self, sesion: Session, gemini_de_mentira: GeminiDeMentira, **datos) -> None:
        super().__init__(sesion, **datos)
        self.gemini = gemini_de_mentira
        self.traza = Traza()

    def _enviar(self, entrada: Entrada) -> "Chat":
        self.ultimas, self.traza = conversacion.atender_con_traza(
            self.sesion, entrada, ahora=self.ahora, interprete=self.gemini
        )
        return self


def chat(sesion: Session, *entendidos: Interpretacion | None | Exception) -> ChatConGemini:
    return ChatConGemini(sesion, GeminiDeMentira(*entendidos))


def test_cuanto_vale_lavar_la_camionetica_muestra_los_precios_de_camioneta(sesion: Session):
    cliente = chat(sesion, Interpretacion(Q.PRECIOS, vehiculo="Camioneta"))

    cliente.escribe("cuánto vale lavar la camionetica")

    assert cliente.texto.startswith("Servicios para camioneta:")
    assert "*Básico* · $50.000" in cliente.texto
    assert cliente.gemini.mensajes == ["cuánto vale lavar la camionetica"]
    [contexto] = cliente.gemini.contextos
    assert contexto.catalogo == catalogo_del_bot(sesion)
    assert "Vehículos que lava: Automóvil, Camioneta y Moto." in contexto.ficha
    assert contexto.en_que_va == ""  # en el menú el bot no está esperando nada


def test_si_dice_carro_pregunta_si_es_automovil_o_camioneta(sesion: Session):
    cliente = chat(sesion, Interpretacion(Q.PRECIOS, vehiculo=CARRO))

    cliente.escribe("a cómo lavan un carro?")

    assert cliente.texto == "¿Automóvil o camioneta?"
    assert cliente.opciones == ["Automóvil", "Camioneta"]


def test_si_tiene_un_solo_vehiculo_los_precios_son_de_ese(sesion: Session):
    cliente_con_vehiculo(sesion, telefono=CELULAR, tipo="Moto", placa="ABC12D")
    cliente = chat(sesion, Interpretacion(Q.PRECIOS))

    cliente.escribe("qué precios manejan?")

    assert cliente.texto.startswith("Servicios para moto:")


def test_ya_no_voy_a_poder_ir_pregunta_antes_de_cancelar(sesion: Session):
    por_whatsapp(sesion, vehiculo=cliente_con_vehiculo(sesion, telefono=CELULAR))
    cliente = chat(sesion, Interpretacion(Q.CANCELAR))

    cliente.escribe("ya no voy a poder ir")

    assert cliente.texto.startswith("¿Seguro que quieres cancelar tu turno *C-01*?")
    assert cliente.opciones == ["Sí, cancelar", "No, mantenerlo"]  # nada se cancela solo


@pytest.mark.parametrize(
    ("entendido", "empieza"),
    [
        (Interpretacion(Q.VER_FILA), "Así está la fila ahora:"),
        (Interpretacion(Q.MI_TURNO), "No tienes turnos activos."),
        (Interpretacion(Q.PEDIR_TURNO, vehiculo="Moto"), "Para darte turno guardamos tu nombre"),
        (Interpretacion(Q.PICO_Y_PLACA), "Por ahora no damos información de pico y placa"),
        (Interpretacion(Q.EN_CAMINO), "No tienes turnos activos."),
        (Interpretacion(Q.LLEGUE), "No encontramos tu celular en la fila de hoy."),
    ],
)
def test_cada_intencion_lleva_al_paso_correcto(sesion: Session, entendido, empieza):
    cliente = chat(sesion, entendido).escribe("algo con mis palabras de más de cuatro")

    assert cliente.texto.startswith(empieza)


def test_ya_estoy_afuera_con_sus_palabras_marca_la_llegada(sesion: Session):
    turno = por_whatsapp(sesion, vehiculo=cliente_con_vehiculo(sesion, telefono=CELULAR))
    cliente = chat(sesion, Interpretacion(Q.LLEGUE))

    cliente.escribe("ya estoy aquí afuera esperando con el carro")

    assert cliente.texto.startswith(f"¡Bienvenido! Ya te marcamos como llegado ({turno.numero})")


def test_si_gemini_no_responde_no_le_dice_no_te_entendi(sesion: Session, caplog):
    cliente = chat(sesion, None, None, RuntimeError("se rompió"))

    cliente.escribe("reciben nequi?")
    assert cliente.texto.startswith("¡Hola! Bienvenido a Auto Ibaguer.")  # primer mensaje
    cliente.escribe("y a qué hora abren?")
    assert cliente.texto.startswith("Uy, no alcancé a leer bien tu mensaje.")
    assert "llama al 322 755 6850" in cliente.texto
    assert "No te entendí" not in cliente.texto  # el mensaje estaba bien escrito
    assert cliente.opciones == MENU
    cliente.escribe("y dónde quedan?")
    assert cliente.texto.startswith("Uy, no alcancé a leer bien tu mensaje.")
    assert "Gemini falló; el bot siguió sin él." in caplog.text


def test_sin_gemini_conectado_si_dice_no_te_entendi(sesion: Session):
    cliente = Chat(sesion).escribe("hola")

    assert cliente.escribe("reciben nequi?").texto.startswith("No te entendí bien.")


def test_si_no_se_entiende_gemini_pide_que_lo_explique(sesion: Session):
    explica = "Uy, no te pillé 😅 ¿Me cuentas qué necesitas?"
    cliente = chat(sesion, None, Interpretacion(Q.OTRO, respuesta=explica), Interpretacion(Q.OTRO))
    cliente.escribe("hola").pasan(1)

    cliente.escribe("asdfgh")
    assert cliente.texto == explica
    assert cliente.opciones == MENU  # sin entender qué quiere, los botones ayudan
    cliente.escribe("kjsdhfk")  # y si no escribió nada, el texto fijo
    assert cliente.texto.startswith("No te entendí bien.")


def test_un_saludo_va_a_gemini_y_las_ordenes_cortas_no(sesion: Session):
    cliente = chat(sesion)

    cliente.escribe("Buenas tardes")
    cliente.escribe("mi turno")
    cliente.toca("Pedir turno")
    cliente.escribe("sí porfa")  # un sí claro al aviso de datos
    cliente.escribe("me llamo Ana Ruiz")  # el nombre lo escribe en su paso

    assert cliente.gemini.mensajes == ["Buenas tardes"]  # para contestar el saludo
    assert paso(sesion) == "vehiculo"


def test_el_saludo_lo_dice_gemini_con_el_menu(sesion: Session):
    saludo = "¡Hola! Muy bien, gracias. Bienvenido a Auto Ibaguer 🚗"
    cliente = chat(sesion, Interpretacion(Q.SALUDO, respuesta=saludo))

    cliente.escribe("hola, cómo estás?")

    assert cliente.texto == saludo
    assert cliente.opciones == MENU  # el saludo es la puerta de entrada
    [contexto] = cliente.gemini.contextos
    guias = dict(contexto.guias)
    assert guias["saludo"].startswith("¡Hola! Bienvenido a Auto Ibaguer. Por WhatsApp damos")
    assert guias["despedida"] == "¡Con gusto! Que tengas un buen día."


def test_escribir_la_camionetica_elige_esa_opcion(sesion: Session):
    cliente = chat(sesion, None, Interpretacion(Q.PEDIR_TURNO, vehiculo="Camioneta"))
    cliente.escribe("hola").toca("Pedir turno").toca("Acepto").escribe("Ana Ruiz")

    cliente.escribe("en la camionetica")

    assert cliente.texto.startswith("¿Cuál es la placa de tu camioneta?")
    [_, contexto] = cliente.gemini.contextos  # el primero fue el "hola"
    assert contexto.en_que_va == (
        "Está pidiendo turno. El bot le preguntó qué vehículo trae. "
        "Opciones: Automóvil | Camioneta | Moto."
    )


def test_escribir_el_servicio_con_sus_palabras_lo_elige(sesion: Session):
    cliente = chat(sesion, None, Interpretacion(Q.OTRO, servicio="Encerado"))
    cliente.escribe("hola").toca("Pedir turno").toca("Acepto").escribe("Ana Ruiz")
    cliente.toca("Automóvil").toca("Ahora no")

    cliente.escribe("el que trae cera")

    assert "*Servicio:* Encerado" in cliente.texto
    assert paso(sesion) == "confirmar"


def test_preguntar_cuanto_vale_un_servicio_no_lo_elige(sesion: Session):
    cliente = chat(sesion, None, Interpretacion(Q.PRECIOS, servicio="Encerado"))
    cliente.escribe("hola").toca("Pedir turno").toca("Acepto").escribe("Ana Ruiz")
    cliente.toca("Automóvil").toca("Ahora no")

    cliente.escribe("cuánto vale el encerado?")

    assert paso(sesion) == "servicio"  # la lista ya trae los precios: sigue ahí


def test_en_medio_de_una_pregunta_puede_cambiar_de_tema(sesion: Session):
    cliente = chat(sesion, None, Interpretacion(Q.VER_FILA))
    cliente.escribe("hola").toca("Pedir turno").toca("Acepto").escribe("Ana Ruiz")

    cliente.escribe("antes dígame si está muy lleno")

    assert cliente.texto.startswith("Así está la fila ahora:")


def test_preguntar_el_precio_mientras_pide_turno_repite_la_pregunta(sesion: Session):
    cliente = chat(sesion, None, Interpretacion(Q.PRECIOS))
    cliente.escribe("hola").toca("Pedir turno").toca("Acepto").escribe("Ana Ruiz")

    cliente.escribe("y eso cuánto vale?")

    assert cliente.texto.startswith("Toca una de las opciones, por favor.")
    assert paso(sesion) == "vehiculo"


# --- Las decisiones que no se deshacen ------------------------------------------------------------


def test_un_si_claro_acepta_los_datos_sin_preguntarle_a_gemini(sesion: Session):
    cliente = chat(sesion)
    cliente.escribe("hola").toca("Pedir turno")

    cliente.escribe("pues sí, de una")

    assert cliente.gemini.mensajes == ["hola"]
    assert paso(sesion) == "nombre"


def test_el_si_que_entiende_gemini_no_basta_para_dar_los_datos(sesion: Session):
    cliente = chat(sesion, None, Interpretacion(Q.PEDIR_TURNO, si_o_no=SI))
    cliente.escribe("hola").toca("Pedir turno")

    cliente.escribe("bueno, si toca, toca")

    assert cliente.texto.startswith("Para eso, toca *Acepto*, por favor.")
    assert cliente.opciones == ["Acepto", "No acepto"]
    assert paso(sesion) == "aviso_datos"


def test_el_no_que_entiende_gemini_si_vale(sesion: Session):
    cliente = chat(sesion, None, Interpretacion(Q.CHARLA, si_o_no=NO))
    cliente.escribe("hola").toca("Pedir turno")

    cliente.escribe("uy no, a mí no me gusta dar mis datos")

    assert "Sin tu autorización no guardamos tus datos" in cliente.texto
    assert paso(sesion) == "menu"


def test_en_el_resumen_el_si_que_entiende_gemini_confirma(sesion: Session):
    cliente_con_vehiculo(sesion, telefono=CELULAR, placa="ABC123")
    cliente = chat(sesion, Interpretacion(Q.PEDIR_TURNO, si_o_no=SI))
    cliente.escribe("mi turno").toca("Pedir turno").toca("Lo decido en el local")

    cliente.escribe("hágale pues mijo, resérvemelo")

    assert "Tu turno es *C-01*." in cliente.texto


def test_cancelar_con_un_si_de_gemini_tampoco(sesion: Session):
    por_whatsapp(sesion, vehiculo=cliente_con_vehiculo(sesion, telefono=CELULAR))
    cliente = chat(sesion, Interpretacion(Q.CANCELAR, si_o_no=SI))
    cliente.escribe("cancelar")

    cliente.escribe("hágale pues, ya qué")

    assert cliente.texto.startswith("Para eso, toca *Sí, cancelar*, por favor.")
    assert paso(sesion) == "cancelar"


# --- Lo que antes contestaba una palabra suelta ---------------------------------------------------


def test_cancelar_con_nequi_es_pagar_y_no_cancela_el_turno(sesion: Session):
    por_whatsapp(sesion, vehiculo=cliente_con_vehiculo(sesion, telefono=CELULAR))
    poner(sesion, "ficha_negocio", "Formas de pago: efectivo y Nequi.")
    contesta = "Sí, puedes pagar con Nequi o en efectivo."
    cliente = chat(sesion, Interpretacion(Q.PREGUNTA, respuesta=contesta))

    cliente.escribe("puedo cancelar con nequi?")

    assert cliente.texto == contesta
    assert cliente.gemini.mensajes == ["puedo cancelar con nequi?"]


def test_gracias_y_una_pregunta_no_es_una_despedida(sesion: Session):
    contesta = "¡Con gusto! Mañana también abrimos, desde las 5:30 a.m."
    cliente = chat(sesion, Interpretacion(Q.PREGUNTA, respuesta=contesta))

    cliente.escribe("gracias, y abren mañana?")

    assert cliente.texto == contesta


def test_gracias_sola_se_despide_con_sus_palabras(sesion: Session):
    cliente = chat(sesion, Interpretacion(Q.DESPEDIDA, respuesta="¡A ti! Que te vaya bien."))

    cliente.escribe("gracias")

    assert cliente.texto == "¡A ti! Que te vaya bien."
    assert cliente.opciones == []


@pytest.mark.parametrize(
    ("frase", "empieza"),
    [
        ("quiero cancelar mi turno", "¿Seguro que quieres cancelar tu turno *C-01*?"),
        ("cancelar", "¿Seguro que quieres cancelar tu turno *C-01*?"),
        ("mi turno", "Tu turno *C-01*"),
        ("ya llegué", "¡Bienvenido! Ya te marcamos como llegado (C-01)"),
    ],
)
def test_las_ordenes_cortas_no_gastan_una_consulta(sesion: Session, frase, empieza):
    por_whatsapp(sesion, vehiculo=cliente_con_vehiculo(sesion, telefono=CELULAR))
    cliente = chat(sesion)

    cliente.escribe(frase)

    assert cliente.texto.startswith(empieza)
    assert cliente.gemini.mensajes == []


def test_ya_no_voy_para_alla_no_avisa_que_va_en_camino(sesion: Session):
    por_whatsapp(sesion, vehiculo=cliente_con_vehiculo(sesion, telefono=CELULAR))
    cliente = chat(sesion, Interpretacion(Q.CANCELAR))

    cliente.escribe("ya no voy para allá, me salió algo")

    assert cliente.texto.startswith("¿Seguro que quieres cancelar tu turno")
    assert cliente.gemini.mensajes == ["ya no voy para allá, me salió algo"]
    cliente.escribe("No, mantenerlo")  # y "voy para allá" sigue siendo en camino
    cliente.escribe("listo, voy para allá")
    assert cliente.texto.startswith("Gracias por avisar, te esperamos.")


# --- Un vehículo que el lavadero no tiene (pruebas A, G y H) --------------------------------


def test_lavan_tractomulas_dice_que_vehiculos_si_lava(sesion: Session):
    contesta = "Por ahora lavamos automóviles, camionetas y motos; tractomulas no."
    cliente = chat(sesion, Interpretacion(Q.PREGUNTA, OTRO_VEHICULO, respuesta=contesta))

    cliente.escribe("puedes lavar camiones o trasctomulas??")

    assert cliente.texto == contesta
    assert "No te entendí" not in cliente.texto


def test_el_precio_de_una_tractomula_no_se_inventa(sesion: Session):
    cliente = chat(sesion, Interpretacion(Q.PRECIOS, OTRO_VEHICULO))  # no escribió nada

    cliente.escribe("cuánto cuesta lavar una tractomula?")

    assert cliente.texto == (
        "Por ahora lavamos automóvil, camioneta y moto: para ese vehículo no tenemos servicio."
    )
    assert cliente.opciones == ["Servicios y precios"]
    assert "Salió el texto fijo «vehiculo_que_no_lava»." in cliente.traza.texto()


# --- En medio de una pregunta del bot (pruebas C y F) ---------------------------------------------


def test_una_pregunta_en_medio_del_turno_se_contesta_y_vuelve_a_preguntar(sesion: Session):
    porque = "La pedimos por seguridad, para entregarte tu vehículo a ti."
    cliente = chat(sesion, None, Interpretacion(Q.PREGUNTA, respuesta=porque))
    cliente.escribe("hola").toca("Pedir turno").toca("Acepto").escribe("Ana Ruiz")
    cliente.toca("Moto")

    cliente.escribe("para qué necesitan la placa?")

    assert cliente.texto.startswith(f"{porque}\n\n¿Cuál es la placa de tu moto?")
    assert cliente.opciones == ["Ahora no"]
    assert paso(sesion) == "placa"


def test_hablar_del_clima_mientras_elige_el_servicio_no_pierde_el_turno(sesion: Session):
    clima = "Uy, el clima de hoy no lo tengo por aquí 😅"
    cliente = chat(
        sesion, Interpretacion(Q.PEDIR_TURNO, "Moto"), Interpretacion(Q.CHARLA, respuesta=clima)
    )
    cliente_con_vehiculo(sesion, telefono=CELULAR, tipo="Moto", placa="ABC12D")
    cliente.escribe("quiero lavar mi moto")
    assert paso(sesion) == "servicio"

    cliente.escribe("por cierto, cómo estuvo el clima hoy?")

    assert cliente.texto.startswith(f"{clima}\n\n¿Qué servicio quieres para tu moto?")
    assert paso(sesion) == "servicio"


def test_el_nombre_no_puede_ser_una_pregunta(sesion: Session):
    porque = "Es para anotar el turno a tu nombre y llamarte cuando te toque."
    cliente = chat(sesion, None, Interpretacion(Q.PREGUNTA, respuesta=porque))
    cliente.escribe("hola").toca("Pedir turno").toca("Acepto")

    cliente.escribe("para qué quieren mi nombre?")

    assert cliente.texto.startswith(f"{porque}\n\n")
    assert paso(sesion) == "nombre"


def test_sin_gemini_una_pregunta_tampoco_se_vuelve_el_nombre(sesion: Session):
    cliente = Chat(sesion).escribe("hola").toca("Pedir turno").toca("Acepto")

    cliente.escribe("¿para qué?")

    assert cliente.texto == "No alcancé a leer el nombre. ¿Me lo escribes otra vez?"
    assert paso(sesion) == "nombre"


# --- La traza: cómo se decidió cada respuesta -----------------------------------------------------


def test_la_traza_dice_si_le_pregunto_a_gemini_y_por_que_salio_un_texto_fijo(sesion: Session):
    cliente = chat(
        sesion,
        Interpretacion(Q.CHARLA, respuesta="Te damos un lavado gratis."),  # rompe una regla
        None,
    )

    cliente.escribe("te quiero mucho")
    traza = cliente.traza.texto()
    assert traza.startswith("Gemini respondió en ")
    assert "entendió «charla»." in traza
    assert "Lo que escribió no pasó la revisión: prometía algo que la ficha no dice" in traza
    assert traza.endswith("Salió el texto fijo «charla».")

    cliente.escribe("y si lo pido ya?")
    assert cliente.traza.texto().startswith("Gemini no respondió (")
    assert "no respondió (de mentira)." in cliente.traza.texto()

    cliente.toca("Ver la fila")
    assert cliente.traza.texto() == "Tocó un botón: el bot no le preguntó a Gemini."


# --- Configuración ---------------------------------------------------------------------------


def test_configuracion_muestra_gemini_y_su_estado(adentro: TestClient, entorno):
    entorno(gemini_clave="", gemini_plan_de_pago=False)

    pagina = adentro.get("/configuracion").text

    assert "Entender lo que escriben (Gemini)" in pagina
    assert 'name="modelo_gemini" value="gemini-3.5-flash-lite"' in pagina
    assert "Falta GEMINI_CLAVE en el archivo .env." in pagina


def test_un_modelo_mal_escrito_no_se_guarda(adentro: TestClient, sesion: Session):
    from pruebas.test_pantallas_clientes_y_configuracion import formulario_de_configuracion

    formulario = formulario_de_configuracion(sesion, modelo_gemini="gemini 3 flash")

    respuesta = adentro.post("/configuracion", data=formulario)

    assert "Escribe el nombre del modelo como sale en Google AI Studio" in respuesta.text
    assert leer_parametros(sesion).modelo_gemini == "gemini-3.5-flash-lite"


# --- Las frases de prueba ----------------------------------------------------------------------


def test_las_frases_de_prueba_hablan_del_menu_de_verdad(sesion: Session):
    catalogo = catalogo_del_bot(sesion)
    for frase in FRASES:
        assert frase.vehiculo in (None, CARRO, OTRO_VEHICULO, *catalogo.vehiculos), frase.texto
        assert frase.servicio in (None, *catalogo.servicios), frase.texto
    assert {frase.intencion for frase in FRASES} == set(Intencion)  # todas tienen frases
    # Las pruebas obligatorias del documento del hito 7c (la J es la memoria, parte 3).
    assert {frase.prueba for frase in FRASES if frase.prueba} == set("ABCDEFGHI")


def test_el_comando_corre_las_frases_con_una_consulta_cada_una(
    monkeypatch: pytest.MonkeyPatch, entorno, conexion, capsys
):
    entorno(gemini_clave="clave")
    monkeypatch.setattr(cli, "obtener_motor", lambda: conexion)  # la base de pruebas
    escribe = {
        Q.SALUDO: "¡Hola! Bienvenido a Auto Ibaguer.",
        Q.DESPEDIDA: "¡Con gusto!",
        Q.FELICITACION: "¡Gracias!",
        Q.CHARLA: "Con gusto, aquí estamos.",
        Q.OTRO: "¿Me lo explicas?",
    }
    esperadas = {
        f.texto: Interpretacion(
            f.intencion,
            f.vehiculo,
            f.servicio,
            si_o_no=f.si_o_no,
            respuesta=escribe.get(f.intencion, ""),
        )
        for f in FRASES
    }
    primera = FRASES[0].texto
    esperadas[primera] = Interpretacion(Q.OTRO)  # una sale mal (y no escribió nada)
    esperadas["te amo"] = Interpretacion(Q.CHARLA, respuesta="Te damos un lavado gratis.")
    esperadas["reciben nequi?"] = Interpretacion(
        Q.PREGUNTA, respuesta="Eso no lo tengo confirmado.", sabe=False
    )
    vio: dict[str, Contexto] = {}

    class GeminiQueSabe:
        def __init__(self, clave: str, modelo: str, **_) -> None:
            self.modelo = modelo
            self.ultimo_problema = None

        def interpretar(self, mensaje: str, contexto: Contexto) -> Interpretacion:
            vio[mensaje] = contexto
            return esperadas[mensaje]

    monkeypatch.setattr(gemini, "Gemini", GeminiQueSabe)

    assert cli.main(["probar-gemini", "--modelo", "gemini-3.6-flash", "--pausa", "0"]) == 1

    salida = capsys.readouterr().out
    assert f"Probando {len(FRASES)} frases con gemini-3.6-flash: una consulta por frase" in salida
    assert f"MAL    «{primera}» → esperaba intención pedir_turno, vehículo Moto" in salida
    assert "         no escribió la respuesta: sale un texto fijo" in salida
    assert f"{len(FRASES) - 1} de {len(FRASES)} bien" in salida
    # A la charla y a las preguntas les muestra lo que contestaría el bot.
    cansado = "  bien   «estoy cansado» → charla\n         contesta «Con gusto, aquí estamos.»"
    assert cansado in salida
    assert (
        "«reciben nequi?» → pregunta\n         no está en la ficha: dice «Eso no lo tengo "
        "confirmado.» y se la pasa al administrador"
    ) in salida
    assert "sale un texto fijo: lo que escribió prometía algo que la ficha no dice" in salida
    # Las pruebas del documento llevan su letra, y las de memoria y pasos lo dicen.
    assert "  bien   [A] «¿Puedes lavar camiones o tractomulas?» → pregunta, otro" in salida
    assert "  bien   «sí porfa» (con memoria) → pedir_turno" in salida
    assert "«para qué necesitan la placa?» (en medio de una pregunta del bot)" in salida
    [con_memoria] = [frase for frase in FRASES if frase.texto == "sí porfa"]
    assert vio["sí porfa"].antes == con_memoria.antes != ()
    assert vio["estoy cansado"].antes == ()
    assert "Vehículos que lava:" in vio["estoy cansado"].ficha
    guias = dict(vio["buenas"].guias)  # como a alguien que todavía no es cliente
    assert guias["despedida"] == "¡Con gusto! Que tengas un buen día."
    final = "Si dicen «429», es la cuota del plan gratis: espera un rato o sube la --pausa."
    assert salida.rstrip().endswith(final)  # el resumen sigue de último


def test_el_comando_sin_clave_dice_que_falta(entorno, capsys):
    entorno(gemini_clave="")

    assert cli.main(["probar-gemini"]) == 1
    assert "Falta GEMINI_CLAVE" in capsys.readouterr().err
