"""Servidor web de Auto Ibaguer: el dashboard, el webhook de WhatsApp, /salud y el reloj.

Para correrlo: uv run fastapi dev app/main.py   y abre http://localhost:8000
"""

import logging
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import obtener_entorno
from app.db.sesion import obtener_sesion
from app.integraciones import whatsapp
from app.reloj import proceso as reloj
from app.web import (
    clientes,
    comun,
    configuracion,
    en_vivo,
    entrar,
    fila,
    mensajes,
    probar_bot,
    textos_del_bot,
    webhook,
)
from app.web.cabeceras import CabecerasDeSeguridad
from app.web.registro import proteger_el_registro_de_peticiones


def mostrar_registro_de_la_app() -> None:
    """Lo que anota la app (por ejemplo, lo que hace el reloj) sale en la terminal, con el
    mismo formato de los mensajes del servidor."""
    registro_de_la_app = logging.getLogger("app")
    del_servidor = logging.getLogger("uvicorn")
    if registro_de_la_app.handlers or not del_servidor.handlers:
        return  # ya estaba listo, o no hay servidor (por ejemplo, en las pruebas)
    for manejador in del_servidor.handlers:
        registro_de_la_app.addHandler(manejador)
    registro_de_la_app.setLevel(logging.INFO)
    registro_de_la_app.propagate = False


@asynccontextmanager
async def ciclo_de_vida(servidor: FastAPI) -> AsyncIterator[None]:
    """Lo que pasa al arrancar el servidor y al apagarlo."""
    en_vivo.cerrar_al_apagar()
    mostrar_registro_de_la_app()
    proteger_el_registro_de_peticiones()  # sin búsquedas ni secretos en los logs
    async with AsyncExitStack() as al_apagar:
        if obtener_entorno().reloj_activo:
            # Con WhatsApp conectado, el reloj también envía y reintenta los mensajes.
            el_reloj = reloj.del_servidor(whatsapp.del_entorno())
            await al_apagar.enter_async_context(reloj.encendido(el_reloj))
            servidor.state.reloj = el_reloj  # /salud revisa que siga dando vueltas
            al_apagar.callback(delattr, servidor.state, "reloj")
        yield


app = FastAPI(title="Auto Ibaguer", version="0.1.0", lifespan=ciclo_de_vida)
app.add_middleware(CabecerasDeSeguridad)
app.mount(
    "/estaticos",
    StaticFiles(directory=Path(__file__).parent / "web" / "estaticos"),
    name="estaticos",
)
for modulo in (
    entrar,
    fila,
    clientes,
    configuracion,
    textos_del_bot,
    mensajes,
    probar_bot,
    webhook,
):
    app.include_router(modulo.router)
app.add_exception_handler(comun.NecesitaEntrar, comun.responder_sin_sesion)

SesionBD = Annotated[Session, Depends(obtener_sesion)]


@app.get("/salud")
def salud(request: Request, sesion: SesionBD) -> JSONResponse:
    """Dice si el servidor, la base de datos y el reloj están funcionando.

    Lo revisan Render (si no responde bien, reinicia el servidor) y la alerta de caídas.
    """
    try:
        sesion.execute(text("SELECT 1"))
    except SQLAlchemyError:
        return JSONResponse(
            status_code=503, content={"estado": "con problemas", "base_de_datos": "sin conexión"}
        )
    contenido = {"estado": "ok", "base_de_datos": "ok"}
    el_reloj: reloj.Reloj | None = getattr(request.app.state, "reloj", None)
    if el_reloj is not None:
        if el_reloj.se_detuvo():
            contenido = {"estado": "con problemas", "base_de_datos": "ok", "reloj": "detenido"}
            return JSONResponse(status_code=503, content=contenido)
        contenido["reloj"] = "ok"
    return JSONResponse(content=contenido)
