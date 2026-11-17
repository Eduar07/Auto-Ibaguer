"""Fotos y audios: el cliente manda una nota de voz o una foto, como le escribiría a una
persona, y el bot la entiende (pieza 3 del checklist "¿Tu bot vende o solo contesta?").

Lo que tiene que cumplirse siempre:
- un audio vale como si lo hubiera escrito: sigue el mismo camino que un texto;
- de una foto se entiende para qué la manda, y lo que se ve le llega al administrador;
- la foto o el audio no se guardan en ninguna parte: solo lo que entendió Gemini;
- sin Gemini no se descarga nada, y el bot pide que lo escriba (no dice "no te entendí");
- mientras piensa, el cliente ve el chulito azul y el "escribiendo…".
"""

import base64
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.bot.interprete import Contexto, Intencion, Interpretacion
from app.bot.mensajes import Adjunto, Entrada, tipo_de_archivo
from app.catalogo.parametros import leer_parametros
from app.catalogo.tablas import Configuracion
from app.comentarios.tablas import Comentario
from app.integraciones import gemini, historial, whatsapp
from app.integraciones.tablas import MensajeEntrante
from pruebas.fabricas import cliente_con_vehiculo
from pruebas.test_gemini import (
    CONTEXTO,
    GeminiDeMentira,
    GoogleDeMentira,
    chat,
    con_google,
    lo_que_contesta,
    respuesta_de_gemini,
)
from pruebas.test_whatsapp import (  # noqa: F401 (fixtures)
    CELULAR,
    MetaDeMentira,
    abrir,
    aviso_de_meta,
    conectado,
    entrantes,
    enviador,
    mandar,
    meta,
    webhook_listo,
)

Q = Intencion
NOTA_DE_VOZ = Adjunto("audio/ogg; codecs=opus", b"OggS-nota-de-voz")
FOTO = Adjunto("image/jpeg", b"\xff\xd8foto-del-carro")


def audio(media_id: str = "audio-1") -> dict:
    return {
        "from": CELULAR,
        "id": f"wamid.{media_id}",
        "type": "audio",
        "audio": {"id": media_id, "mime_type": "audio/ogg; codecs=opus", "voice": True},
    }


def foto(media_id: str = "foto-1", pie: str | None = None) -> dict:
    imagen = {"id": media_id, "mime_type": "image/jpeg"}
    if pie is not None:
        imagen["caption"] = pie
    return {"from": CELULAR, "id": f"wamid.{media_id}", "type": "image", "image": imagen}


@pytest.fixture
def con_gemini(monkeypatch: pytest.MonkeyPatch, enviador) -> GeminiDeMentira:  # noqa: F811
    """Gemini conectado (de mentira) y WhatsApp conectado al Meta de mentira."""
    de_mentira = GeminiDeMentira()
    monkeypatch.setattr(gemini, "para_el_bot", lambda _: de_mentira)
    monkeypatch.setattr(whatsapp, "del_entorno", lambda: enviador)
    return de_mentira


# --- Lo que llega de Meta ------------------------------------------------------------------------


def test_de_una_foto_o_un_audio_se_guarda_solo_el_id_y_el_pie():
    [nota] = whatsapp.leer_aviso_de_meta(aviso_de_meta(audio()), "109876543210")[0]
    [con_pie] = whatsapp.leer_aviso_de_meta(
        aviso_de_meta(foto(pie="¿cuánto por esta?")), "109876543210"
    )[0]

    assert (nota.archivo, nota.mime, nota.texto) == ("audio-1", "audio/ogg; codecs=opus", None)
    assert (con_pie.archivo, con_pie.texto) == ("foto-1", "¿cuánto por esta?")


@pytest.mark.parametrize(
    ("tipo", "contenido", "archivo", "ignorar"),
    [
        ("video", {"id": "v-1", "mime_type": "video/mp4", "caption": "mire"}, None, False),
        ("document", {"id": "d-1", "mime_type": "application/pdf"}, None, False),
        ("sticker", {"id": "s-1", "mime_type": "image/webp"}, None, True),
    ],
)
def test_lo_que_gemini_no_ve_no_se_descarga_y_un_sticker_no_se_contesta(
    tipo: str, contenido: dict, archivo, ignorar: bool
):
    mensaje = {"from": CELULAR, "id": "wamid.x", "type": tipo, tipo: contenido}

    [recibido] = whatsapp.leer_aviso_de_meta(aviso_de_meta(mensaje), "109876543210")[0]

    assert (recibido.archivo, recibido.ignorar) == (archivo, ignorar)
    if tipo == "video":
        assert recibido.texto == "mire"  # el pie sí se lee


@pytest.mark.parametrize(
    ("mime", "tipo"),
    [
        ("audio/ogg; codecs=opus", "audio"),
        ("AUDIO/MPEG", "audio"),
        ("image/webp", "imagen"),
        ("audio/amr", None),
        ("video/mp4", None),
        (None, None),
    ],
)
def test_que_archivos_se_le_muestran_a_gemini(mime, tipo):
    assert tipo_de_archivo(mime) == tipo


# --- Lo que lee Gemini ---------------------------------------------------------------------------


def test_el_audio_va_adjunto_en_la_misma_consulta_y_con_mas_tiempo():
    google = GoogleDeMentira(
        respuesta_de_gemini(
            lo_que_contesta("pedir_turno", vehiculo="Moto", lo_que_dice="quiero lavar la moto")
        )
    )
    contexto = Contexto(CONTEXTO.catalogo, ficha=CONTEXTO.ficha, adjunto=NOTA_DE_VOZ)

    entendido = con_google(google).interpretar("", contexto)

    assert entendido == Interpretacion(
        Q.PEDIR_TURNO, vehiculo="Moto", lo_que_dice="quiero lavar la moto"
    )
    [peticion] = google.peticiones
    texto, adjunto = json.loads(peticion.content)["contents"][0]["parts"]
    assert texto["text"].endswith(
        "MENSAJE NUEVO DEL CLIENTE:\n[Mandó un audio, sin texto: está adjunto]"
    )
    assert adjunto == {
        "inlineData": {
            "mimeType": "audio/ogg",  # sin "codecs=opus": así lo nombra Gemini
            "data": base64.b64encode(NOTA_DE_VOZ.datos).decode(),
        }
    }
    assert peticion.extensions["timeout"]["read"] == gemini.ESPERA_CON_ADJUNTO


def test_la_foto_va_con_su_pie_tapado():
    google = GoogleDeMentira(respuesta_de_gemini(lo_que_contesta("precios")))
    contexto = Contexto(CONTEXTO.catalogo, adjunto=FOTO)

    con_google(google).interpretar("cuánto por esta? placa ABC123", contexto)

    texto = google.cuerpo()["contents"][0]["parts"][0]["text"]
    assert texto.endswith(
        "[Mandó una foto, adjunto, con este texto:] cuánto por esta? placa [placa]"
    )
    instrucciones = google.cuerpo()["systemInstruction"]["parts"][0]["text"]
    assert "Nunca dices que no puedes ver fotos ni oír audios" in instrucciones


def test_sin_adjunto_la_consulta_es_la_de_siempre():
    google = GoogleDeMentira(respuesta_de_gemini(lo_que_contesta("saludo", "¡Hola!")))

    con_google(google).interpretar("hola", CONTEXTO)

    [parte] = google.cuerpo()["contents"][0]["parts"]
    assert "inlineData" not in parte
    assert google.peticiones[0].extensions["timeout"]["read"] == gemini.ESPERA


# --- En la conversación ------------------------------------------------------------------------


def test_un_audio_pidiendo_turno_sigue_como_si_lo_hubiera_escrito(sesion: Session):
    cliente = chat(
        sesion,
        Interpretacion(Q.PEDIR_TURNO, vehiculo="Moto", lo_que_dice="quiero lavar la moto"),
    )

    cliente._enviar(Entrada(CELULAR, otro=True, adjunto=NOTA_DE_VOZ))

    assert cliente.texto == leer_parametros(sesion).aviso_datos  # empieza el turno
    assert cliente.traza.lo_que_dice == "quiero lavar la moto"
    assert "Gemini lo oyó" in cliente.traza.texto()
    assert cliente.gemini.contextos[0].adjunto == NOTA_DE_VOZ


def test_en_un_paso_de_texto_el_audio_cuenta_como_lo_escrito(sesion: Session):
    cliente = chat(sesion)
    cliente.escribe("hola").toca("Pedir turno").toca("Acepto")
    cliente.gemini.entendidos.append(Interpretacion(Q.OTRO, lo_que_dice="Ana Ruiz"))

    cliente._enviar(Entrada(CELULAR, otro=True, adjunto=NOTA_DE_VOZ))

    assert cliente.texto == "¿Qué vehículo traes?"  # tomó el nombre del audio


def test_una_foto_sola_nunca_se_toma_como_el_nombre(sesion: Session):
    cliente = chat(sesion)
    cliente.escribe("hola").toca("Pedir turno").toca("Acepto")
    cliente.gemini.entendidos.append(
        Interpretacion(Q.OTRO, respuesta="¿Me escribes tu nombre?", lo_que_dice="Un carro gris")
    )

    cliente._enviar(Entrada(CELULAR, otro=True, adjunto=FOTO))

    assert cliente.texto.startswith("¿Me escribes tu nombre?")
    assert "¿A nombre de quién hacemos el turno?" in cliente.texto


def test_la_foto_de_un_rayon_es_una_queja_y_el_administrador_lee_que_se_ve(sesion: Session):
    cliente_con_vehiculo(sesion, telefono=CELULAR, nombre="Ana Ruiz")
    cliente = chat(
        sesion,
        Interpretacion(Q.QUEJA, lo_que_dice="Una puerta de carro gris con un rayón largo"),
    )

    cliente._enviar(Entrada(CELULAR, texto="así me lo entregaron", otro=True, adjunto=FOTO))

    assert cliente.texto.startswith("Qué pena, Ana")
    queja = sesion.scalars(select(Comentario)).one()
    assert queja.texto == (
        "así me lo entregaron\n(Mandó una foto: Una puerta de carro gris con un rayón largo)"
    )


def test_un_audio_grave_recibe_la_ayuda_de_siempre(sesion: Session):
    cliente = chat(sesion, Interpretacion(Q.CRISIS, lo_que_dice="ya no quiero vivir"))

    cliente._enviar(Entrada(CELULAR, otro=True, adjunto=NOTA_DE_VOZ))

    assert "Línea 106" in cliente.texto


def test_si_gemini_no_alcanza_a_verla_le_pide_que_lo_escriba(sesion: Session):
    cliente = chat(sesion, None)  # Gemini no respondió

    cliente._enviar(Entrada(CELULAR, otro=True, adjunto=NOTA_DE_VOZ))

    assert cliente.texto == "Uy, eso no lo alcancé a ver por aquí. ¿Me lo cuentas escribiendo?"
    assert "no lo vio nadie" not in cliente.traza.texto()  # el que falló fue Gemini


# --- Por WhatsApp, de punta a punta --------------------------------------------------------------


def test_por_whatsapp_un_audio_se_descarga_se_entiende_y_queda_lo_que_dijo(
    webhook_listo: TestClient,  # noqa: F811
    sesion: Session,
    meta: MetaDeMentira,  # noqa: F811
    con_gemini: GeminiDeMentira,
):
    meta.archivos["audio-1"] = ("audio/ogg; codecs=opus", b"OggS-nota")
    con_gemini.entendidos.append(
        Interpretacion(Q.PRECIOS, vehiculo="Moto", lo_que_dice="cuánto vale lavar la moto")
    )

    mandar(webhook_listo, aviso_de_meta(audio()))

    assert meta.leidos == ["wamid.audio-1"]  # el chulito azul y el "escribiendo…"
    assert meta.descargados == ["audio-1"]
    assert con_gemini.contextos[0].adjunto == Adjunto("audio/ogg; codecs=opus", b"OggS-nota")
    assert meta.textos[0].startswith("Servicios para moto:")
    [recibido] = entrantes(sesion)
    # El archivo no se guarda: solo el id y lo que dijo, como si lo hubiera escrito.
    assert recibido.contenido == {
        "archivo": {"id": "audio-1", "mime": "audio/ogg; codecs=opus"},
        "lo_que_dice": "cuánto vale lavar la moto",
    }
    [linea, *_] = historial.conversacion(sesion, CELULAR, leer_parametros(sesion))
    assert linea.dice == "(audio) cuánto vale lavar la moto"


def test_por_whatsapp_si_meta_no_entrega_el_archivo_le_pide_escribir(
    webhook_listo: TestClient,  # noqa: F811
    meta: MetaDeMentira,  # noqa: F811
    con_gemini: GeminiDeMentira,
):
    mandar(webhook_listo, aviso_de_meta(audio("se-perdio")))

    assert con_gemini.contextos == []  # no hubo nada que mostrarle
    assert meta.textos == ["Uy, eso no lo alcancé a ver por aquí. ¿Me lo cuentas escribiendo?"]


def test_un_archivo_muy_pesado_no_se_descarga(enviador, meta: MetaDeMentira):  # noqa: F811
    meta.archivos["pesado"] = ("image/jpeg", b"x" * (whatsapp.PESO_MAXIMO + 1))

    assert enviador.descargar("pesado") is None
    assert meta.descargados == []  # Meta dijo cuánto pesaba: ni se pidió


def test_si_el_escribiendo_falla_igual_se_contesta(
    webhook_listo: TestClient,  # noqa: F811
    meta: MetaDeMentira,  # noqa: F811
):
    meta.falla_el_leido = True

    mandar(
        webhook_listo,
        aviso_de_meta({"from": CELULAR, "id": "wamid.h", "type": "text", "text": {"body": "hola"}}),
    )

    assert meta.textos and meta.textos[0].startswith("¡Hola!")


def test_un_sticker_no_recibe_respuesta(
    webhook_listo: TestClient,  # noqa: F811
    sesion: Session,
    meta: MetaDeMentira,  # noqa: F811
):
    sticker = {"from": CELULAR, "id": "wamid.s", "type": "sticker", "sticker": {"id": "s-1"}}

    mandar(webhook_listo, aviso_de_meta(sticker))

    [recibido] = sesion.scalars(select(MensajeEntrante)).all()
    assert recibido.procesado_en is not None and meta.enviados == [] and meta.leidos == []


# --- Las fotos se pueden apagar (vienen apagadas) -----------------------------------------------


def ver_fotos(sesion: Session, si: bool) -> None:
    sesion.merge(Configuracion(clave="bot_ve_fotos", valor=si, descripcion="Ver fotos"))
    sesion.flush()


def test_las_fotos_vienen_apagadas(sesion: Session):
    assert leer_parametros(sesion).bot_ve_fotos is False


def test_con_las_fotos_apagadas_no_se_descarga_ni_se_le_muestra_a_gemini(
    webhook_listo: TestClient,  # noqa: F811
    meta: MetaDeMentira,  # noqa: F811
    con_gemini: GeminiDeMentira,
):
    meta.archivos["foto-1"] = ("image/jpeg", b"\xff\xd8")

    mandar(webhook_listo, aviso_de_meta(foto()))

    assert meta.descargados == [] and con_gemini.contextos == []
    assert meta.textos == ["Uy, eso no lo alcancé a ver por aquí. ¿Me lo cuentas escribiendo?"]


def test_con_las_fotos_encendidas_gemini_la_ve(
    webhook_listo: TestClient,  # noqa: F811
    sesion: Session,
    meta: MetaDeMentira,  # noqa: F811
    con_gemini: GeminiDeMentira,
):
    ver_fotos(sesion, True)
    meta.archivos["foto-1"] = ("image/jpeg", b"\xff\xd8")
    con_gemini.entendidos.append(
        Interpretacion(Q.PRECIOS, vehiculo="Moto", lo_que_dice="Una moto con barro")
    )

    mandar(webhook_listo, aviso_de_meta(foto()))

    assert meta.descargados == ["foto-1"]
    assert meta.textos[0].startswith("Servicios para moto:")


def test_con_las_fotos_apagadas_el_pie_de_foto_igual_se_lee(
    webhook_listo: TestClient,  # noqa: F811
    meta: MetaDeMentira,  # noqa: F811
    con_gemini: GeminiDeMentira,
):
    con_gemini.entendidos.append(Interpretacion(Q.PRECIOS, vehiculo="Moto"))

    mandar(webhook_listo, aviso_de_meta(foto(pie="cuánto por lavar la moto?")))

    assert meta.descargados == []
    assert con_gemini.contextos[0].adjunto is None  # leyó el texto, sin la foto
    assert meta.textos[0].startswith("Servicios para moto:")


def test_en_configuracion_se_enciende_y_se_apaga(adentro: TestClient, sesion: Session):
    pagina = adentro.get("/configuracion").text
    assert 'name="bot_ve_fotos"' in pagina and "Ver las fotos que mandan" in pagina
