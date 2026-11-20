"""Dónde queda el lavadero: el bot lo dice cuando le preguntan y al dar un turno.

Lo que pidió el negocio (19 de noviembre de 2026): "cuando pregunten la dirección... y cuando
se asigna el turno también debe aparecer la dirección para que sepan dónde llegar".

La dirección (y el enlace de Google Maps, opcional) se escribe una sola vez en
Configuración > Recibo y reporte: la usan el bot, el recibo y la política de privacidad. En
las pruebas va una dirección de ejemplo; la de verdad no se escribe en el código.

Lo que tiene que cumplirse siempre:
- "¿dónde quedan?" se contesta con la dirección, sin Gemini, y si iba pidiendo un turno,
  sigue donde iba;
- la confirmación del turno, la de la reserva y el recordatorio llevan la dirección;
- sin dirección escrita, ningún mensaje dice "Dirección:" vacío;
- un enlace del mapa que no es https:// no se guarda.
"""

import dataclasses
from datetime import datetime, time, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app import listo
from app.bot import textos
from app.bot.avisos import mensaje_del_aviso
from app.bot.conversacion import catalogo_del_bot
from app.bot.redactor import ficha_completa, revisar
from app.catalogo.parametros import leer_parametros
from app.turnos import casos_de_uso as casos
from app.turnos import recibo
from app.turnos.estados import OrigenTurno, TipoAviso
from pruebas.fabricas import BOGOTA, LUNES_8AM, cliente_con_vehiculo
from pruebas.navegador import HTMX, aviso
from pruebas.test_bot import CELULAR, MENU, Chat

DIRECCION = "Calle 10 # 5-20, Bucaramanga"
MAPA = "https://maps.app.goo.gl/EjemploDePrueba"
MARTES = LUNES_8AM.date() + timedelta(days=1)


def con_direccion(sesion: Session, direccion: str = DIRECCION, mapa: str = "") -> None:
    escrito = dataclasses.replace(recibo.leer_ajustes(sesion), direccion=direccion, mapa=mapa)
    recibo.guardar_ajustes(sesion, escrito)


def pide_turno(chat: Chat) -> Chat:
    """Laura pide turno para su carro (Básico)."""
    chat.toca_el_id("menu.pedir").toca("Acepto").escribe("Laura Gómez")
    return chat.toca("Automóvil").toca("Ahora no").toca("Básico")


# --- Cuando preguntan ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "pregunta",
    [
        "¿Dónde quedan?",
        "Cuál es la dirección",
        "buenas tardes, ¿dónde están ubicados?",
        "Hola, me puede regalar la dirección del lavadero por favor",
        "¿cómo llego?",
        "mándeme la ubicación",
    ],
)
def test_preguntan_donde_quedan_y_el_bot_da_la_direccion(sesion: Session, pregunta: str):
    con_direccion(sesion)

    chat = Chat(sesion).escribe(pregunta)

    assert chat.texto == (
        f"📍 Estamos en *{DIRECCION}*. Atendemos todos los días de 5:30 a.m. a 7:30 p.m."
    )
    assert chat.opciones == MENU


def test_con_el_enlace_de_google_maps_lo_manda_para_llegar_con_un_toque(sesion: Session):
    con_direccion(sesion, mapa=MAPA)

    chat = Chat(sesion).escribe("¿Dónde quedan?")

    assert chat.texto.endswith(f"\n\n🗺️ Cómo llegar: {MAPA}")


def test_si_pregunta_mientras_pide_el_turno_sigue_donde_iba(sesion: Session):
    con_direccion(sesion)
    chat = Chat(sesion).toca_el_id("menu.pedir").toca("Acepto")

    chat.escribe("¿dónde quedan?")

    assert chat.texto.startswith(f"📍 Estamos en *{DIRECCION}*.")
    assert chat.texto.endswith("¿A nombre de quién hacemos el turno?")


@pytest.mark.parametrize(
    "mensaje", ["ya estoy ubicado afuera", "¿dónde están los precios?", "¿dónde pago?"]
)
def test_lo_que_no_pregunta_por_la_direccion_no_la_recibe(sesion: Session, mensaje: str):
    con_direccion(sesion)

    chat = Chat(sesion).escribe(mensaje)

    assert "Estamos en" not in chat.texto


def test_sin_direccion_escrita_no_se_inventa(sesion: Session):
    chat = Chat(sesion).escribe("¿Dónde quedan?")

    assert "Estamos en" not in chat.texto and "Dirección" not in chat.texto


# --- Al dar el turno -----------------------------------------------------------------------


def test_el_turno_confirmado_dice_a_donde_llegar(sesion: Session):
    con_direccion(sesion, mapa=MAPA)
    chat = pide_turno(Chat(sesion)).toca("Lo antes posible")

    chat.toca("Confirmar turno")

    assert chat.texto.startswith("¡Listo, Laura! Tu turno es *C-01*.")
    assert chat.texto.endswith(f"\n\n📍 *Dirección:* {DIRECCION}\n🗺️ *Cómo llegar:* {MAPA}")


def test_la_reserva_confirmada_dice_a_donde_llegar(sesion: Session):
    con_direccion(sesion)
    chat = pide_turno(Chat(sesion)).escribe("mañana a las 10:30")

    chat.toca("Confirmar turno")

    assert chat.texto.startswith("¡Listo, Laura! Tu turno *C-01* quedó reservado para mañana")
    assert chat.texto.endswith(f"escribe *cancelar*.\n\n📍 *Dirección:* {DIRECCION}")
    assert chat.opciones == ["Cambiar hora", "Cancelar"]


def test_sin_direccion_la_confirmacion_queda_como_siempre(sesion: Session):
    chat = pide_turno(Chat(sesion)).escribe("mañana a las 10:30").toca("Confirmar turno")

    assert chat.texto.endswith("escribe *cancelar*.")


def test_el_recordatorio_dice_a_donde_llegar(sesion: Session, monkeypatch):
    con_direccion(sesion, mapa=MAPA)
    monkeypatch.setattr("app.tiempo.ahora", lambda: datetime.combine(MARTES, time(9, 30), BOGOTA))
    reservada = datetime.combine(MARTES, time(10, 30), BOGOTA)
    contenido = {"turno": "C-03", "hora_reservada": reservada.isoformat()}

    mensaje = mensaje_del_aviso(TipoAviso.RECORDATORIO.value, contenido, leer_parametros(sesion))

    assert mensaje.texto.startswith("¡Hola! Te recordamos tu turno *C-03*: te esperamos hoy")
    assert mensaje.texto.endswith(f"\n\n📍 *Dirección:* {DIRECCION}\n🗺️ *Cómo llegar:* {MAPA}")


def test_si_con_la_direccion_no_cabe_en_whatsapp_va_sin_el_enlace():
    largo = "x" * 960

    con_enlace = textos.con_la_direccion("corto", DIRECCION, MAPA)
    sin_enlace = textos.con_la_direccion(largo, DIRECCION, MAPA)

    assert con_enlace == f"corto\n\n📍 *Dirección:* {DIRECCION}\n🗺️ *Cómo llegar:* {MAPA}"
    assert sin_enlace == f"{largo}\n\n📍 *Dirección:* {DIRECCION}"
    assert textos.con_la_direccion("x" * 1020, DIRECCION, MAPA) == "x" * 1020


# --- Lo que sabe Gemini ------------------------------------------------------------------------


def test_gemini_sabe_la_direccion_y_puede_decirla(sesion: Session):
    con_direccion(sesion, mapa=MAPA)

    ficha = ficha_completa(leer_parametros(sesion), catalogo_del_bot(sesion))

    assert f"Dirección: {DIRECCION}." in ficha.splitlines()
    assert f"Cómo llegar (enlace de Google Maps): {MAPA}" in ficha.splitlines()
    # Lo que escriba con la dirección y el enlace pasa la revisión (que no deja salir
    # números ni enlaces que no estén en la ficha).
    respuesta = f"Estamos en la Calle 10 # 5-20, Bucaramanga. Aquí el mapa: {MAPA}"
    assert revisar(respuesta, ficha) == respuesta


def test_gemini_sabe_que_se_puede_reservar(sesion: Session):
    """Antes, la ficha decía "no se apartan para otro día ni para una hora exacta"."""
    ficha = ficha_completa(leer_parametros(sesion), catalogo_del_bot(sesion))

    assert "no se apartan" not in ficha
    assert (
        "o se reserva un día y una hora, hasta 7 días adelante, con al menos 30 minutos de "
        "anticipación" in ficha
    )


# --- Configuración y Lista para abrir ------------------------------------------------------


def test_la_direccion_y_el_mapa_se_escriben_en_recibo_y_reporte(
    adentro: TestClient, sesion: Session
):
    respuesta = adentro.post(
        "/configuracion/recibo",
        headers=HTMX,
        data={
            "direccion": "  Calle 10 #  5-20,   Bucaramanga ",
            "mapa": f" {MAPA} ",
            "pie_recibo": "Gracias por preferirnos.",
            "ancho_ticket": "80",
        },
    )

    assert aviso(respuesta) == "Recibo y reporte guardados."
    parametros = leer_parametros(sesion)
    assert (parametros.direccion, parametros.mapa) == (DIRECCION, MAPA)
    assert f'value="{MAPA}"' in respuesta.text


def test_un_enlace_del_mapa_sin_https_no_se_guarda(adentro: TestClient, sesion: Session):
    respuesta = adentro.post(
        "/configuracion/recibo",
        headers=HTMX,
        data={"direccion": DIRECCION, "mapa": "maps.google.com/lavadero", "ancho_ticket": "80"},
    )

    assert "El enlace del mapa debe empezar por https://" in respuesta.text
    assert leer_parametros(sesion).mapa == ""


def test_lista_para_abrir_avisa_si_falta_la_direccion(sesion: Session):
    def direccion_lista() -> bool | None:
        puntos = [p for grupo in listo.revisar(sesion) for p in grupo.puntos]
        return next(p.listo for p in puntos if p.que == "Dirección del lavadero")

    assert direccion_lista() is False
    con_direccion(sesion)
    assert direccion_lista() is True


def test_la_reserva_por_el_dashboard_no_cambia(sesion: Session):
    """La dirección va en los mensajes del bot, no en el turno: reservar sigue igual."""
    con_direccion(sesion)
    vehiculo = cliente_con_vehiculo(sesion, telefono=CELULAR)

    reserva = casos.reservar(
        sesion,
        vehiculo_id=vehiculo.id,
        hora_elegida=datetime.combine(MARTES, time(10, 30), BOGOTA),
        origen=OrigenTurno.WHATSAPP,
        ahora=LUNES_8AM,
    )

    assert reserva.numero == "C-01"
