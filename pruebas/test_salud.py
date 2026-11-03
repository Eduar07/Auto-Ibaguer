"""El servidor responde en /salud y avisa si pierde la base de datos o el reloj se detiene."""

import time
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.db.sesion import obtener_sesion
from app.main import app
from app.reloj import proceso


@pytest.fixture
def cliente_web() -> Iterator[TestClient]:
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_salud_con_la_base_de_datos_funcionando(cliente_web: TestClient, sesion: Session):
    app.dependency_overrides[obtener_sesion] = lambda: sesion

    respuesta = cliente_web.get("/salud")

    assert respuesta.status_code == 200
    assert respuesta.json() == {"estado": "ok", "base_de_datos": "ok"}


def test_salud_sin_base_de_datos(cliente_web: TestClient):
    class SesionSinConexion:
        def execute(self, *_args, **_kwargs):
            raise OperationalError("SELECT 1", {}, Exception("no hay conexión"))

    app.dependency_overrides[obtener_sesion] = lambda: SesionSinConexion()

    respuesta = cliente_web.get("/salud")

    assert respuesta.status_code == 503
    assert respuesta.json()["base_de_datos"] == "sin conexión"


# --- El reloj, también revisado en /salud -----------------------------------------------------


@pytest.fixture
def con_reloj(monkeypatch: pytest.MonkeyPatch) -> Iterator[proceso.Reloj]:
    """Como en el servidor de verdad: el reloj encendido queda en app.state."""
    el_reloj = proceso.Reloj([], abrir=lambda: None)
    monkeypatch.setattr(app.state, "reloj", el_reloj, raising=False)
    yield el_reloj


def test_salud_dice_que_el_reloj_da_vueltas(cliente_web, sesion: Session, con_reloj):
    app.dependency_overrides[obtener_sesion] = lambda: sesion
    con_reloj.una_vuelta()

    respuesta = cliente_web.get("/salud")

    assert respuesta.status_code == 200
    assert respuesta.json() == {"estado": "ok", "base_de_datos": "ok", "reloj": "ok"}


def test_salud_avisa_si_el_reloj_se_detuvo(cliente_web, sesion: Session, con_reloj):
    app.dependency_overrides[obtener_sesion] = lambda: sesion
    con_reloj.empezo_ultima_vuelta = time.monotonic() - proceso.SE_DETUVO_SI_PASAN - 1

    respuesta = cliente_web.get("/salud")

    assert respuesta.status_code == 503  # Render reinicia el servidor y llega la alerta
    assert respuesta.json()["reloj"] == "detenido"


def test_un_reloj_recien_encendido_no_esta_detenido():
    el_reloj = proceso.Reloj([], abrir=lambda: None)

    assert not el_reloj.se_detuvo()
    assert el_reloj.se_detuvo(time.monotonic() + proceso.SE_DETUVO_SI_PASAN + 1)
