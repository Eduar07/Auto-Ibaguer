"""La política de privacidad: pública (Meta la pide para publicar la app de WhatsApp) y con
los datos del negocio que puso el administrador."""

import dataclasses

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.turnos.recibo import guardar_ajustes, leer_ajustes


def test_se_ve_sin_entrar_al_dashboard(web: TestClient):
    respuesta = web.get("/privacidad")

    assert respuesta.status_code == 200
    assert "Política de privacidad y tratamiento de datos personales" in respuesta.text
    assert "Ley 1581 de 2012" in respuesta.text
    assert 'id="borrar"' in respuesta.text  # las instrucciones para borrar los datos


def test_lleva_los_datos_del_negocio_de_la_configuracion(web: TestClient, sesion: Session):
    guardar_ajustes(sesion, dataclasses.replace(leer_ajustes(sesion), nit="900123456-7"))
    sesion.flush()

    assert "NIT 900123456-7" in web.get("/privacidad").text


def test_la_pantalla_de_entrar_la_enlaza(web: TestClient):
    assert 'href="/privacidad"' in web.get("/entrar").text
