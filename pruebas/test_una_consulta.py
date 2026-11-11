"""Hito 7c, parte 1: Gemini lee todo lo que el cliente escribe con sus palabras, en una sola
consulta, y el bot solo se adelanta en lo que no deja dudas.

Aquí van las piezas que deciden cuándo no hace falta Gemini (un sí o un no, una orden
corta, el nombre, la placa) y la traza que ve el administrador en Probar el bot.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.bot import conversacion
from app.bot.conversacion import (
    _el_nombre,
    _es_corto,
    _la_placa,
    _no_tiene_la_placa,
    _pide_cancelar,
    normalizar,
    si_o_no,
)
from app.bot.interprete import NO, SI, Intencion, Interpretacion
from app.integraciones import gemini
from pruebas.fabricas import LUNES_8AM
from pruebas.test_bot import paso
from pruebas.test_gemini import GeminiDeMentira
from pruebas.test_probar_bot import CELULAR, escribir

Q = Intencion


@pytest.mark.parametrize(
    ("escrito", "respuesta"),
    [
        ("sí", SI),
        ("Sí, porfa", SI),
        ("dale pues", SI),
        ("de una", SI),
        ("claro que sí, señor", SI),
        ("listo, de acuerdo", SI),
        ("ok dale, acepto los términos", SI),
        ("no", NO),
        ("No, gracias", NO),
        ("mejor no", NO),
        ("nop", NO),
        ("ya no", NO),
        ("sí, pero ¿para qué es?", None),  # pregunta algo más: eso lo lee Gemini
        ("no sé", None),
        ("sí no", None),
        ("gracias", None),  # agradece, no contesta
        ("bueno y cuánto cuesta", None),
        ("", None),
    ],
)
def test_un_si_o_un_no_claros(escrito, respuesta):
    assert si_o_no(normalizar(escrito)) == respuesta


@pytest.mark.parametrize(
    ("escrito", "cancela"),
    [
        ("cancelar", True),
        ("quiero cancelar mi turno", True),
        ("Hola, cancela mi turno por favor", True),
        ("cancelen el turno", True),
        ("¿puedo cancelar con Nequi?", False),  # en Colombia, cancelar también es pagar
        ("cancelo allá?", False),
        ("ya no voy, cancelen", False),  # eso lo entiende Gemini
    ],
)
def test_pedir_cancelar_es_una_orden_y_pagar_no(escrito, cancela):
    assert _pide_cancelar(normalizar(escrito)) is cancela


@pytest.mark.parametrize(
    ("escrito", "corto"),
    [
        ("mi turno", True),
        ("hola, ya llegué, gracias", True),
        ("ya llegué a la casa y vi el rayón", False),
        ("gracias, y abren mañana?", True),
    ],
)
def test_una_orden_corta(escrito, corto):
    assert _es_corto(normalizar(escrito)) is corto


@pytest.mark.parametrize(
    ("escrito", "nombre"),
    [
        ("Ana Ruiz", "Ana Ruiz"),
        ("me llamo Ana", "Ana"),
        ("Soy Pedro Gómez", "Pedro Gómez"),
        ("Buenos días, mi nombre es Luz Marina", "Luz Marina"),
        ("a nombre de Carlos", "Carlos"),
        ("Don Pedro", "Don Pedro"),
        ("¿para qué?", None),  # una pregunta: la lee Gemini
        ("para que quieren mi nombre", None),
        ("cuánto cuesta", None),
        ("hola", None),
        ("sí", None),
        ("no quiero dar mi nombre", None),
        ("mi placa es ABC123", None),
        ("uno dos tres cuatro cinco seis", None),
    ],
)
def test_lo_que_parece_un_nombre(escrito, nombre):
    assert _el_nombre(escrito, normalizar(escrito)) == nombre


@pytest.mark.parametrize(
    ("escrito", "placa"),
    [
        ("ABC123", "ABC123"),
        ("es la abc12d", "ABC12D"),
        ("abc 123", "abc 123"),  # validar_placa lo limpia
        ("1234", "1234"),  # un intento de placa: validar_placa explica el formato
        ("para qué necesitan la placa?", None),
        ("no la tengo", None),
    ],
)
def test_lo_que_parece_una_placa(escrito, placa):
    assert _la_placa(escrito) == placa


@pytest.mark.parametrize(
    ("escrito", "sin_placa"),
    [
        ("no la tengo", True),
        ("no me acuerdo", True),
        ("después se la doy", True),
        ("no", True),
        ("no sé para qué la quieren de verdad", False),  # largo: lo lee Gemini
        ("para qué la quieren?", False),
    ],
)
def test_no_tener_la_placa_a_mano(escrito, sin_placa):
    assert _no_tiene_la_placa(escrito, normalizar(escrito)) is sin_placa


# --- Probar el bot muestra cómo se decidió cada respuesta ---------------------------------------


def test_probar_el_bot_muestra_si_respondio_gemini(
    adentro: TestClient, monkeypatch: pytest.MonkeyPatch
):
    de_mentira = GeminiDeMentira(
        Interpretacion(Q.PREGUNTA, respuesta="Por ahora lavamos automóviles, camionetas y motos."),
        None,
    )
    de_mentira.ultimo_problema = None
    monkeypatch.setattr(gemini, "para_el_bot", lambda _parametros: de_mentira)

    respuesta = escribir(adentro, texto="lavan tractomulas?")

    assert "Por ahora lavamos automóviles, camionetas y motos." in respuesta
    assert '<p class="charla__traza">' in respuesta
    assert "Gemini respondió en " in respuesta and "entendió «pregunta»." in respuesta

    respuesta = escribir(adentro, texto="y buses?")
    assert "Uy, no alcancé a leer bien tu mensaje." in respuesta
    assert "Gemini no respondió (" in respuesta
    assert "Salió el texto fijo «sin_gemini»." in respuesta


def test_sin_gemini_la_traza_lo_dice(adentro: TestClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(gemini, "para_el_bot", lambda _parametros: None)
    escribir(adentro, texto="hola")

    respuesta = escribir(adentro, texto="lavan tractomulas?")

    assert "No te entendí bien." in respuesta
    assert "Gemini no está conectado (Configuración): el bot siguió solo." in respuesta


def test_un_boton_no_le_pregunta_a_gemini(adentro: TestClient, sesion: Session):
    respuesta = escribir(adentro, opcion="menu.fila", titulo="Ver la fila")

    assert "Tocó un botón: el bot no le preguntó a Gemini." in respuesta
    assert paso(sesion, CELULAR) == "menu"


def test_pedir_mi_turno_no_es_ver_el_turno_que_tiene(sesion: Session):
    de_mentira = GeminiDeMentira(Interpretacion(Q.PEDIR_TURNO))
    entrada = conversacion.Entrada(CELULAR, texto="quiero pedir mi turno")

    # A las 8 a.m. del lunes: con la hora real, de noche, el bot diría que ya cerró.
    mensajes = conversacion.atender(sesion, entrada, interprete=de_mentira, ahora=LUNES_8AM)

    assert de_mentira.mensajes == ["quiero pedir mi turno"]  # lo leyó Gemini
    assert mensajes[0].texto.startswith("Para darte turno guardamos tu nombre")


def test_una_foto_tambien_tiene_traza(sesion: Session):
    entrada = conversacion.Entrada(CELULAR, otro=True)

    _, traza = conversacion.atender_con_traza(sesion, entrada, interprete=GeminiDeMentira())

    assert traza.texto().startswith("Mandó algo que no es texto")


def test_la_traza_no_lleva_lo_que_escribio_el_cliente(sesion: Session):
    de_mentira = GeminiDeMentira(Interpretacion(Q.CHARLA, respuesta="¡Qué bien!"))
    entrada = conversacion.Entrada(CELULAR, texto="mi placa es ABC123 y vivo en la 45")

    _, traza = conversacion.atender_con_traza(sesion, entrada, interprete=de_mentira)

    assert "ABC123" not in traza.texto() and "45" not in traza.texto()
    assert "¡Qué bien!" not in traza.texto()  # ni lo que escribió Gemini
