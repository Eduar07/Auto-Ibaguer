"""Lo que protege al servidor en la nube: las cabeceras de seguridad de cada respuesta y un
registro de peticiones sin datos de los clientes."""

import logging
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from uvicorn.config import LOGGING_CONFIG
from uvicorn.logging import AccessFormatter

from app.config import obtener_entorno
from app.db.sesion import obtener_sesion
from app.main import app
from app.web.registro import SinParametrosEnElRegistro, proteger_el_registro_de_peticiones


@pytest.fixture
def cliente_web(sesion: Session) -> Iterator[TestClient]:
    app.dependency_overrides[obtener_sesion] = lambda: sesion
    yield TestClient(app)
    app.dependency_overrides.clear()


# --- Cabeceras de seguridad --------------------------------------------------------------------


def test_cada_respuesta_trae_las_cabeceras_de_seguridad(cliente_web: TestClient):
    encabezados = cliente_web.get("/salud").headers

    assert encabezados["x-content-type-options"] == "nosniff"
    assert encabezados["x-frame-options"] == "DENY"
    assert encabezados["content-security-policy"] == "frame-ancestors 'none'"
    assert encabezados["referrer-policy"] == "same-origin"
    assert "strict-transport-security" not in encabezados  # en tu computador no hay HTTPS


def test_en_la_nube_el_navegador_siempre_usa_https(
    cliente_web: TestClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(obtener_entorno(), "entorno", "produccion")

    encabezados = cliente_web.get("/entrar").headers

    assert encabezados["strict-transport-security"] == "max-age=31536000"


# --- El registro de peticiones -----------------------------------------------------------------


def linea_del_registro(direccion: str) -> str:
    """La línea que uvicorn escribe en el registro cuando alguien pide esa dirección."""
    registro = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        0,
        '%s - "%s %s HTTP/%s" %d',
        ("190.1.2.3:51234", "GET", direccion, "1.1", 200),
        None,
    )
    assert SinParametrosEnElRegistro().filter(registro)  # la línea se anota igual
    formato = AccessFormatter(LOGGING_CONFIG["formatters"]["access"]["fmt"], use_colors=False)
    return formato.format(registro)


def test_el_registro_no_guarda_lo_que_se_busca_en_el_dashboard():
    linea = linea_del_registro("/clientes/lista?q=3001234567&numero=1")

    assert '"GET /clientes/lista HTTP/1.1" 200' in linea
    assert "3001234567" not in linea


def test_el_registro_no_guarda_la_palabra_secreta_del_webhook():
    linea = linea_del_registro(
        "/webhook?hub.mode=subscribe&hub.verify_token=palabra-secreta&hub.challenge=1234"
    )

    assert '"GET /webhook HTTP/1.1" 200' in linea
    assert "palabra-secreta" not in linea


def test_una_direccion_sin_parametros_queda_igual():
    assert '"GET /fila HTTP/1.1" 200' in linea_del_registro("/fila")


def test_al_arrancar_el_servidor_el_registro_queda_protegido(monkeypatch: pytest.MonkeyPatch):
    peticiones = logging.getLogger("uvicorn.access")
    monkeypatch.setattr(peticiones, "filters", [])
    monkeypatch.setattr(obtener_entorno(), "reloj_activo", False)

    with TestClient(app):
        pass
    proteger_el_registro_de_peticiones()  # otra vez, como en una recarga: no se repite

    assert [type(filtro) for filtro in peticiones.filters] == [SinParametrosEnElRegistro]
