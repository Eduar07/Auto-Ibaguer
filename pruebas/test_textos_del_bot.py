"""Los textos del bot que se cambian en Configuración > Textos del bot.

Primero las reglas (qué datos lleva cada texto y cuáles no se pueden quitar), después
que el bot y los avisos usen lo que se guardó, y al final la pantalla.
"""

import logging
import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.bot import conversacion, textos_editables
from app.bot.avisos import mensaje_del_aviso
from app.bot.mensajes import MAX_TEXTO_INTERACTIVO, Entrada
from app.bot.textos import TEXTOS, Textos, huecos
from app.bot.textos_editables import DATOS, EDITABLES, GRUPOS, TextoInvalido, validar
from app.catalogo.parametros import PREFIJO_TEXTO_DEL_BOT, leer_parametros
from app.catalogo.tablas import Configuracion
from app.clientes.reglas import LARGO_MAXIMO_NOMBRE
from app.turnos.estados import TipoAviso
from pruebas.fabricas import LUNES_8AM
from pruebas.navegador import HTMX, aviso, error

CELULAR = "573001234567"


def guardados(sesion: Session) -> dict[str, str]:
    filas = sesion.scalars(
        select(Configuracion).where(Configuracion.clave.startswith(PREFIJO_TEXTO_DEL_BOT))
    )
    return {fila.clave.removeprefix(PREFIJO_TEXTO_DEL_BOT): fila.valor for fila in filas}


# --- El catálogo ---------------------------------------------------------------------------------


def test_cada_texto_editable_existe_y_se_explica():
    assert len(EDITABLES) == sum(len(grupo.textos) for grupo in GRUPOS)  # ninguno repetido
    for editable in EDITABLES.values():
        assert editable.clave in TEXTOS
        assert set(editable.datos) <= DATOS.keys(), editable.clave  # cada dato dice qué es
        assert editable.opcionales <= set(editable.datos), editable.clave
        assert validar(editable, editable.original) == editable.original  # el original sirve
        assert len(editable.original) <= editable.maximo, editable.clave


def test_cada_texto_se_ve_en_la_vista_previa(sesion: Session):
    parametros = leer_parametros(sesion)
    for editable in EDITABLES.values():
        mensaje = textos_editables.vista_previa(editable, editable.original, parametros)
        assert "{" not in mensaje.texto, editable.clave  # quedaron llenos todos los datos


# Lo más largo que puede llegar a ser cada dato, por los límites de cada campo.
LARGO_DE_CADA_DATO = {
    "negocio": 60,  # el nombre del lavadero: hasta 60 letras
    "horario": 100,  # "los lunes, martes, miércoles, jueves, viernes y sábados de ... a ..."
    "apertura": 40,  # "el miércoles a las 10:30 a.m."
    "telefono": 20,
    "nombre": LARGO_MAXIMO_NOMBRE,
    "vehiculo": 30 + 8,  # el tipo de vehículo y la placa
    "servicio": 40 + 14,  # el nombre del servicio y su precio
    "fila": 30,
    "como_va": 100,  # "12 vehículos esperando; un turno nuevo entraría en aprox. 2 h 30 min"
    "numero": 15,
    "numeros": 30,
    "turno": 15,
    "gracia": 2,
    "hora": 16,
    "adelante": 30,
    "adelante_mayuscula": 30,
    "espera": 40,
    "avisar": 1,
    "filas": 300,  # una línea por fila
    "servicios": 600,  # si no cabe, el bot quita las descripciones o lo recorta
    "lavadas": 15,  # "1234 lavadas"
    "ultima": 30,  # "el 30 de septiembre de 2025"
}


def _el_mas_largo(editable, datos) -> str:
    """El texto más largo que se deja guardar, con cada dato en su largo máximo."""
    huecos_del_texto = "".join(f"{{{dato}}}" for dato in editable.datos)
    return ("y" * (editable.maximo - len(huecos_del_texto)) + huecos_del_texto).format_map(datos)


def test_un_texto_largo_con_datos_largos_cabe_en_whatsapp():
    """El texto más largo que se deja guardar, con sus datos en el largo máximo, cabe en un
    mensaje de WhatsApp con botones (1.024 caracteres). Así un texto cambiado nunca deja
    al bot sin poder contestar."""
    assert DATOS.keys() - {"situacion"} == LARGO_DE_CADA_DATO.keys()
    datos = {nombre: "x" * largo for nombre, largo in LARGO_DE_CADA_DATO.items()}
    # {situacion} es una de las partes del turno confirmado: la más larga.
    partes = [e for e in EDITABLES.values() if e.dentro_de == "turno_confirmado"]
    datos["situacion"] = max((_el_mas_largo(parte, datos) for parte in partes), key=len)
    for editable in EDITABLES.values():
        if editable.dentro_de or editable.clave == "precios":  # precios se recorta solo
            continue
        assert len(_el_mas_largo(editable, datos)) <= MAX_TEXTO_INTERACTIVO, editable.clave


# --- Revisar lo que escribe el administrador -------------------------------------------------


@pytest.mark.parametrize(
    ("clave", "escrito", "motivo"),
    [
        ("saludo", "   ", "el bot no puede mandar un mensaje vacío"),
        ("despedida", "x" * 501, "hasta 500 caracteres y este tiene 501"),
        ("aviso_llamado", "Te toca *{turno}*, ven antes de {hora", "Revisa las llaves"),
        ("aviso_llamado", "Te toca {turno} hasta {hora:>10}", "Revisa las llaves"),
        ("aviso_llamado", "Te toca {turno} hasta {hora}, {cliente}", "{cliente} no es un dato"),
        ("despedida", "Chao, {nombre}", "Este mensaje no lleva datos: quita {nombre}"),
        ("aviso_llamado", "¡Te toca, {turno}! Ven ya.", "Falta {hora}: el cliente lo necesita"),
        ("turno_confirmado", "Listo.", "Faltan {numero} y {situacion}"),
    ],
)
def test_un_texto_que_no_sirve_dice_por_que(clave, escrito, motivo):
    with pytest.raises(TextoInvalido) as error_de_texto:
        validar(EDITABLES[clave], escrito)
    assert motivo in str(error_de_texto.value)


def test_los_datos_opcionales_se_pueden_quitar():
    sin_negocio = "¡Hola! Bienvenido. ¿En qué te ayudamos?"

    assert validar(EDITABLES["saludo"], sin_negocio) == sin_negocio


def test_el_texto_se_guarda_limpio():
    escrito = "  ¡Hola!   Bienvenido\r\n\r\n\r\n\r\n a {negocio}.\x07  "

    assert validar(EDITABLES["saludo"], escrito) == "¡Hola! Bienvenido\n\na {negocio}."


# --- Guardar y volver al original ----------------------------------------------------------------


def test_guardar_un_texto_y_volver_al_original(sesion: Session):
    assert textos_editables.guardar(sesion, "despedida", "¡Gracias por escribir!")
    assert guardados(sesion) == {"despedida": "¡Gracias por escribir!"}

    original = EDITABLES["despedida"].original
    assert not textos_editables.guardar(sesion, "despedida", original)
    assert guardados(sesion) == {}  # igual al original: no queda guardado


def test_un_texto_que_no_sirve_no_se_guarda(sesion: Session):
    with pytest.raises(TextoInvalido):
        textos_editables.guardar(sesion, "aviso_llamado", "Te toca {turno}")

    assert guardados(sesion) == {}


# --- El bot usa lo que se guardó -----------------------------------------------------------------


def test_el_bot_saluda_con_el_texto_cambiado(sesion: Session, reloj):
    textos_editables.guardar(sesion, "saludo", "¡Bienvenido a {negocio}, parcero!")

    [saludo] = conversacion.atender(sesion, Entrada(CELULAR, texto="hola"), ahora=LUNES_8AM)

    assert saludo.texto == "¡Bienvenido a Auto Ibaguer, parcero!"
    assert [boton.id for boton in saludo.botones] == ["menu.pedir", "menu.fila", "menu.precios"]


def test_los_avisos_de_la_fila_salen_con_el_texto_cambiado(sesion: Session):
    textos_editables.guardar(sesion, "aviso_llamado", "*{turno}*, ¡te toca! Llega antes de {hora}.")
    parametros = leer_parametros(sesion)
    contenido = {"turno": "C-07", "gracia_hasta": "2026-10-26T13:15:00+00:00"}

    mensaje = mensaje_del_aviso(TipoAviso.LLAMADO.value, contenido, parametros)

    assert mensaje.texto == "*C-07*, ¡te toca! Llega antes de las 8:15 a.m."
    assert [boton.titulo for boton in mensaje.botones] == ["En camino", "Ya estoy aquí"]


def test_si_un_texto_guardado_ya_no_sirve_el_bot_usa_el_original(caplog):
    # Por ejemplo, alguien lo cambió directo en la base con un dato que el bot no tiene.
    texto = Textos({"despedida": "Chao, {apodo}"})

    with caplog.at_level(logging.WARNING, logger="app.bot.textos"):
        assert texto("despedida") == TEXTOS["despedida"]

    assert "«despedida» cambiado en Configuración no se pudo usar" in caplog.text


def test_los_textos_que_no_se_cambiaron_son_los_originales():
    assert Textos({})("menu") == TEXTOS["menu"]
    assert set(huecos(TEXTOS["aviso_llamado"])) == {"turno", "hora"}


# --- La pantalla ---------------------------------------------------------------------------------


def test_la_pantalla_pide_entrar(web: TestClient):
    respuesta = web.get("/configuracion/textos", follow_redirects=False)

    assert respuesta.headers["location"] == "/entrar"


def test_la_pantalla_muestra_todos_los_textos_por_grupo(adentro: TestClient, sesion: Session):
    textos_editables.guardar(sesion, "despedida", "¡Gracias por escribir!")
    sesion.commit()

    pagina = adentro.get("/configuracion/textos").text

    for grupo in GRUPOS:
        assert f'id="grupo-{grupo.id}"' in pagina
    assert pagina.count('class="texto-bot"') == len(EDITABLES)
    assert "¡Gracias por escribir!" in pagina
    assert pagina.count(">Cambiado</span>") == 1
    assert 'href="/configuracion/textos"' in adentro.get("/configuracion").text


def test_la_vista_previa_muestra_el_mensaje_con_datos_de_ejemplo(adentro: TestClient):
    respuesta = adentro.post(
        "/configuracion/textos/turno_confirmado/vista-previa",
        data={"texto": "Listo, {nombre}: tu turno es *{numero}*.\n\n{situacion}"},
        headers=HTMX,
    )

    assert respuesta.status_code == 200
    assert (
        "Listo, Ana: tu turno es <strong>C-07</strong>.<br><br>Hay un puesto libre"
        in respuesta.text
    )
    assert re.search(r'id="error-turno_confirmado" hx-swap-oob="true"\s+hidden', respuesta.text)


def test_la_vista_previa_dice_que_falta(adentro: TestClient):
    respuesta = adentro.post(
        "/configuracion/textos/aviso_llamado/vista-previa",
        data={"texto": "¡Te toca, {turno}!"},
        headers=HTMX,
    )

    assert "Falta {hora}: el cliente lo necesita" in respuesta.text
    assert "Cuando el texto sirva, aquí lo ves" in respuesta.text


def test_guardar_desde_la_pantalla(adentro: TestClient, sesion: Session):
    respuesta = adentro.post(
        "/configuracion/textos/despedida", data={"texto": "¡Gracias, vuelve pronto!"}, headers=HTMX
    )

    assert aviso(respuesta) == "«Despedida» guardado. El bot lo usa desde el siguiente mensaje."
    assert ">Cambiado</span>" in respuesta.text and "Volver al original" in respuesta.text
    assert guardados(sesion) == {"despedida": "¡Gracias, vuelve pronto!"}

    original = EDITABLES["despedida"].original
    respuesta = adentro.post("/configuracion/textos/despedida", data={"texto": original})

    assert aviso(respuesta) == "«Despedida» quedó con el texto original."
    assert guardados(sesion) == {}


def test_un_texto_con_error_no_se_guarda_desde_la_pantalla(adentro: TestClient, sesion: Session):
    respuesta = adentro.post(
        "/configuracion/textos/aviso_llamado", data={"texto": "¡Te toca!"}, headers=HTMX
    )

    assert error(respuesta) == "No se guardó: revisa el texto marcado en rojo."
    assert "Faltan {turno} y {hora}" in respuesta.text
    assert "¡Te toca!</textarea>" in respuesta.text  # lo que escribió sigue ahí
    assert guardados(sesion) == {}


def test_un_texto_guardado_que_ya_no_sirve_se_marca(adentro: TestClient, sesion: Session):
    sesion.add(
        Configuracion(clave="texto_bot.despedida", valor="Chao {apodo}", descripcion="prueba")
    )
    sesion.commit()

    pagina = adentro.get("/configuracion/textos").text

    assert "No se puede usar</span>" in pagina
    assert "el bot está usando el original" in pagina


def test_un_texto_que_no_existe(adentro: TestClient):
    assert adentro.post("/configuracion/textos/inventado", data={"texto": "x"}).status_code == 404


def test_otro_sitio_no_puede_cambiar_un_texto(adentro: TestClient, sesion: Session):
    respuesta = adentro.post(
        "/configuracion/textos/despedida",
        data={"texto": "Hackeado"},
        headers={"Origin": "https://otro-sitio.example"},
    )

    assert respuesta.status_code == 403
    assert guardados(sesion) == {}
