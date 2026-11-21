"""Los videos de los servicios: el administrador sube el de cada servicio en Configuración,
el bot lo manda cuando el cliente escoge ese servicio (una vez al mes) y WhatsApp lo recibe
con el id que da Meta (el reloj lo sube antes y lo renueva antes de que se venza).

Meta no se toca: lo que "recibe" es un Meta de mentira (httpx.MockTransport).
"""

import itertools
import json
from datetime import timedelta
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.bot import textos_editables
from app.bot.mensajes import Mensaje, Opcion
from app.catalogo import videos
from app.catalogo.tablas import VideoDelServicio
from app.config import obtener_entorno
from app.integraciones import bandeja_salida, whatsapp
from app.integraciones.bandeja_salida import EnvioFallido
from app.integraciones.tablas import EstadoMensaje, MensajeEntrante, MensajeSaliente
from app.reloj import tareas
from app.web.comun import plantillas
from pruebas.fabricas import LUNES_8AM, minutos, servicio
from pruebas.navegador import HTMX, aviso
from pruebas.test_bot import Chat

CELULAR = "573001234567"
_ids = itertools.count(1)
ID_DEL_NUMERO = "109876543210"
# Lo mínimo para que el archivo sea un MP4: "ftyp" en los bytes 4 a 8.
MP4 = b"\x00\x00\x00\x18ftypmp42" + bytes(range(256)) * 4


def poner_video(sesion: Session, nombre: str = "Básico", fila: str = "Carros") -> int:
    del_servicio = servicio(sesion, nombre, fila)
    videos.guardar(sesion, del_servicio.id, nombre="basico.mp4", contenido=MP4, ahora=LUNES_8AM)
    return del_servicio.id


def hasta_los_servicios(sesion: Session, telefono: str = CELULAR) -> Chat:
    """Un cliente nuevo que pide turno para su automóvil y llega a la lista de servicios."""
    chat = Chat(sesion, telefono=telefono).escribe("hola").toca("Pedir turno").toca("Acepto")
    return chat.escribe("Laura").toca("Automóvil").toca("Ahora no")


def ya_le_salio_el_video(sesion: Session, servicio_id: int, hace: timedelta) -> None:
    mensaje = bandeja_salida.encolar_respuesta(
        sesion,
        telefono=CELULAR,
        mensaje=Mensaje("🎬 El video", video=servicio_id),
        ahora=LUNES_8AM - hace,
    )
    mensaje.estado, mensaje.enviado_en = EstadoMensaje.ENVIADO, LUNES_8AM - hace
    sesion.flush()


# --- El mensaje con el video -------------------------------------------------------------------


def test_un_mensaje_con_video_lleva_su_texto_debajo_y_se_guarda_en_la_bandeja():
    mensaje = Mensaje("🎬 Así es el *Básico*", video=3)
    assert Mensaje.desde_datos(mensaje.a_datos()) == mensaje
    assert mensaje.a_datos() == {"texto": "🎬 Así es el *Básico*", "video": 3}
    assert Mensaje.desde_datos({"texto": "hola"}).video is None


def test_un_video_no_lleva_botones_y_su_texto_cabe_en_whatsapp():
    with pytest.raises(ValueError, match="video con botones"):
        Mensaje("Mira", video=3, botones=(Opcion("a.b", "Ok"),))
    with pytest.raises(ValueError, match="1024"):
        Mensaje("x" * 1025, video=3)


def test_asi_se_lo_pide_whatsapp():
    contenido = whatsapp.contenido_para_meta(Mensaje("Así es", video=3), CELULAR, "ID-DEL-VIDEO")
    assert contenido["type"] == "video"
    assert contenido["video"] == {"id": "ID-DEL-VIDEO", "caption": "Así es"}
    with pytest.raises(ValueError):
        whatsapp.contenido_para_meta(Mensaje("Así es", video=3), CELULAR)


# --- Las reglas del video ----------------------------------------------------------------------


def test_solo_un_mp4_de_hasta_16_mb(sesion: Session):
    del_servicio = servicio(sesion)
    with pytest.raises(videos.VideoInvalido, match="vacío"):
        videos.guardar(sesion, del_servicio.id, nombre="a.mp4", contenido=b"", ahora=LUNES_8AM)
    with pytest.raises(videos.VideoInvalido, match="no es un video MP4"):
        videos.guardar(
            sesion,
            del_servicio.id,
            nombre="foto.jpg",
            contenido=b"\xff\xd8\xff" * 50,
            ahora=LUNES_8AM,
        )
    pesado = MP4 + b"\x00" * videos.PESO_MAXIMO
    with pytest.raises(videos.VideoInvalido, match="hasta 16 MB"):
        videos.guardar(sesion, del_servicio.id, nombre="a.mp4", contenido=pesado, ahora=LUNES_8AM)
    assert not videos.tiene_video(sesion, del_servicio.id)


def test_cambiarlo_lo_vuelve_a_subir_a_whatsapp_y_quitarlo_lo_borra(sesion: Session):
    servicio_id = poner_video(sesion)
    video = videos.del_servicio(sesion, servicio_id)
    videos.anotar_subida(video, "ID-1", LUNES_8AM)
    assert videos.id_vigente(video, LUNES_8AM + timedelta(days=20)) == "ID-1"
    assert videos.id_vigente(video, LUNES_8AM + timedelta(days=26)) is None  # por vencerse
    videos.guardar(sesion, servicio_id, nombre="nuevo.mp4", contenido=MP4 + b"!", ahora=LUNES_8AM)
    assert video.id_en_meta is None  # el nuevo no lo tiene WhatsApp todavía
    assert video.nombre == "nuevo.mp4" and video.peso == len(MP4) + 1
    assert videos.quitar(sesion, servicio_id)
    assert not videos.tiene_video(sesion, servicio_id)
    assert not videos.quitar(sesion, servicio_id)


# --- El bot lo manda al escoger el servicio ----------------------------------------------------


def test_al_escoger_un_servicio_con_video_el_bot_lo_manda_antes_de_preguntar_cuando(
    sesion: Session,
):
    servicio_id = poner_video(sesion)
    chat = hasta_los_servicios(sesion).toca("Básico")
    video, pregunta = chat.ultimas
    assert video.video == servicio_id
    assert video.texto == "🎬 Así es el lavado *Básico* para tu automóvil, paso a paso."
    assert pregunta.video is None and pregunta.texto.startswith("¿Cuándo quieres venir?")


def test_un_servicio_sin_video_y_lo_decido_en_el_local_siguen_igual(sesion: Session):
    poner_video(sesion, "Encerado")
    chat = hasta_los_servicios(sesion).toca("Básico")
    assert [m.video for m in chat.ultimas] == [None]
    chat = hasta_los_servicios(sesion, "573001112233").toca("Lo decido en el local")
    assert [m.video for m in chat.ultimas] == [None]


def test_no_se_le_repite_el_video_en_un_mes(sesion: Session):
    servicio_id = poner_video(sesion)
    ya_le_salio_el_video(sesion, servicio_id, hace=timedelta(days=10))
    chat = hasta_los_servicios(sesion).toca("Básico")
    assert [m.video for m in chat.ultimas] == [None]


def test_al_mes_se_le_vuelve_a_mandar(sesion: Session):
    servicio_id = poner_video(sesion)
    ya_le_salio_el_video(sesion, servicio_id, hace=timedelta(days=31))
    chat = hasta_los_servicios(sesion).toca("Básico")
    assert chat.ultimas[0].video == servicio_id


def test_si_el_video_no_salio_se_le_puede_volver_a_mandar(sesion: Session):
    servicio_id = poner_video(sesion)
    ya_le_salio_el_video(sesion, servicio_id, hace=timedelta(days=1))
    sesion.scalars(select(MensajeSaliente)).one().estado = EstadoMensaje.FALLIDO
    sesion.flush()
    chat = hasta_los_servicios(sesion).toca("Básico")
    assert chat.ultimas[0].video == servicio_id


def test_el_texto_del_video_se_puede_cambiar_y_sin_el_vehiculo(sesion: Session):
    servicio_id = poner_video(sesion)
    textos_editables.guardar(sesion, "video_del_servicio", "Mira el *{servicio}* 👀")
    chat = hasta_los_servicios(sesion).toca("Básico")
    assert chat.ultimas[0].texto == "Mira el *Básico* 👀"
    assert chat.ultimas[0].video == servicio_id


# --- Sale por WhatsApp -------------------------------------------------------------------------


class MetaDeMentira:
    """Hace de la Cloud API: recibe videos (/media) y mensajes (/messages)."""

    def __init__(self) -> None:
        self.subidos: list[dict[str, Any]] = []
        self.enviados: list[dict[str, Any]] = []
        self.falla_al_subir = False

    def __call__(self, peticion: httpx.Request) -> httpx.Response:
        assert peticion.headers["Authorization"] == "Bearer token-de-prueba"
        if peticion.url.path == f"/v26.0/{ID_DEL_NUMERO}/media":
            if self.falla_al_subir:
                return httpx.Response(
                    400, json={"error": {"code": 131053, "message": "Media upload error"}}
                )
            cuerpo = peticion.read()
            assert b'name="messaging_product"' in cuerpo and b"video/mp4" in cuerpo
            self.subidos.append({"peso": len(cuerpo)})
            return httpx.Response(200, json={"id": f"VIDEO-{len(self.subidos)}"})
        assert peticion.url.path == f"/v26.0/{ID_DEL_NUMERO}/messages"
        self.enviados.append(json.loads(peticion.content))
        return httpx.Response(200, json={"messages": [{"id": f"wamid.{len(self.enviados)}"}]})


@pytest.fixture
def meta() -> MetaDeMentira:
    return MetaDeMentira()


@pytest.fixture
def enviador(monkeypatch: pytest.MonkeyPatch, meta: MetaDeMentira, reloj) -> whatsapp.WhatsApp:
    entorno = obtener_entorno()
    for campo, valor in {
        "whatsapp_token": "token-de-prueba",
        "whatsapp_id_numero": ID_DEL_NUMERO,
        "whatsapp_version_api": "v26.0",
    }.items():
        monkeypatch.setattr(entorno, campo, valor)
    return whatsapp.WhatsApp(entorno, http=httpx.Client(transport=httpx.MockTransport(meta)))


def video_por_salir(sesion: Session, servicio_id: int) -> MensajeSaliente:
    """El cliente acaba de escribir (ventana de 24 horas abierta) y su video está en la bandeja."""
    sesion.add(
        MensajeEntrante(
            id_meta=f"wamid.entrante.{next(_ids)}",
            telefono=CELULAR,
            tipo="text",
            contenido={"texto": "Básico"},
            recibido_en=LUNES_8AM - minutos(1),
        )
    )
    mensaje = bandeja_salida.encolar_respuesta(
        sesion, telefono=CELULAR, mensaje=Mensaje("🎬 Así es", video=servicio_id), ahora=LUNES_8AM
    )
    sesion.flush()
    return mensaje


def test_el_video_sale_con_el_id_que_da_meta_y_no_se_vuelve_a_subir(
    sesion: Session, enviador: whatsapp.WhatsApp, meta: MetaDeMentira
):
    servicio_id = poner_video(sesion)
    assert enviador.enviar(sesion, video_por_salir(sesion, servicio_id)) == "wamid.1"
    assert len(meta.subidos) == 1
    assert meta.enviados[0]["type"] == "video"
    assert meta.enviados[0]["video"] == {"id": "VIDEO-1", "caption": "🎬 Así es"}
    assert sesion.get(VideoDelServicio, servicio_id).id_en_meta == "VIDEO-1"

    enviador.enviar(sesion, video_por_salir(sesion, servicio_id))
    assert len(meta.subidos) == 1  # ya lo tenía: sale con el mismo id
    assert meta.enviados[1]["video"]["id"] == "VIDEO-1"


def test_si_el_video_no_sale_no_se_reintenta_para_no_frenar_lo_demas(
    sesion: Session, enviador: whatsapp.WhatsApp, meta: MetaDeMentira
):
    servicio_id = poner_video(sesion)
    meta.falla_al_subir = True
    with pytest.raises(EnvioFallido) as error:
        enviador.enviar(sesion, video_por_salir(sesion, servicio_id))
    assert not error.value.reintentar
    assert "131053" in str(error.value)
    videos.quitar(sesion, servicio_id)
    with pytest.raises(EnvioFallido, match="ya no tiene video") as error:
        enviador.enviar(sesion, video_por_salir(sesion, servicio_id))
    assert not error.value.reintentar
    assert meta.enviados == []


# --- El reloj se lo sube a WhatsApp antes ------------------------------------------------------


class SubidorDeMentira:
    def __init__(self) -> None:
        self.subidos: list[str] = []
        self.falla = False

    def subir_video(self, contenido: bytes, tipo: str, nombre: str) -> str:
        if self.falla:
            raise EnvioFallido("WhatsApp respondió 500", reintentar=True)
        assert tipo == "video/mp4" and contenido == MP4
        self.subidos.append(nombre)
        return f"VIDEO-{len(self.subidos)}"


def test_el_reloj_sube_los_videos_y_los_renueva_antes_de_que_se_venzan(sesion: Session):
    servicio_id = poner_video(sesion)
    subidor = SubidorDeMentira()
    tarea = tareas.subir_videos(subidor)
    abrir = lambda: Session(bind=sesion.connection(), join_transaction_mode="create_savepoint")  # noqa: E731

    assert tarea.hacer(abrir, LUNES_8AM) == ["1 video de un servicio quedó listo en WhatsApp."]
    assert tarea.hacer(abrir, LUNES_8AM + timedelta(days=24)) == []  # todavía sirve
    assert tarea.hacer(abrir, LUNES_8AM + timedelta(days=25)) == [
        "1 video de un servicio quedó listo en WhatsApp."
    ]
    assert subidor.subidos == ["basico.mp4", "basico.mp4"]
    sesion.expire_all()
    assert sesion.get(VideoDelServicio, servicio_id).id_en_meta == "VIDEO-2"


def test_si_whatsapp_no_lo_recibe_el_reloj_espera_una_hora(sesion: Session):
    poner_video(sesion)
    subidor = SubidorDeMentira()
    subidor.falla = True
    tarea = tareas.subir_videos(subidor)
    abrir = lambda: Session(bind=sesion.connection(), join_transaction_mode="create_savepoint")  # noqa: E731
    assert tarea.hacer(abrir, LUNES_8AM) == []
    subidor.falla = False
    assert tarea.hacer(abrir, LUNES_8AM + minutos(30)) == []  # no lo intenta en cada vuelta
    assert tarea.hacer(abrir, LUNES_8AM + minutos(61)) == [
        "1 video de un servicio quedó listo en WhatsApp."
    ]


def test_el_reloj_sube_videos_solo_si_whatsapp_esta_conectado():
    nombres = [t.nombre for t in tareas.del_servidor(None)]
    assert "los videos de los servicios" not in nombres
    nombres = [t.nombre for t in tareas.del_servidor(SubidorDeMentira())]  # type: ignore[arg-type]
    assert nombres[-1] == "los videos de los servicios"


# --- En el dashboard ---------------------------------------------------------------------------


def test_cada_servicio_del_menu_tiene_su_boton_de_video(adentro: TestClient, sesion: Session):
    servicio_id = poner_video(sesion)
    pagina = adentro.get("/configuracion").text
    assert f'id="video-servicio-{servicio_id}"' in pagina
    assert "🎬 Ver video" in pagina
    assert "Subir video" in pagina  # los que no tienen


def test_subir_ver_y_quitar_el_video_de_un_servicio(adentro: TestClient, sesion: Session):
    del_servicio = servicio(sesion, "Encerado")
    url = f"/configuracion/servicios/{del_servicio.id}/video"
    assert "Todavía no tiene video." in adentro.get(url, headers=HTMX).text

    respuesta = adentro.post(
        url, headers=HTMX, files={"archivo": ("encerado.mp4", MP4, "video/mp4")}
    )
    assert respuesta.status_code == 200
    assert "Video de Encerado guardado" in aviso(respuesta)
    assert "encerado.mp4" in respuesta.text and "hx-swap-oob" in respuesta.text
    assert "En un minuto queda listo en WhatsApp" in respuesta.text

    archivo = adentro.get(f"{url}.mp4")
    assert archivo.status_code == 200
    assert archivo.headers["content-type"] == "video/mp4"
    assert archivo.content == MP4

    respuesta = adentro.post(f"{url}/quitar", headers=HTMX)
    assert "Se quitó el video de Encerado." in aviso(respuesta)
    assert adentro.get(f"{url}.mp4").status_code == 404


def test_un_archivo_que_no_es_mp4_no_se_guarda_y_dice_por_que(adentro: TestClient, sesion: Session):
    del_servicio = servicio(sesion)
    respuesta = adentro.post(
        f"/configuracion/servicios/{del_servicio.id}/video",
        headers=HTMX,
        files={"archivo": ("foto.jpg", b"\xff\xd8\xff" * 50, "image/jpeg")},
    )
    assert "«foto.jpg» no es un video MP4." in respuesta.text
    assert not videos.tiene_video(sesion, del_servicio.id)


def test_sin_entrar_no_se_ve_ni_se_cambia_el_video(web: TestClient, sesion: Session, admin):
    servicio_id = poner_video(sesion)
    respuesta = web.get(f"/configuracion/servicios/{servicio_id}/video.mp4", follow_redirects=False)
    assert respuesta.status_code in (401, 303)
    respuesta = web.post(
        f"/configuracion/servicios/{servicio_id}/video/quitar", follow_redirects=False
    )
    assert respuesta.status_code in (401, 303)
    assert videos.tiene_video(sesion, servicio_id)


def test_en_probar_el_bot_y_en_la_conversacion_se_ve_el_video():
    burbuja = plantillas.env.from_string(
        '{% import "_burbujas_del_bot.html" as b %}{{ b.del_bot(m, "573009998877") }}'
    ).render(m=Mensaje("🎬 Así es el *Básico*", video=7))
    assert 'src="/configuracion/servicios/7/video.mp4"' in burbuja
    assert "<strong>Básico</strong>" in burbuja


def test_en_mensajes_dice_que_salio_un_video(sesion: Session):
    from app.web.mensajes import _lo_que_dice

    mensaje = bandeja_salida.encolar_respuesta(
        sesion, telefono=CELULAR, mensaje=Mensaje("Así es el Básico", video=7), ahora=LUNES_8AM
    )
    assert _lo_que_dice(mensaje) == "🎬 Video del servicio. Así es el Básico"
