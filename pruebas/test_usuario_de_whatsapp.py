"""Clientes que escriben por WhatsApp sin mostrar su celular.

Desde 2026, a quien tiene nombre de usuario en WhatsApp Meta le puede mandar al bot sin su
celular: solo un id de usuario de la empresa (BSUID, "CO.1349…"). Pasó en la primera
semana: el bot recibía el aviso y no contestaba.

Lo que tiene que cumplirse siempre:
- se le atiende igual, con su id de usuario donde iría el celular;
- se le contesta con "recipient" (no con "to");
- si después llega con el celular, sigue siendo el mismo cliente, con su conversación;
- en el dashboard sale "WhatsApp sin número visible", sin botón de llamar.
"""

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import formato
from app.bot.mensajes import Mensaje
from app.clientes import servicio as clientes
from app.clientes.reglas import es_usuario_de_whatsapp
from app.clientes.tablas import Cliente
from app.comentarios import servicio as comentarios
from app.comentarios.tablas import TipoComentario
from app.integraciones import bandeja_entrada, whatsapp
from app.integraciones.tablas import MensajeEntrante
from pruebas.test_whatsapp import (  # noqa: F401 (fixtures)
    CELULAR,
    ID_DEL_NUMERO,
    MetaDeMentira,
    abrir,
    aviso_de_meta,
    conectado,
    enviador,
    mandar,
    meta,
    toca,
    webhook_listo,
)

USUARIO = "CO.13491208655302741918"


def sin_celular(texto: str = "hola", id_meta: str = "wamid.u1") -> dict:
    return {"id": id_meta, "from_user_id": USUARIO, "type": "text", "text": {"body": texto}}


def con_los_dos(texto: str = "hola", id_meta: str = "wamid.u2") -> dict:
    return {**sin_celular(texto, id_meta), "from": CELULAR}


def test_que_es_un_usuario_de_whatsapp():
    assert es_usuario_de_whatsapp(USUARIO)
    assert not es_usuario_de_whatsapp(CELULAR)
    assert not es_usuario_de_whatsapp("co.123") and not es_usuario_de_whatsapp(None)


def test_sin_celular_se_lee_con_su_usuario():
    [recibido], _ = whatsapp.leer_aviso_de_meta(aviso_de_meta(sin_celular()), ID_DEL_NUMERO)

    assert (recibido.telefono, recibido.texto, recibido.usuario) == (USUARIO, "hola", None)


def test_con_los_dos_gana_el_celular_y_se_recuerda_el_usuario():
    [recibido], _ = whatsapp.leer_aviso_de_meta(aviso_de_meta(con_los_dos()), ID_DEL_NUMERO)

    assert (recibido.telefono, recibido.usuario) == (CELULAR, USUARIO)


def test_al_usuario_se_le_escribe_con_recipient():
    al_usuario = whatsapp.contenido_para_meta(Mensaje("hola"), USUARIO)
    al_celular = whatsapp.contenido_para_meta(Mensaje("hola"), CELULAR)

    assert al_usuario["recipient"] == USUARIO and "to" not in al_usuario
    assert al_celular["to"] == CELULAR and "recipient" not in al_celular


def test_por_whatsapp_el_bot_le_contesta_sin_conocer_su_celular(
    webhook_listo: TestClient,  # noqa: F811
    sesion: Session,
    meta: MetaDeMentira,  # noqa: F811
):
    mandar(webhook_listo, aviso_de_meta(sin_celular()))

    [respuesta] = meta.enviados
    assert respuesta["recipient"] == USUARIO and "to" not in respuesta
    assert meta.textos[0].startswith("¡Hola!")
    [recibido] = sesion.scalars(select(MensajeEntrante)).all()
    assert recibido.telefono == USUARIO and recibido.procesado_en is not None

    # Y sigue la conversación: pide turno y queda como cliente, con su usuario.
    mandar(
        webhook_listo,
        aviso_de_meta(
            {**toca("menu.precios", "Servicios y precios"), "from": None, "from_user_id": USUARIO}
        ),
    )
    assert meta.textos[-1].startswith("¿Para qué vehículo")


def test_al_aceptar_el_aviso_queda_como_cliente_con_su_usuario(sesion: Session):
    ana = clientes.registrar_cliente(sesion, nombre="ana ruiz", acepta_datos=True, telefono=USUARIO)

    assert ana.telefono == USUARIO
    assert clientes.buscar_por_telefono(sesion, USUARIO) == ana


def test_si_despues_llega_con_el_celular_sigue_siendo_el_mismo_cliente(sesion: Session):
    ana = Cliente(telefono=USUARIO, nombre="Ana Ruiz")
    sesion.add(ana)
    sesion.add(
        MensajeEntrante(
            id_meta="wamid.antes", telefono=USUARIO, tipo="text", contenido={"texto": "hola"}
        )
    )
    comentarios.registrar(sesion, TipoComentario.QUEJA, "mal", cliente=ana)
    sesion.flush()

    bandeja_entrada.unir_usuario_y_celular(sesion, USUARIO, CELULAR)
    sesion.expire_all()

    assert sesion.get(Cliente, ana.id).telefono == CELULAR
    telefonos = set(sesion.scalars(select(MensajeEntrante.telefono)))
    assert telefonos == {CELULAR}  # la conversación de antes sigue con él


def test_si_ese_celular_ya_era_de_otro_cliente_no_se_juntan(sesion: Session):
    con_usuario = Cliente(telefono=USUARIO, nombre="Ana Ruiz")
    con_celular = Cliente(telefono=CELULAR, nombre="Pedro Gil")
    sesion.add_all([con_usuario, con_celular])
    sesion.flush()

    bandeja_entrada.unir_usuario_y_celular(sesion, USUARIO, CELULAR)
    sesion.expire_all()

    assert sesion.get(Cliente, con_usuario.id).telefono == USUARIO


def test_en_el_dashboard_sale_sin_numero_y_sin_llamar(adentro: TestClient, sesion: Session):
    ana = Cliente(telefono=USUARIO, nombre="Ana Ruiz")
    sesion.add(ana)
    sesion.flush()
    comentarios.registrar(sesion, TipoComentario.QUIERE_HABLAR, "llámenme", cliente=ana)
    sesion.flush()

    pagina = adentro.get("/comentarios").text

    assert formato.telefono_oculto(USUARIO) == "WhatsApp sin número visible"
    assert formato.telefono(USUARIO) == "WhatsApp sin número visible"
    assert "WhatsApp sin número visible" in pagina
    assert f"tel:+{USUARIO}" not in pagina
