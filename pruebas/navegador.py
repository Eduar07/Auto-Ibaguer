"""Atajos para las pruebas que usan el dashboard como lo haría el navegador."""

import json

from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy.orm import Session

from app.web import sesiones

CLAVE = "clave-larga-del-lavadero"
HTMX = {"HX-Request": "true"}


def poner_cookie(web: TestClient, valor: str) -> None:
    """Pone a mano la cookie de sesión, como si el navegador la tuviera guardada."""
    web.cookies.clear()
    web.cookies.set(sesiones.NOMBRE_COOKIE, valor, domain="testserver.local")


def guardar(sesion: Session) -> None:
    """Confirma lo preparado, como si ya estuviera guardado de antes.

    Cuando una regla no se cumple, el dashboard deshace solo esa acción (rollback). En la
    vida real lo anterior ya estaba guardado; en la prueba hay que confirmarlo primero.
    Igual se borra al final: la prueba entera corre dentro de una transacción.
    """
    sesion.commit()


def eventos(respuesta: Response, encabezado: str = "HX-Trigger") -> dict:
    """Lo que el servidor le pide a la página después de una acción (HX-Trigger)."""
    return json.loads(respuesta.headers.get(encabezado, "{}"))


def aviso(respuesta: Response) -> str:
    return eventos(respuesta).get("aviso", "")


def error(respuesta: Response) -> str:
    return eventos(respuesta).get("error", "")
