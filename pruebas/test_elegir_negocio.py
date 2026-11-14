"""Entrar por negocio: después del usuario y la contraseña se escoge el lavadero o el
parqueadero (/inicio), y cada pantalla muestra solo las secciones de su negocio.

Lo que tiene que cumplirse siempre:
- al entrar (y en /) se llega a la pantalla de elegir, que muestra solo los dos negocios;
- cada negocio tiene su menú: lo del lavadero (el bot, el turno nuevo, los comentarios) no
  sale en el parqueadero, y las secciones del parqueadero no salen en el lavadero;
- el nombre del negocio, arriba a la izquierda, lleva de vuelta a elegir;
- las cifras de hoy de cada negocio son las de hoy en Bogotá: lo de ayer no cuenta.
"""

import re
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.comentarios import servicio as comentarios
from app.comentarios.tablas import TipoComentario
from app.parqueadero import casos_de_uso as parqueo
from app.parqueadero.estados import TipoParqueo
from app.turnos import casos_de_uso as casos
from app.turnos.estados import MedioPago
from app.web import inicio
from app.web.negocios import LAVADERO, NEGOCIOS, PARQUEADERO, de_la_seccion
from pruebas.fabricas import BOGOTA, LUNES_8AM, minutos, ocupar_puestos, por_whatsapp, turno_listo
from pruebas.navegador import CLAVE

AYER = timedelta(days=1)
LUNES_6AM = LUNES_8AM - minutos(120)


def parte(pagina: str, patron: str) -> str:
    encontrada = re.search(patron, pagina, re.DOTALL)
    return encontrada.group(0) if encontrada else ""


def encabezado(pagina: str) -> str:
    return parte(pagina, r"<header.*?</header>")


def secciones(pagina: str, nav: str) -> list[str]:
    """Las direcciones del menú de arriba (navegacion) o de la barra del celular."""
    return re.findall(r'<a href="([^"]+)"', parte(pagina, rf'<nav class="{nav}".*?</nav>'))


# --- Al entrar se escoge el negocio ---------------------------------------------------------


def test_al_entrar_se_escoge_el_negocio(web: TestClient, admin):
    respuesta = web.post(
        "/entrar", data={"usuario": "eduar", "clave": CLAVE}, follow_redirects=False
    )

    assert respuesta.headers["location"] == "/inicio"
    pagina = web.get("/inicio").text
    assert re.findall(r'class="negocio__enlace" href="([^"]+)"', pagina) == [
        "/fila",
        "/parqueadero",
    ]
    assert ">Lavadero</a></h2>" in pagina and ">Parqueadero</a></h2>" in pagina
    # Todavía no está en ningún negocio: ni menú de secciones ni lo del lavadero arriba.
    assert secciones(pagina, "navegacion") == secciones(pagina, "navegacion-celular") == []
    assert "Nuevo turno" not in encabezado(pagina)
    assert "estado-bot" not in encabezado(pagina)
    assert "/comentarios/contador" not in pagina


def test_la_raiz_y_entrar_con_sesion_llevan_a_elegir(adentro: TestClient):
    assert adentro.get("/", follow_redirects=False).headers["location"] == "/inicio"
    assert adentro.get("/entrar", follow_redirects=False).headers["location"] == "/inicio"


def test_sin_sesion_elegir_pide_entrar(web: TestClient):
    respuesta = web.get("/inicio", follow_redirects=False)

    assert respuesta.status_code == 303 and respuesta.headers["location"] == "/entrar"


@pytest.mark.parametrize(
    ("hora", "esperado"),
    [
        (5, "Buenos días"),
        (11, "Buenos días"),
        (12, "Buenas tardes"),
        (18, "Buenas tardes"),
        (19, "Buenas noches"),
        (2, "Buenas noches"),
    ],
)
def test_el_saludo_va_con_la_hora_de_bogota(hora: int, esperado: str):
    assert inicio.saludo(datetime(2026, 10, 26, hora, 30, tzinfo=BOGOTA)) == esperado


def test_saluda_al_que_entro(adentro: TestClient, reloj):
    reloj["ahora"] = LUNES_8AM.replace(hour=15)

    assert "<h1>Buenas tardes, eduar</h1>" in adentro.get("/inicio").text


# --- Lo de hoy de cada negocio -------------------------------------------------------------


@pytest.fixture
def un_dia_en_el_lavadero(sesion: Session, caja_del_lavadero):
    """Dos esperando, uno lavándose en C2, uno listo por cobrar y uno ya cobrado ($40.000).
    Ayer se cobró otro, que hoy no cuenta. Y un comentario por atender."""
    ayer = turno_listo(sesion, placa="AAA111", ahora=LUNES_8AM - AYER)
    casos.cobrar(sesion, ayer, medio_pago=MedioPago.EFECTIVO, ahora=LUNES_8AM - AYER + minutos(60))
    cobrado = turno_listo(sesion, placa="BBB222")
    casos.cobrar(sesion, cobrado, medio_pago=MedioPago.NEQUI, ahora=LUNES_8AM + minutos(60))
    turno_listo(sesion, placa="CCC333", ahora=LUNES_8AM + minutos(10))
    ocupar_puestos(sesion, "C2")
    por_whatsapp(sesion)
    por_whatsapp(sesion)
    comentarios.registrar(sesion, TipoComentario.QUEJA, "me atendieron mal")


@pytest.fixture
def un_dia_en_el_parqueadero(sesion: Session):
    """La caja se abrió a las 6:00 a.m.; un carro estuvo una hora y pagó $3.000 y una moto
    sigue adentro."""
    parqueo.abrir_caja(sesion, base=20_000, ahora=LUNES_6AM)
    carro = parqueo.registrar_entrada(
        sesion, placa="ABC123", tipo=TipoParqueo.CARRO, ahora=LUNES_6AM + minutos(30)
    )
    parqueo.cobrar_salida(
        sesion, carro.id, medio_pago=MedioPago.EFECTIVO, ahora=LUNES_6AM + minutos(90)
    )
    parqueo.registrar_entrada(sesion, placa="XYZ12A", tipo=TipoParqueo.MOTO, ahora=LUNES_6AM)


def test_lo_de_hoy_en_el_lavadero(sesion: Session, un_dia_en_el_lavadero):
    hoy = inicio.hoy_en_el_lavadero(sesion, LUNES_8AM + minutos(120))

    assert (hoy.en_espera, hoy.en_lavado, hoy.por_cobrar) == (2, 1, 1)
    assert (hoy.cobrado, hoy.cobros) == (40_000, 1)
    assert hoy.comentarios_pendientes == 1


def test_lo_de_hoy_en_el_parqueadero(sesion: Session, un_dia_en_el_parqueadero):
    hoy = inicio.hoy_en_el_parqueadero(sesion, LUNES_8AM)

    assert (hoy.adentro, hoy.salidas, hoy.recogido) == (1, 1, 3_000)
    assert hoy.caja is not None and not hoy.caja_de_otro_dia


def test_las_cifras_en_la_pantalla(
    adentro: TestClient, sesion: Session, reloj, un_dia_en_el_lavadero, un_dia_en_el_parqueadero
):
    sesion.commit()
    reloj["ahora"] = LUNES_8AM + minutos(120)

    pagina = adentro.get("/inicio").text

    for nombre, valor in [
        ("En espera", "2"),
        ("En lavado", "1"),
        ("Por cobrar", "1"),
        ("Cobrado hoy", "$40.000"),
        ("Adentro", "1"),
        ("Salieron hoy", "1"),
        ("Recogido hoy", "$3.000"),
    ]:
        assert f'<dt>{nombre}</dt><dd class="numero">{valor}</dd>' in pagina, nombre
    # La caja del lavadero se abrió ayer y sigue abierta: en naranja, con el bot y lo pendiente.
    assert (
        "<strong>La caja está abierta desde ayer a las 6:00 a.m.</strong> Ciérrala y abre la "
        "de hoy. · Bot activo · <strong>1 comentario por atender</strong>"
    ) in pagina
    assert "Caja abierta hoy a las 6:00 a.m." in pagina


def test_la_caja_cerrada(adentro: TestClient):
    pagina = adentro.get("/inicio").text

    assert "Caja cerrada" in pagina
    assert "Bot activo</span>" in pagina  # sin cobros ni comentarios, solo el bot


def test_la_caja_que_quedo_abierta_de_otro_dia(adentro: TestClient, sesion: Session):
    parqueo.abrir_caja(sesion, base=0, ahora=LUNES_6AM - AYER)
    sesion.commit()

    pagina = adentro.get("/inicio").text

    assert 'class="negocio__estado negocio__estado--alerta"' in pagina
    assert "<strong>La caja está abierta desde ayer a las 6:00 a.m.</strong>" in pagina
    assert "Ciérrala y abre la de hoy." in pagina


# --- Cada negocio con su menú --------------------------------------------------------------


def test_de_que_negocio_es_cada_seccion():
    assert de_la_seccion("parqueadero/caja") == (PARQUEADERO, "caja")
    assert de_la_seccion("lavadero/fila") == (LAVADERO, "fila")
    assert de_la_seccion("inicio") == (None, "inicio")  # la de elegir no es de ninguno


@pytest.mark.parametrize("seccion", ["lavadero/", "otro/fila", "parqueadero/fila", "fila", ""])
def test_una_seccion_mal_escrita_se_avisa_de_una_vez(seccion: str):
    with pytest.raises(ValueError, match="Esa sección no existe"):
        de_la_seccion(seccion)


def test_el_lavadero_muestra_solo_lo_suyo(adentro: TestClient):
    pagina = adentro.get("/fila").text

    rutas = ["/fila", "/caja", "/clientes", "/comentarios", "/configuracion"]
    assert secciones(pagina, "navegacion") == secciones(pagina, "navegacion-celular") == rutas
    arriba = encabezado(pagina)
    assert "Nuevo turno" in arriba and "Bot activo" in arriba
    assert 'hx-get="/comentarios/contador"' in arriba
    assert '<a class="marca marca--negocio" href="/inicio">' in arriba
    assert "Lavadero" in parte(arriba, r'<span class="marca__nombre">.*?</span>')


def test_el_parqueadero_muestra_solo_lo_suyo(adentro: TestClient):
    pagina = adentro.get("/parqueadero").text

    rutas = [
        "/parqueadero",
        "/parqueadero/caja",
        "/parqueadero/mensualidades",
        "/parqueadero/balance",
        "/parqueadero/tarifas",
    ]
    assert secciones(pagina, "navegacion") == secciones(pagina, "navegacion-celular") == rutas
    arriba = encabezado(pagina)
    assert "Nuevo turno" not in arriba and "estado-bot" not in arriba
    assert "/comentarios/contador" not in pagina
    assert "contador-comentarios" not in pagina
    assert "Parqueadero" in parte(arriba, r'<span class="marca__nombre">.*?</span>')
    # Las partes del parqueadero ya no se repiten debajo del título: están en el menú.
    assert 'class="atajos"' not in pagina


@pytest.mark.parametrize(
    "ruta",
    [s.ruta for negocio in NEGOCIOS.values() for s in negocio.secciones],
)
def test_cada_seccion_se_marca_arriba_y_abajo(adentro: TestClient, ruta: str):
    respuesta = adentro.get(ruta)

    assert respuesta.status_code == 200
    assert respuesta.text.count(f'<a href="{ruta}" aria-current="page">') == 2


def test_una_caja_del_parqueadero_esta_en_la_seccion_caja(adentro: TestClient, sesion: Session):
    caja = parqueo.abrir_caja(sesion, base=0, ahora=LUNES_6AM)
    sesion.commit()

    pagina = adentro.get(f"/parqueadero/caja/{caja.id}").text

    assert pagina.count('<a href="/parqueadero/caja" aria-current="page">') == 2


@pytest.mark.parametrize("ruta", ["/configuracion/textos", "/mensajes"])
def test_lo_que_se_abre_desde_configuracion_esta_en_esa_seccion(adentro: TestClient, ruta: str):
    pagina = adentro.get(ruta).text

    assert pagina.count('<a href="/configuracion" aria-current="page">') == 2
