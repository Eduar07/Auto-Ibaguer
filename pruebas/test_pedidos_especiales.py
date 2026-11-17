"""Lo que el bot no vende y lo que no le toca decidir (piezas 2 y 5 del checklist "¿Tu bot
vende o solo contesta?").

Lo que tiene que cumplirse siempre:
- un servicio que no está en el menú no tiene precio: el bot dice que no lo tiene y cuáles
  sí, y nunca inventa una cifra;
- una flota, un convenio o un precio especial los decide el dueño: el bot no negocia, se
  los pasa con un resumen y le dice al cliente que le escriben;
- al dueño le llega un correo con el resumen (sin el celular del cliente), una sola vez.
"""

import dataclasses
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.bot.interprete import Intencion, Interpretacion
from app.clientes.tablas import Cliente
from app.comentarios import avisos
from app.comentarios import servicio as comentarios
from app.comentarios.tablas import Comentario, TipoComentario
from app.reloj import tareas
from app.turnos.recibo import guardar_ajustes, leer_ajustes
from pruebas.fabricas import LUNES_8AM, cliente_con_vehiculo
from pruebas.test_bot import CELULAR, paso
from pruebas.test_gemini import chat
from pruebas.test_whatsapp import abrir  # noqa: F401 (fixture)

Q = Intencion
POLICHADO = Interpretacion(
    Q.PRECIOS,
    servicio_que_no_hay=True,
    respuesta="El polichado no lo tenemos en el menú. Hacemos lavado básico, general y encerado.",
)
FLOTA = Interpretacion(
    Q.PEDIDO_ESPECIAL, resumen="Empresa pide precio para lavar 20 camionetas cada semana"
)
PIDE_LA_FLOTA = "tengo una empresa, son 20 camionetas cada semana, me hacen precio?"


def el_comentario(sesion: Session) -> Comentario:
    return sesion.scalars(select(Comentario)).one()


# --- Un servicio que no está en el menú ------------------------------------------------------


def test_por_un_servicio_que_no_hay_dice_que_no_lo_tiene_sin_inventar_precio(sesion: Session):
    cliente = chat(sesion, POLICHADO).escribe("cuánto vale el polichado?")

    assert cliente.texto == POLICHADO.respuesta
    assert cliente.opciones == ["Servicios y precios", "Pedir turno"]


def test_sin_lo_que_escribe_gemini_sale_el_texto_fijo_con_los_servicios(sesion: Session):
    sin_respuesta = dataclasses.replace(POLICHADO, respuesta="")

    cliente = chat(sesion, sin_respuesta).escribe("cuánto vale el polichado?")

    assert cliente.texto.startswith("Ese servicio no lo tenemos en el menú. Lo que sí hacemos: ")
    assert "básico" in cliente.texto and "$" not in cliente.texto


def test_si_gemini_le_pone_precio_no_sale(sesion: Session):
    inventado = dataclasses.replace(POLICHADO, respuesta="El polichado te sale en $80.000.")

    cliente = chat(sesion, inventado).escribe("cuánto vale el polichado?")

    assert "80.000" not in cliente.texto
    assert cliente.texto.startswith("Ese servicio no lo tenemos en el menú.")
    assert "no pasó la revisión" in cliente.traza.texto()


def test_eligiendo_el_servicio_contesta_y_vuelve_a_preguntar(sesion: Session):
    cliente_con_vehiculo(sesion, telefono=CELULAR, nombre="Ana Ruiz", placa="ABC123")
    cliente = chat(sesion)
    cliente.escribe("hola").toca("Pedir turno")
    cliente.gemini.entendidos.append(
        Interpretacion(
            Q.PEDIR_TURNO,
            servicio_que_no_hay=True,
            respuesta="El polichado no lo tenemos; de lo que hay, el encerado es el más completo.",
        )
    )

    cliente.escribe("quiero un polichado")

    assert cliente.texto.startswith("El polichado no lo tenemos;")
    assert "¿Qué servicio quieres para tu" in cliente.texto  # sigue con el turno
    assert paso(sesion) == "servicio"


# --- Pedidos especiales ----------------------------------------------------------------------


def test_una_flota_se_le_pasa_al_dueno_con_un_resumen(sesion: Session):
    cliente_con_vehiculo(sesion, telefono=CELULAR, nombre="Ana Ruiz")
    cliente = chat(sesion, FLOTA)

    cliente.escribe(PIDE_LA_FLOTA)

    assert cliente.texto.startswith("¡Qué bien, Ana! Eso lo cuadra directamente el administrador")
    assert "$" not in cliente.texto  # no negocia
    pedido = el_comentario(sesion)
    assert (pedido.tipo, pedido.texto, pedido.resumen) == (
        TipoComentario.PEDIDO_ESPECIAL,
        PIDE_LA_FLOTA,
        "Empresa pide precio para lavar 20 camionetas cada semana",
    )
    assert pedido.telefono == CELULAR

    cliente.gemini.entendidos.append(Interpretacion(Q.OTRO))
    cliente.escribe("son para lavar los lunes")
    assert el_comentario(sesion).texto == f"{PIDE_LA_FLOTA}\nson para lavar los lunes"


def test_sin_ser_cliente_pide_permiso_antes_de_guardar_su_numero(sesion: Session):
    cliente = chat(sesion, FLOTA).escribe(PIDE_LA_FLOTA)

    assert "Para que te escriba guardamos tu número de WhatsApp" in cliente.texto
    assert cliente.opciones == ["Sí, que me responda", "No, gracias"]
    assert sesion.scalars(select(Comentario)).all() == []  # sin permiso, nada

    cliente.toca("Sí, que me responda")

    pedido = el_comentario(sesion)
    assert pedido.tipo == TipoComentario.PEDIDO_ESPECIAL
    assert pedido.resumen == FLOTA.resumen and pedido.telefono == CELULAR


def test_en_medio_de_pedir_un_turno_tambien_se_le_pasa(sesion: Session):
    cliente_con_vehiculo(sesion, telefono=CELULAR, nombre="Ana Ruiz")
    cliente = chat(sesion)
    cliente.escribe("hola").toca("Pedir turno")
    cliente.gemini.entendidos.append(FLOTA)

    cliente.escribe("y si les llevo 20 camionetas cada semana?")

    assert cliente.texto.startswith("¡Qué bien, Ana!")
    assert el_comentario(sesion).tipo == TipoComentario.PEDIDO_ESPECIAL


def test_la_queja_tambien_lleva_resumen(sesion: Session):
    cliente_con_vehiculo(sesion, telefono=CELULAR, nombre="Ana Ruiz")
    queja = Interpretacion(Q.QUEJA, resumen="Le rayaron la puerta del carro")

    chat(sesion, queja).escribe("me entregaron el carro rayado en la puerta")

    assert el_comentario(sesion).resumen == "Le rayaron la puerta del carro"


def test_en_comentarios_se_ve_el_pedido_y_su_resumen(adentro: TestClient, sesion: Session):
    vehiculo = cliente_con_vehiculo(sesion, telefono=CELULAR, nombre="Ana Ruiz")
    cliente = sesion.get(Cliente, vehiculo.cliente_id)
    comentarios.registrar(
        sesion,
        TipoComentario.PEDIDO_ESPECIAL,
        PIDE_LA_FLOTA,
        cliente=cliente,
        resumen=FLOTA.resumen,
    )
    sesion.flush()

    pagina = adentro.get("/comentarios").text

    assert "Pedido especial" in pagina
    assert f'<p class="comentario__resumen">{FLOTA.resumen}</p>' in pagina


# --- El correo al dueño ----------------------------------------------------------------------


@pytest.fixture
def con_destinos(sesion: Session, correo_configurado: list) -> list:
    guardar_ajustes(sesion, dataclasses.replace(leer_ajustes(sesion), correos_reporte="jefe@x.co"))
    return correo_configurado


def registrar(sesion: Session, tipo: TipoComentario, texto: str, hace: timedelta, **datos):
    return comentarios.registrar(sesion, tipo, texto, ahora=LUNES_8AM - hace, **datos)


def test_al_dueno_le_llega_un_correo_con_el_resumen_una_sola_vez(
    sesion: Session, con_destinos: list
):
    vehiculo = cliente_con_vehiculo(sesion, telefono=CELULAR, nombre="Ana Ruiz")
    cliente = sesion.get(Cliente, vehiculo.cliente_id)
    pedido = registrar(
        sesion,
        TipoComentario.PEDIDO_ESPECIAL,
        PIDE_LA_FLOTA,
        timedelta(minutes=5),
        cliente=cliente,
        resumen=FLOTA.resumen,
    )
    registrar(sesion, TipoComentario.QUEJA, "me atendieron mal", timedelta(minutes=3))
    registrar(sesion, TipoComentario.FELICITACION, "muy bueno", timedelta(minutes=5))
    registrar(sesion, TipoComentario.QUIERE_HABLAR, "acabo de escribir", timedelta(seconds=30))
    registrar(sesion, TipoComentario.QUEJA, "de ayer", timedelta(hours=13))

    aviso = avisos.preparar(sesion, ahora=LUNES_8AM)
    assert avisos.enviar(aviso)

    [correo] = con_destinos
    assert correo["To"] == "jefe@x.co"
    assert correo["Subject"] == "Auto Ibaguer: 2 clientes esperan que les respondas"
    cuerpo = correo.get_content()
    assert "- Pedido especial · Ana · 7:55 a.m.\n  " + FLOTA.resumen in cuerpo
    assert "- Queja · Alguien que no es cliente · 7:57 a.m.\n  me atendieron mal" in cuerpo
    assert "muy bueno" not in cuerpo and "de ayer" not in cuerpo
    assert "acabo de escribir" not in cuerpo  # todavía puede contar más: va en el próximo
    assert "300" not in cuerpo and CELULAR not in cuerpo  # nunca el celular
    assert pedido.avisado_en == LUNES_8AM
    assert avisos.preparar(sesion, ahora=LUNES_8AM) is None  # una sola vez


def test_sin_correo_configurado_no_se_marca_nada(sesion: Session):
    pedido = registrar(sesion, TipoComentario.QUEJA, "mal", timedelta(minutes=5))

    assert avisos.preparar(sesion, ahora=LUNES_8AM) is None
    assert pedido.avisado_en is None  # se avisa cuando se configure, si sigue siendo reciente


def test_lo_que_ya_se_atendio_no_se_avisa(sesion: Session, con_destinos: list):
    queja = registrar(sesion, TipoComentario.QUEJA, "mal", timedelta(minutes=5))
    queja.atendido_en = LUNES_8AM - timedelta(minutes=1)

    assert avisos.preparar(sesion, ahora=LUNES_8AM) is None


def test_el_reloj_lo_manda_en_su_vuelta(sesion: Session, con_destinos: list, abrir):  # noqa: F811
    registrar(sesion, TipoComentario.QUIERE_HABLAR, "llámenme", timedelta(minutes=5))
    sesion.flush()

    hizo = tareas.AVISO_AL_DUENO.hacer(abrir, LUNES_8AM)

    assert hizo == ["Se le avisó al dueño por correo de clientes que esperan respuesta."]
    assert con_destinos[0]["Subject"] == "Auto Ibaguer: un cliente espera que le respondas"
    assert tareas.AVISO_AL_DUENO in tareas.del_servidor(None)
