"""Lo que salió de la primera noche con clientes por WhatsApp:

- algunos celulares "nunca recibieron respuesta": en Mensajes se busca el celular y se ve
  de una si le escribió al bot y qué pasó con la respuesta;
- un mensaje para otro número de la cuenta se descarta, pero deja rastro en el registro;
- la charla ("te amo", "viste el partido?") se contesta como una persona y vuelve al
  lavadero, con los botones para pedir turno o ver los precios.
"""

import logging

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.bot.interprete import Intencion, Interpretacion
from app.integraciones import whatsapp
from app.integraciones.gemini import INSTRUCCIONES
from pruebas.fabricas import LUNES_8AM, cliente_con_vehiculo, por_whatsapp
from pruebas.test_bot import CELULAR
from pruebas.test_gemini import ChatConGemini, GeminiDeMentira, chat
from pruebas.test_whatsapp import (  # noqa: F401 (fixtures)
    ID_DEL_NUMERO,
    MetaDeMentira,
    abrir,
    aviso_de_meta,
    conectado,
    enviador,
    escribe,
    mandar,
    meta,
    webhook_listo,
)

OTRO_CELULAR = "573151234567"


# --- Buscar un celular en Mensajes ---------------------------------------------------------------


def test_se_busca_un_celular_y_sale_solo_lo_suyo(
    webhook_listo: TestClient,  # noqa: F811
    adentro: TestClient,
    meta: MetaDeMentira,  # noqa: F811
):
    mandar(webhook_listo, aviso_de_meta(escribe("hola"), escribe("buenas", OTRO_CELULAR)))

    pagina = adentro.get("/mensajes", params={"celular": "315 123 4567"}).text

    assert "315 *** 4567" in pagina and "300 *** 4567" not in pagina
    assert 'value="315 123 4567"' in pagina and 'href="/mensajes">Ver todos' in pagina


def test_si_ese_celular_no_escribio_lo_dice(adentro: TestClient):
    pagina = adentro.get("/mensajes", params={"celular": "3001112233"}).text

    assert "Ese celular no le ha escrito al bot" in pagina
    assert "Suscribir webhooks" in pagina


def test_un_celular_mal_escrito_se_explica(adentro: TestClient):
    pagina = adentro.get("/mensajes", params={"celular": "12"}).text

    assert "Escribe el celular con sus 10 dígitos" in pagina


def test_un_mensaje_para_otro_numero_deja_rastro(caplog):
    with caplog.at_level(logging.WARNING, logger="app.integraciones.whatsapp"):
        recibidos, _ = whatsapp.leer_aviso_de_meta(
            aviso_de_meta(escribe("hola"), numero="999"), ID_DEL_NUMERO
        )

    assert recibidos == []
    assert "para el número 999, y el del bot es 109876543210" in caplog.text
    assert CELULAR not in caplog.text  # el celular del cliente no queda en el registro


# --- La charla vuelve al lavadero --------------------------------------------------------------

TE_AMO = Interpretacion(
    Intencion.CHARLA,
    respuesta="Jaja, gracias por el cariño 😄 ¿Y la moto cómo va? Te la dejamos brillando.",
)


def test_la_charla_vuelve_al_lavadero_con_los_botones(sesion: Session):
    cliente = chat(sesion, TE_AMO).escribe("te amo")

    assert cliente.texto == TE_AMO.respuesta
    assert cliente.opciones == ["Pedir turno", "Servicios y precios"]


def test_de_noche_la_charla_solo_ofrece_los_precios(sesion: Session):
    noche = LUNES_8AM.replace(hour=22)
    cliente = ChatConGemini(sesion, GeminiDeMentira(TE_AMO), ahora=noche).escribe("te amo")

    assert cliente.opciones == ["Servicios y precios"]


def test_si_ya_tiene_turno_no_se_le_insiste(sesion: Session):
    vehiculo = cliente_con_vehiculo(sesion, telefono=CELULAR, nombre="Ana Ruiz")
    por_whatsapp(sesion, vehiculo=vehiculo)
    cliente = chat(sesion, TE_AMO)

    cliente.escribe("te amo")

    assert cliente.opciones == []
    assert cliente.gemini.contextos[0].en_que_va == "Ya tiene un turno hoy: lo esperamos."


def test_gemini_sabe_que_la_charla_vuelve_al_lavadero():
    instrucciones = " ".join(INSTRUCCIONES.split())

    assert "en el mismo mensaje, vuelve al lavadero" in instrucciones
    assert "No conviertes la charla en una venta" not in instrucciones
