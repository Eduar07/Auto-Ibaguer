"""Sin Gemini (no está conectado, se acabó la cuota o no respondió), el bot entiende solo lo
más común: así un cliente no recibe "no te entendí" por preguntar lo de siempre."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.bot.conversacion import normalizar, por_palabras
from app.bot.interprete import CARRO, Intencion
from app.clientes.tablas import Cliente
from app.comentarios.tablas import Comentario, TipoComentario
from pruebas.fabricas import cliente_con_vehiculo
from pruebas.test_bot import CELULAR, Chat

Q = Intencion


@pytest.mark.parametrize(
    ("escribe", "intencion", "vehiculo"),
    [
        ("¿lavan motos?", Q.PRECIOS, "moto"),
        ("cuánto vale lavar la camionetica", Q.PRECIOS, "camioneta"),
        ("qué precio tiene el lavado del carro", Q.PRECIOS, CARRO),
        ("quiero lavar la moto", Q.PEDIR_TURNO, "moto"),
        ("me puede separar un cupo", Q.PEDIR_TURNO, None),
        ("está muy lleno?", Q.VER_FILA, None),
        ("cuánto se demora más o menos", Q.VER_FILA, None),
        ("tengo una flota de taxis", Q.PEDIDO_ESPECIAL, None),
        ("hola", None, None),
        ("reciben nequi?", None, None),
    ],
)
def test_lo_que_entiende_por_palabras(escribe: str, intencion, vehiculo):
    entendido = por_palabras(normalizar(escribe))

    if intencion is None:
        assert entendido is None
    else:
        assert (entendido.intencion, entendido.vehiculo) == (intencion, vehiculo)


@pytest.mark.parametrize(
    ("escribe", "empieza"),
    [
        ("¿lavan motos?", "Servicios para moto:"),
        ("quiero lavar el carro", "Para darte turno guardamos tu nombre"),
        ("está muy lleno?", "Así está la fila ahora:"),
        ("hasta qué hora atienden?", "Por WhatsApp damos turnos todos los días"),
        ("tengo una flota de taxis", "¡Qué bien! Eso lo cuadra directamente el administrador"),
        ("me interesa el carro, cuánto vale?", "¿Automóvil o camioneta?"),
    ],
)
def test_sin_gemini_contesta_lo_de_siempre(sesion: Session, escribe: str, empieza: str):
    cliente = Chat(sesion).escribe("hola").escribe(escribe)

    assert cliente.texto.startswith(empieza)


def test_un_saludo_con_pregunta_contesta_la_pregunta(sesion: Session):
    assert (
        Chat(sesion)
        .escribe("hola, cuánto vale lavar la moto?")
        .texto.startswith("Servicios para moto:")
    )


# --- La ficha del cliente: en qué quedó (pieza 6) -----------------------------------------------


def test_la_ficha_dice_en_que_quedo_por_whatsapp(adentro: TestClient, sesion: Session):
    cliente_con_vehiculo(sesion, telefono=CELULAR, nombre="Ana Ruiz")
    Chat(sesion).escribe("hola").toca("Servicios y precios").toca("Moto")
    Chat(sesion).escribe("me rayaron la puerta ayer")
    sesion.flush()
    ana = sesion.scalars(select(Cliente).where(Cliente.telefono == CELULAR)).one()
    queja = sesion.scalars(select(Comentario)).one()
    assert queja.tipo == TipoComentario.QUEJA

    ficha = adentro.get(f"/clientes/{ana.id}/ficha").text

    assert "<h3>Por WhatsApp</h3>" in ficha
    assert "Queja por atender (en Comentarios): me rayaron la puerta ayer" in ficha
    assert "Preguntó los precios de moto" in ficha


def test_sin_whatsapp_la_ficha_no_muestra_el_bloque(adentro: TestClient, sesion: Session):
    vehiculo = cliente_con_vehiculo(sesion, sin_telefono=True, nombre="Pedro Gil")

    ficha = adentro.get(f"/clientes/{vehiculo.cliente_id}/ficha").text

    assert "Por WhatsApp" not in ficha
