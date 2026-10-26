"""El servidor responde en /salud y avisa si pierde la base de datos."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.db.sesion import obtener_sesion
from app.main import app


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
