"""Gemini: el intérprete del texto libre, con un Gemini de mentira.

Google no se toca: lo que "responde Gemini" lo arma aquí un httpx.MockTransport, y en las
pruebas de la conversación un intérprete de mentira dice qué entendió. Las frases de
prueba contra el modelo de verdad se corren aparte: uv run auto-ibaguer probar-gemini.
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
from app.bot.conversacion import catalogo_del_bot
from app.bot.frases_de_prueba import FRASES
from app.bot.interprete import (
    CARRO,
    Catalogo,
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
from pruebas.test_bot import CELULAR, Chat, paso, poner

Q = Intencion
CATALOGO = Catalogo(
    ("Automóvil", "Camioneta", "Moto"),
    ("Básico", "Encerado", "General", "Premium", "Empelotado", "Intermedio"),
)


@pytest.fixture(autouse=True)
def avisos_de_cero(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cada prueba empieza sin problemas de Gemini ya anotados en el registro."""
    monkeypatch.setattr(gemini, "_ULTIMA_VEZ", {})


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
        {"intencion": "precios", "vehiculo": "Camioneta", "servicio": "Encerado"}, CATALOGO
    )
    assert entendido == Interpretacion(Q.PRECIOS, "Camioneta", "Encerado")

    inventado = leer_respuesta(
        {"intencion": "precios", "vehiculo": "Bus", "servicio": "Polichado"}, CATALOGO
    )
    assert inventado == Interpretacion(Q.PRECIOS)  # sin vehículo ni servicio inventados
    assert leer_respuesta({"intencion": "precios", "vehiculo": CARRO}, CATALOGO).vehiculo == CARRO
    assert leer_respuesta({"intencion": "vender_el_carro"}, CATALOGO) is None
    assert leer_respuesta(["precios"], CATALOGO) is None


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


def test_le_pide_a_gemini_una_respuesta_con_formato_fijo():
    google = GoogleDeMentira(
        respuesta_de_gemini(
            {"intencion": "precios", "vehiculo": "Camioneta", "servicio": "ninguno"}
        )
    )

    entendido = con_google(google).interpretar("cuánto vale lavar la camionetica ABC123", CATALOGO)

    assert entendido == Interpretacion(Q.PRECIOS, vehiculo="Camioneta")
    [peticion] = google.peticiones
    assert str(peticion.url) == (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        "gemini-3.5-flash-lite:generateContent"
    )
    assert peticion.headers["x-goog-api-key"] == "clave-de-prueba"  # la clave nunca en la URL
    cuerpo = google.cuerpo()
    assert cuerpo["contents"] == [
        {"role": "user", "parts": [{"text": "cuánto vale lavar la camionetica [placa]"}]}
    ]
    instrucciones = cuerpo["systemInstruction"]["parts"][0]["text"]
    assert "lavadero de carros y motos" in instrucciones and "Auto Ibaguer" in instrucciones
    # Los nombres de la API de AI Studio: "responseFormat" es solo de Vertex AI y el nivel
    # de pensamiento va dentro de thinkingConfig (la librería oficial de Google, google-genai).
    configuracion = cuerpo["generationConfig"]
    assert configuracion["responseMimeType"] == "application/json"
    propiedades = configuracion["responseJsonSchema"]["properties"]
    assert propiedades["intencion"]["enum"] == [i.value for i in Intencion]
    assert propiedades["vehiculo"]["enum"] == ["Automóvil", "Camioneta", "Moto", "carro", "ninguno"]
    assert configuracion["thinkingConfig"] == {"thinkingLevel": "MINIMAL"}
    assert "responseFormat" not in configuracion
    assert "thinkingLevel" not in configuracion


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
    google = GoogleDeMentira(
        respuesta_de_gemini({"intencion": "otro", "vehiculo": "ninguno", "servicio": "ninguno"})
    )

    con_google(google, modelo).interpretar("hola", CATALOGO)

    configuracion = google.cuerpo()["generationConfig"]
    assert configuracion["thinkingConfig"] == pensamiento
    assert "thinkingLevel" not in configuracion


def test_un_modelo_2_x_pro_piensa_lo_que_necesita():
    google = GoogleDeMentira(
        respuesta_de_gemini({"intencion": "otro", "vehiculo": "ninguno", "servicio": "ninguno"})
    )

    con_google(google, "gemini-2.5-pro").interpretar("hola", CATALOGO)

    assert "thinkingConfig" not in google.cuerpo()["generationConfig"]  # no se puede apagar


def test_lee_la_respuesta_aunque_traiga_lo_que_penso_o_venga_entre_comillas():
    entre_comillas = (
        '```json\n{"intencion": "ver_fila", "vehiculo": "ninguno", "servicio": "ninguno"}\n```'
    )
    google = GoogleDeMentira(
        respuesta_de_gemini(
            {"intencion": "cancelar", "vehiculo": "ninguno", "servicio": "ninguno"},
            con_pensamiento=True,
        ),
        respuesta_de_gemini(entre_comillas),
    )
    interprete = con_google(google)

    assert interprete.interpretar("ya no voy", CATALOGO) == Interpretacion(Q.CANCELAR)
    assert interprete.interpretar("hay mucha gente?", CATALOGO) == Interpretacion(Q.VER_FILA)


@pytest.mark.parametrize(
    ("respuesta", "problema"),
    [
        (httpx.ReadTimeout("lento"), "no respondió en 5 segundos."),
        (httpx.ConnectError("sin internet"), "no se pudo conectar (ConnectError)."),
        (
            httpx.Response(429, json={"error": {"message": "Resource exhausted"}}),
            "se acabó la cuota",
        ),
        (
            httpx.Response(400, json={"error": {"message": "API key not valid."}}),
            "respondió 400: API key not valid.",
        ),
        (httpx.Response(500, text="<html>error</html>"), "respondió 500."),
        (httpx.Response(200, json={"promptFeedback": {"blockReason": "SAFETY"}}), "formato pedido"),
        (respuesta_de_gemini("no sé qué decir"), "formato pedido"),
    ],
)
def test_si_gemini_falla_el_bot_sigue_y_queda_anotado_por_que(respuesta, problema, caplog):
    interprete = con_google(GoogleDeMentira(respuesta))

    with caplog.at_level(logging.WARNING, logger="app.integraciones.gemini"):
        assert interprete.interpretar("ya llegué con la moto ABC12D", CATALOGO) is None

    assert problema in interprete.ultimo_problema
    assert problema in caplog.text and "El bot siguió con los botones." in caplog.text
    assert "ABC12D" not in caplog.text and "moto" not in caplog.text  # nunca el mensaje


def test_un_mismo_problema_se_anota_una_vez_y_no_con_cada_mensaje(caplog):
    google = GoogleDeMentira(*[httpx.ReadTimeout("lento")] * 3)
    interprete = con_google(google)

    with caplog.at_level(logging.WARNING, logger="app.integraciones.gemini"):
        for _ in range(3):
            interprete.interpretar("hola", CATALOGO)

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
    """Dice lo que se le pida que entendió, y anota qué le preguntaron."""

    def __init__(self, *entendidos: Interpretacion | None | Exception) -> None:
        self.entendidos = list(entendidos)
        self.mensajes: list[str] = []
        self.catalogos: list[Catalogo] = []

    def interpretar(self, mensaje: str, catalogo: Catalogo) -> Interpretacion | None:
        self.mensajes.append(mensaje)
        self.catalogos.append(catalogo)
        entendido = self.entendidos.pop(0) if self.entendidos else None
        if isinstance(entendido, Exception):
            raise entendido
        return entendido


class ChatConGemini(Chat):
    def __init__(self, sesion: Session, gemini_de_mentira: GeminiDeMentira, **datos) -> None:
        super().__init__(sesion, **datos)
        self.gemini = gemini_de_mentira

    def _enviar(self, entrada: Entrada) -> "Chat":
        self.ultimas = conversacion.atender(
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
    assert cliente.gemini.catalogos[0] == catalogo_del_bot(sesion)


def test_si_dice_carro_pregunta_si_es_automovil_o_camioneta(sesion: Session):
    cliente = chat(sesion, Interpretacion(Q.PRECIOS, vehiculo=CARRO))

    cliente.escribe("a cómo lavan un carro?")

    assert cliente.texto == "¿Automóvil o camioneta?"
    assert cliente.opciones == ["Automóvil", "Camioneta"]


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
    ],
)
def test_cada_intencion_lleva_al_paso_correcto(sesion: Session, entendido, empieza):
    cliente = chat(sesion, entendido).escribe("algo con mis palabras")

    assert cliente.texto.startswith(empieza)


def test_si_gemini_no_entiende_o_falla_el_bot_sigue_con_botones(sesion: Session, caplog):
    cliente = chat(sesion, Interpretacion(Q.OTRO), None, RuntimeError("se rompió"))

    cliente.escribe("reciben nequi?")
    assert cliente.texto.startswith("¡Hola! Bienvenido a Auto Ibaguer.")  # primer mensaje
    cliente.escribe("y a qué hora abren?")
    assert cliente.texto.startswith("No te entendí bien.")
    cliente.escribe("y dónde quedan?")
    assert cliente.texto.startswith("No te entendí bien.")
    assert "El intérprete falló; el bot siguió con los botones." in caplog.text


def test_un_saludo_y_las_palabras_que_ya_conoce_no_van_a_gemini(sesion: Session):
    cliente = chat(sesion)

    cliente.escribe("Buenas tardes")
    cliente.escribe("mi turno")
    cliente.toca("Pedir turno")
    cliente.toca("Acepto")
    cliente.escribe("Ana Ruiz")  # el nombre lo escribe en su paso: no se interpreta

    assert cliente.gemini.mensajes == []
    assert paso(sesion) == "vehiculo"


def test_escribir_la_camionetica_elige_esa_opcion(sesion: Session):
    cliente = chat(sesion, Interpretacion(Q.PEDIR_TURNO, vehiculo="Camioneta"))
    cliente.escribe("hola").toca("Pedir turno").toca("Acepto").escribe("Ana Ruiz")

    cliente.escribe("en la camionetica")

    assert cliente.texto.startswith("¿Cuál es la placa de tu camioneta?")


def test_escribir_el_servicio_con_sus_palabras_lo_elige(sesion: Session):
    cliente = chat(sesion, Interpretacion(Q.OTRO, servicio="Encerado"))
    cliente.escribe("hola").toca("Pedir turno").toca("Acepto").escribe("Ana Ruiz")
    cliente.toca("Automóvil").toca("Ahora no")

    cliente.escribe("el que trae cera")

    assert "*Servicio:* Encerado" in cliente.texto
    assert paso(sesion) == "confirmar"


def test_en_medio_de_una_pregunta_puede_cambiar_de_tema(sesion: Session):
    cliente = chat(sesion, Interpretacion(Q.VER_FILA))
    cliente.escribe("hola").toca("Pedir turno").toca("Acepto").escribe("Ana Ruiz")

    cliente.escribe("antes dígame si está muy lleno")

    assert cliente.texto.startswith("Así está la fila ahora:")


def test_preguntar_el_precio_mientras_pide_turno_repite_la_pregunta(sesion: Session):
    cliente = chat(sesion, Interpretacion(Q.PRECIOS))
    cliente.escribe("hola").toca("Pedir turno").toca("Acepto").escribe("Ana Ruiz")

    cliente.escribe("y eso cuánto vale?")

    assert cliente.texto.startswith("Toca una de las opciones, por favor.")
    assert paso(sesion) == "vehiculo"


def test_aceptar_los_datos_y_confirmar_nunca_los_decide_gemini(sesion: Session):
    cliente = chat(sesion, Interpretacion(Q.PEDIR_TURNO))
    cliente.escribe("hola").toca("Pedir turno")

    cliente.escribe("pues sí, de una")  # no es un sí claro: toca el botón

    assert cliente.texto.startswith("Toca una de las opciones, por favor.")
    assert cliente.gemini.mensajes == []
    assert paso(sesion) == "aviso_datos"


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
        assert frase.vehiculo in (None, CARRO, *catalogo.vehiculos), frase.texto
        assert frase.servicio in (None, *catalogo.servicios), frase.texto
    assert {frase.intencion for frase in FRASES} == set(Intencion)  # todas tienen frases


def test_el_comando_corre_las_frases_y_dice_cuantas_salieron_bien(
    monkeypatch: pytest.MonkeyPatch, entorno, conexion, capsys
):
    entorno(gemini_clave="clave")
    monkeypatch.setattr(cli, "obtener_motor", lambda: conexion)  # la base de pruebas
    esperadas = {f.texto: Interpretacion(f.intencion, f.vehiculo, f.servicio) for f in FRASES}
    primera = FRASES[0].texto
    esperadas[primera] = Interpretacion(Q.OTRO)  # una sale mal

    class GeminiQueSabe:
        def __init__(self, clave: str, modelo: str, **_) -> None:
            self.modelo = modelo
            self.ultimo_problema = None

        def interpretar(self, mensaje: str, _catalogo: Catalogo) -> Interpretacion:
            return esperadas[mensaje]

    monkeypatch.setattr(gemini, "Gemini", GeminiQueSabe)

    assert cli.main(["probar-gemini", "--modelo", "gemini-3.6-flash", "--pausa", "0"]) == 1

    salida = capsys.readouterr().out
    assert f"Probando {len(FRASES)} frases con gemini-3.6-flash:" in salida
    assert f"MAL    «{primera}» → esperaba intención pedir_turno" in salida
    assert f"{len(FRASES) - 1} de {len(FRASES)} bien" in salida


def test_el_comando_sin_clave_dice_que_falta(entorno, capsys):
    entorno(gemini_clave="")

    assert cli.main(["probar-gemini"]) == 1
    assert "Falta GEMINI_CLAVE" in capsys.readouterr().err


# --- Las palabras que el bot ya conocía ------------------------------------------------------


def test_cancelar_mi_turno_es_cancelar(sesion: Session):
    por_whatsapp(sesion, vehiculo=cliente_con_vehiculo(sesion, telefono=CELULAR))

    cliente = chat(sesion).escribe("quiero cancelar mi turno")

    assert cliente.texto.startswith("¿Seguro que quieres cancelar tu turno *C-01*?")


def test_ya_no_voy_para_alla_no_avisa_que_va_en_camino(sesion: Session):
    por_whatsapp(sesion, vehiculo=cliente_con_vehiculo(sesion, telefono=CELULAR))
    cliente = chat(sesion, Interpretacion(Q.CANCELAR))

    cliente.escribe("ya no voy para allá, me salió algo")

    assert cliente.texto.startswith("¿Seguro que quieres cancelar tu turno")
    assert cliente.gemini.mensajes == ["ya no voy para allá, me salió algo"]
    cliente.escribe("No, mantenerlo")  # y "voy para allá" sigue siendo en camino
    cliente.escribe("listo, voy para allá")
    assert cliente.texto.startswith("Gracias por avisar, te esperamos.")
