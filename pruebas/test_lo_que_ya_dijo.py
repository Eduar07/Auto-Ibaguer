"""Hito 7b, parte 5: el bot no vuelve a preguntar lo que el cliente ya dijo, y no saluda ni
se despide siempre igual.

Primero lo que ya dijo al pedir el turno con sus palabras (el vehículo, el servicio, la
placa) o con los precios que vio. Después los saludos, agradecimientos y despedidas que
Gemini dice con sus palabras, con el texto fijo cuando no se puede.
"""

import logging

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.bot.interprete import Anterior, Intencion, Interpretacion
from app.bot.textos import CON_SUS_PALABRAS, TEXTOS
from app.clientes import servicio as clientes
from app.clientes.reglas import placa_en_el_texto
from app.integraciones import gemini
from pruebas.fabricas import cliente_con_vehiculo, tipo_vehiculo
from pruebas.test_bot import CELULAR, MENU, Chat, paso
from pruebas.test_gemini import GoogleDeMentira, chat, con_google, respuesta_de_gemini
from pruebas.test_respuestas import charla

Q = Intencion
PIDE_TURNO = Q.PEDIR_TURNO


def pregunta_la_placa(vehiculo: str) -> str:
    return TEXTOS["pedir_placa"].format(vehiculo=vehiculo).split("?")[0] + "?"


# --- La placa en un mensaje ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("mensaje", "placa"),
    [
        ("quiero lavar la moto abc12d", "ABC12D"),
        ("mi placa es ABC 123", "ABC123"),
        ("es la ABC-123", "ABC123"),
        ("el carro por 120 mil", None),  # con espacio y en minúscula: no es una placa
        ("con 100 mil pesos", None),
        ("ABC123 o XYZ987, no me acuerdo", None),  # dos: no se adivina cuál
        ("hola, quiero lavar", None),
    ],
)
def test_la_placa_que_trae_un_mensaje(mensaje, placa):
    assert placa_en_el_texto(mensaje) == placa


# --- Lo que ya dijo al pedir el turno --------------------------------------------------------


def test_un_cliente_nuevo_que_ya_dijo_la_moto_y_la_placa_no_las_repite(sesion: Session):
    cliente = chat(sesion, Interpretacion(PIDE_TURNO, vehiculo="Moto"))

    cliente.escribe("buenas, quiero lavar la moto abc12d")
    assert cliente.opciones == ["Acepto", "No acepto"]  # el aviso de datos va siempre
    cliente.toca("Acepto").escribe("Ana Ruiz")

    # Sin preguntar el vehículo ni la placa: de una vez el servicio.
    assert cliente.texto.startswith("¿Qué servicio quieres para tu moto?")
    assert paso(sesion) == "servicio"
    cliente.toca("Lo decido en el local")
    assert "*Vehículo:* Moto ABC12D" in cliente.texto


def test_si_tiene_un_carro_y_dice_la_moto_no_le_pone_el_carro(sesion: Session):
    cliente_con_vehiculo(sesion, telefono=CELULAR, nombre="Ana Ruiz", placa="ABC123")
    cliente = chat(sesion, Interpretacion(PIDE_TURNO, vehiculo="Moto"))

    cliente.escribe("quiero lavar la moto")

    assert cliente.texto.startswith(pregunta_la_placa("moto"))  # una moto nueva
    assert paso(sesion) == "placa"


def test_si_tiene_carro_y_moto_y_dice_la_moto_usa_la_suya(sesion: Session):
    carro = cliente_con_vehiculo(sesion, telefono=CELULAR, nombre="Ana Ruiz", placa="ABC123")
    moto = tipo_vehiculo(sesion, "Moto").id
    clientes.registrar_vehiculo(
        sesion, cliente_id=carro.cliente_id, tipo_vehiculo_id=moto, placa="XYZ12A"
    )
    cliente = chat(sesion, Interpretacion(PIDE_TURNO, vehiculo="Moto"))

    cliente.escribe("quiero lavar la moto")
    cliente.toca("Lo decido en el local")

    assert "*Vehículo:* Moto XYZ12A" in cliente.texto


def test_la_placa_que_escribio_elige_entre_sus_vehiculos(sesion: Session):
    carro = cliente_con_vehiculo(sesion, telefono=CELULAR, nombre="Ana Ruiz", placa="ABC123")
    otro = tipo_vehiculo(sesion, "Automóvil").id
    clientes.registrar_vehiculo(
        sesion, cliente_id=carro.cliente_id, tipo_vehiculo_id=otro, placa="DEF456"
    )
    cliente = chat(sesion, Interpretacion(PIDE_TURNO, vehiculo="carro"))

    cliente.escribe("me separa un turno para el DEF456")
    cliente.toca("Lo decido en el local")

    assert "*Vehículo:* Automóvil DEF456" in cliente.texto


def test_el_servicio_que_nombro_tampoco_se_pregunta(sesion: Session):
    cliente_con_vehiculo(
        sesion, tipo="Camioneta", telefono=CELULAR, nombre="Ana Ruiz", placa="ABC123"
    )
    entendido = Interpretacion(PIDE_TURNO, vehiculo="Camioneta", servicio="General")
    cliente = chat(sesion, entendido)

    cliente.escribe("quiero un general para la camioneta")

    assert paso(sesion) == "confirmar"
    assert "*Servicio:* General · $140.000" in cliente.texto


def test_carro_sin_decir_cual_se_pregunta_si_tiene_dos(sesion: Session):
    carro = cliente_con_vehiculo(sesion, telefono=CELULAR, nombre="Ana Ruiz", placa="ABC123")
    camioneta = tipo_vehiculo(sesion, "Camioneta").id
    clientes.registrar_vehiculo(
        sesion, cliente_id=carro.cliente_id, tipo_vehiculo_id=camioneta, placa="DEF456"
    )
    cliente = chat(sesion, Interpretacion(PIDE_TURNO, vehiculo="carro"))

    cliente.escribe("quiero lavar el carro")

    assert cliente.texto == TEXTOS["pedir_vehiculo_conocido"]  # no se adivina cuál


def test_si_dice_carro_y_solo_tiene_una_moto_se_le_pregunta(sesion: Session):
    cliente_con_vehiculo(sesion, tipo="Moto", telefono=CELULAR, nombre="Ana", placa="ABC12D")
    cliente = chat(sesion, Interpretacion(PIDE_TURNO, vehiculo="carro"))

    cliente.escribe("quiero lavar el carro")

    assert cliente.texto == TEXTOS["pedir_vehiculo_conocido"]  # no le pone la moto
    assert cliente.opciones == ["Moto ABC12D", "Otro vehículo"]


def test_una_placa_que_no_es_de_ese_vehiculo_se_pregunta_sin_regannar(sesion: Session):
    cliente_con_vehiculo(sesion, telefono=CELULAR, nombre="Ana Ruiz", placa="ABC123")
    cliente = chat(sesion, Interpretacion(PIDE_TURNO, vehiculo="Moto"))

    cliente.escribe("quiero lavar la moto KLM456")  # tiene formato de carro

    assert cliente.texto.startswith(pregunta_la_placa("moto"))
    assert "formato" not in cliente.texto


def test_la_placa_de_otro_cliente_se_le_dice(sesion: Session):
    cliente_con_vehiculo(sesion, placa="KLM45F", tipo="Moto")  # de otra persona
    cliente_con_vehiculo(sesion, telefono=CELULAR, nombre="Ana Ruiz", placa="ABC123")
    cliente = chat(sesion, Interpretacion(PIDE_TURNO, vehiculo="Moto"))

    cliente.escribe("quiero lavar la moto KLM45F")

    assert "KLM45F" in cliente.texto and paso(sesion) == "placa"
    assert cliente.opciones == ["Ahora no"]


def test_los_precios_que_vio_son_para_el_turno(sesion: Session):
    cliente = Chat(sesion).toca_el_id("menu.precios")
    moto = tipo_vehiculo(sesion, "Moto").id
    cliente.toca_el_id(f"precios.{moto}")
    assert cliente.texto.startswith("Servicios para moto:")

    cliente.toca("Pedir turno").toca("Acepto").escribe("Ana Ruiz")

    assert cliente.texto.startswith(pregunta_la_placa("moto"))  # sin preguntar el vehículo


def test_si_ya_tiene_vehiculos_los_precios_que_vio_no_le_cambian_el_suyo(sesion: Session):
    cliente_con_vehiculo(sesion, telefono=CELULAR, nombre="Ana Ruiz", placa="ABC123")
    cliente = Chat(sesion).toca_el_id("menu.precios")
    cliente.toca_el_id(f"precios.{tipo_vehiculo(sesion, 'Moto').id}")

    cliente.toca("Pedir turno").toca("Lo decido en el local")

    assert "*Vehículo:* Automóvil ABC123" in cliente.texto  # el suyo, como siempre


def test_al_cambiar_datos_en_el_resumen_ya_lo_elige_el(sesion: Session):
    cliente_con_vehiculo(
        sesion, tipo="Camioneta", telefono=CELULAR, nombre="Ana Ruiz", placa="ABC123"
    )
    entendido = Interpretacion(PIDE_TURNO, vehiculo="Camioneta", servicio="General")
    cliente = chat(sesion, entendido)
    cliente.escribe("quiero un general para la camioneta")

    cliente.toca("Cambiar datos")

    assert cliente.texto == TEXTOS["pedir_vehiculo_conocido"]
    cliente.toca("Camioneta ABC123")
    assert paso(sesion) == "servicio"  # el General que dijo ya no se elige solo


# --- Saludos, agradecimientos y despedidas con sus palabras ----------------------------------


def ana(sesion: Session) -> None:
    cliente_con_vehiculo(sesion, telefono=CELULAR, nombre="Ana Ruiz", placa="ABC123")


SALUDO = TEXTOS["saludo_cliente"].format(nombre="Ana")


def test_con_gemini_el_saludo_no_es_siempre_igual(sesion: Session):
    ana(sesion)
    cliente = charla(sesion, [], [])
    cliente.redactor.dichos[SALUDO] = "¡Hola, Ana! Qué gusto verte otra vez 🙂"

    cliente.escribe("hola")

    assert cliente.texto == "¡Hola, Ana! Qué gusto verte otra vez 🙂"
    assert cliente.opciones == MENU  # el menú sigue: es la puerta de entrada
    assert cliente.redactor.con_sus_palabras_pedidos == [(SALUDO, "hola")]


def test_las_gracias_y_la_despedida_tambien(sesion: Session):
    ana(sesion)
    cliente = charla(sesion, [Interpretacion(Q.FELICITACION)], [])
    felicitacion = TEXTOS["felicitacion_cliente"].format(nombre="Ana")
    cliente.redactor.dichos[felicitacion] = "¡Qué alegría, Ana! Se lo contamos al equipo."
    cliente.redactor.dichos[TEXTOS["despedida"]] = "¡A ti, Ana! Que te vaya muy bien."

    cliente.escribe("la moto quedó como nueva, los felicito")
    assert cliente.texto == "¡Qué alegría, Ana! Se lo contamos al equipo."
    assert cliente.opciones == []  # como la charla: sin el menú pegado

    cliente.escribe("gracias")
    assert cliente.texto == "¡A ti, Ana! Que te vaya muy bien."


@pytest.mark.parametrize(
    "dicho",
    [
        "¡Hola, Ana! Hoy tienes 20% de descuento.",  # una promesa
        "¡Hola, Ana! Te esperamos a las 8:15.",  # una hora que nadie dijo
        "Hola, soy una persona del lavadero.",
    ],
)
def test_si_lo_que_dice_rompe_una_regla_sale_el_texto_tal_cual(sesion: Session, dicho, caplog):
    ana(sesion)
    cliente = charla(sesion, [], [])
    cliente.redactor.dichos[SALUDO] = dicho

    with caplog.at_level(logging.WARNING):
        cliente.escribe("hola")

    assert cliente.texto == SALUDO
    assert dicho not in caplog.text  # nunca se escribe lo que dijo


def test_si_gemini_falla_o_no_dice_nada_sale_el_texto_tal_cual(sesion: Session, caplog):
    ana(sesion)
    cliente = charla(sesion, [], [])
    cliente.redactor.dichos[SALUDO] = RuntimeError("se cayó")

    with caplog.at_level(logging.ERROR):
        cliente.escribe("hola")
    assert cliente.texto == SALUDO
    assert "El redactor falló" in caplog.text

    del cliente.redactor.dichos[SALUDO]  # no responde
    assert cliente.escribe("hola").texto == SALUDO


def test_lo_que_lleva_datos_que_cuidar_sale_siempre_tal_cual(sesion: Session):
    ana(sesion)
    cliente = charla(sesion, [Interpretacion(Q.QUEJA)], [])

    cliente.escribe("me atendieron mal")

    assert cliente.texto.startswith("Qué pena, Ana, con lo que pasó.")
    assert cliente.redactor.con_sus_palabras_pedidos == []
    assert "queja_cliente" not in CON_SUS_PALABRAS


def test_sin_gemini_los_saludos_salen_tal_cual(sesion: Session):
    ana(sesion)

    assert Chat(sesion).escribe("hola").texto == SALUDO


# --- Cómo se le pide a Gemini ------------------------------------------------------------------


def test_a_gemini_se_le_pide_la_misma_idea_con_sus_palabras():
    antes = (Anterior("cliente", "ya quedó lista la moto?"), Anterior("bot", "Sí, ya está."))

    cuerpo = gemini.peticion_con_sus_palabras(
        "¡Con gusto! Que tengas un buen día.",
        "gracias",
        "FICHA",
        modelo="gemini-3.5-flash-lite",
        negocio="Auto Ibaguer",
        antes=antes,
    )

    instrucciones = cuerpo["systemInstruction"]["parts"][0]["text"]
    assert "El bot tiene que decir esto: «¡Con gusto! Que tengas un buen día.»" in instrucciones
    assert "sin agregar nada nuevo" in instrucciones
    assert "Un emoji como mucho" in instrucciones  # con la misma personalidad
    assert cuerpo["contents"][0]["parts"][0]["text"].endswith("Mensaje nuevo del cliente:\ngracias")


def test_gemini_lo_dice_con_sus_palabras_o_nada():
    dicho = {"respuesta": "¡A ti! Que te vaya bonito.", "sabe": True}
    google = GoogleDeMentira(respuesta_de_gemini(dicho), respuesta_de_gemini("no es json"))
    con_gemini = con_google(google)

    assert con_gemini.con_sus_palabras("¡Con gusto!", "gracias", "FICHA") == dicho["respuesta"]
    assert con_gemini.con_sus_palabras("¡Con gusto!", "gracias", "FICHA") is None


# --- Textos del bot ------------------------------------------------------------------------------


def test_textos_del_bot_dice_cuales_dice_con_sus_palabras(adentro: TestClient):
    pagina = adentro.get("/configuracion/textos").text

    def editor(clave: str) -> str:
        inicio = pagina.index(f'id="texto-{clave}"')
        return pagina[inicio : pagina.index("</details>", inicio)]

    nota = "Con Gemini conectado, el bot dice este mensaje con sus palabras"
    assert nota in editor("despedida")
    assert "Este mensaje no lleva datos." in editor("despedida")
    assert nota in editor("saludo_cliente")
    assert nota not in editor("saludo")  # el primer saludo lleva el horario: tal cual
    assert "Este mensaje no lleva datos: es siempre igual." in editor("menu")
