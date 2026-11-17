"""La página para probar el bot desde el dashboard, antes de tener WhatsApp conectado."""

import re

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.bot.interprete import Intencion, Interpretacion
from app.config import obtener_entorno
from app.formato import dias_de_atencion, telefono
from app.integraciones import gemini
from app.turnos import casos_de_uso as casos
from app.web.comun import plantillas
from pruebas.fabricas import LUNES_8AM, cliente_con_vehiculo, minutos, ocupar_puestos, por_whatsapp
from pruebas.navegador import HTMX
from pruebas.test_gemini import GeminiDeMentira

CELULAR = "573001234567"


def escribir(adentro: TestClient, **datos: str) -> str:
    respuesta = adentro.post(
        "/probar-bot/mensaje", data={"telefono": CELULAR, **datos}, headers=HTMX
    )
    assert respuesta.status_code == 200
    return respuesta.text


def test_la_pagina_abre_una_charla_con_un_celular_inventado(adentro: TestClient):
    pagina = adentro.get("/probar-bot").text

    assert "Probar el bot" in pagina and 'id="charla"' in pagina
    inventado = re.search(r'name="telefono" value="(\d+)"', pagina).group(1)
    assert inventado.startswith("5799") and len(inventado) == 12
    assert "(celular inventado)" in pagina


def test_se_le_escribe_y_se_tocan_sus_botones(adentro: TestClient):
    respuesta = escribir(adentro, texto="hola")

    assert '<div class="burbuja burbuja--yo"><p>hola</p></div>' in respuesta
    assert "Bienvenido a Auto Ibaguer" in respuesta
    assert '"opcion": "menu.pedir"' in respuesta  # el botón le dice al servidor cuál tocó

    respuesta = escribir(adentro, opcion="menu.precios", titulo="Servicios y precios")
    assert "<p>Servicios y precios</p>" in respuesta  # lo que tocó, como lo ve el cliente
    assert "¿Para qué vehículo quieres ver los precios?" in respuesta


def test_la_lista_de_servicios_se_abre_como_en_whatsapp(adentro: TestClient):
    for paso in ({"opcion": "menu.pedir"}, {"opcion": "datos.si"}, {"texto": "Ana"}):
        escribir(adentro, **paso)
    respuesta = escribir(adentro, texto="moto")
    respuesta = escribir(adentro, opcion="placa.no", titulo="Ahora no")

    assert '<details class="burbuja__lista">' in respuesta
    assert '<summary class="burbuja__boton">Ver servicios</summary>' in respuesta
    assert (
        "<strong>Básico</strong><span>$18.000 · Lavado a presión, champú y secado</span>"
        in respuesta
    )


def test_los_avisos_de_la_fila_llegan_a_la_charla(adentro: TestClient, sesion: Session):
    lavando = ocupar_puestos(sesion, "C1", "C2")[0]
    vehiculo = cliente_con_vehiculo(sesion, telefono=CELULAR)
    nuevo = por_whatsapp(sesion, vehiculo=vehiculo)  # no hay puesto libre: espera
    pagina = adentro.get(f"/probar-bot?celular={CELULAR}").text
    desde = int(re.search(r'"desde": (\d+)', pagina).group(1))

    casos.marcar_listo(sesion, lavando, ahora=LUNES_8AM + minutos(30))  # C1 libre: lo llaman
    respuesta = adentro.get(f"/probar-bot/avisos?telefono={CELULAR}&desde={desde}").text

    assert 'hx-swap-oob="beforeend:#charla"' in respuesta
    assert "Aviso de la fila" in respuesta
    assert f"Ya hay un puesto libre para <strong>{nuevo.numero}</strong>" in respuesta
    assert "Tienes hasta las 8:45 a.m. para llegar" in respuesta
    # La próxima pregunta empieza después de este aviso: no se repite.
    assert int(re.search(r'"desde": (\d+)', respuesta).group(1)) > desde


def test_en_produccion_la_pagina_no_existe(adentro: TestClient, monkeypatch):
    monkeypatch.setattr(obtener_entorno(), "entorno", "produccion")

    assert adentro.get("/probar-bot").status_code == 404
    assert adentro.post("/probar-bot/mensaje", data={"telefono": CELULAR}).status_code == 404


def test_configuracion_enlaza_la_pagina_solo_en_desarrollo(adentro: TestClient, monkeypatch):
    assert 'href="/probar-bot"' in adentro.get("/configuracion").text

    monkeypatch.setattr(obtener_entorno(), "entorno", "produccion")
    assert 'href="/probar-bot"' not in adentro.get("/configuracion").text


def test_los_textos_del_bot_se_ven_como_en_whatsapp():
    como_en_whatsapp = plantillas.env.filters["whatsapp"]

    assert str(como_en_whatsapp("Tu turno es *C-05*.\nTe esperamos")) == (
        "Tu turno es <strong>C-05</strong>.<br>Te esperamos"
    )
    assert (
        str(como_en_whatsapp("<b>hola</b> *ok*")) == "&lt;b&gt;hola&lt;/b&gt; <strong>ok</strong>"
    )


def test_dias_de_atencion_y_telefono_a_la_colombiana():
    assert dias_de_atencion(range(1, 8)) == "todos los días"
    assert dias_de_atencion([1, 2, 3, 4, 5, 6]) == "de lunes a sábado"
    assert dias_de_atencion([1, 3, 6]) == "los lunes, miércoles y sábados"
    assert dias_de_atencion([7]) == "los domingos"
    assert telefono("573227556850") == "322 755 6850"


def mandar_archivo(adentro: TestClient, nombre: str, mime: str, datos: bytes, texto: str = ""):
    return adentro.post(
        "/probar-bot/mensaje",
        data={"telefono": CELULAR, "texto": texto},
        files={"archivo": (nombre, datos, mime)},
        headers=HTMX,
    )


def test_se_le_manda_un_audio_y_se_ve_lo_que_oyo_gemini(adentro: TestClient, monkeypatch):
    de_mentira = GeminiDeMentira(
        Interpretacion(Intencion.PRECIOS, vehiculo="Moto", lo_que_dice="cuánto vale la moto")
    )
    monkeypatch.setattr(gemini, "para_el_bot", lambda _: de_mentira)

    respuesta = mandar_archivo(adentro, "nota.ogg", "audio/ogg", b"OggS")

    assert respuesta.status_code == 200
    assert "<strong>Audio</strong>" in respuesta.text
    assert "Gemini oyó: cuánto vale la moto" in respuesta.text
    assert "Servicios para moto:" in respuesta.text
    assert de_mentira.contextos[0].adjunto.datos == b"OggS"


def test_sin_gemini_una_foto_pide_que_lo_escriba(adentro: TestClient):
    respuesta = mandar_archivo(adentro, "carro.jpg", "image/jpeg", b"\xff\xd8", texto="esta")

    assert "<strong>Foto</strong> · esta" in respuesta.text
    assert "no lo alcancé a ver" not in respuesta.text  # trae pie: se lee como texto


def test_un_archivo_que_no_es_foto_ni_audio_no_se_manda(adentro: TestClient):
    respuesta = mandar_archivo(adentro, "video.mp4", "video/mp4", b"x")

    assert respuesta.status_code == 400
