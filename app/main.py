"""Servidor web de Auto Ibaguer: el dashboard, /salud y el reloj. Aquí llegará el webhook
de WhatsApp.

Para correrlo: uv run fastapi dev app/main.py   y abre http://localhost:8000
"""

import logging
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import obtener_entorno
from app.db.sesion import obtener_sesion
from app.reloj import proceso as reloj
from app.web import clientes, comun, configuracion, en_vivo, entrar, fila, probar_bot


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
async def ciclo_de_vida(_: FastAPI) -> AsyncIterator[None]:
    """Lo que pasa al arrancar el servidor y al apagarlo."""
    en_vivo.cerrar_al_apagar()
    mostrar_registro_de_la_app()
    async with AsyncExitStack() as al_apagar:
        if obtener_entorno().reloj_activo:
            # Hito 5: aquí recibe el enviador de WhatsApp y los avisos empiezan a salir.
            await al_apagar.enter_async_context(reloj.encendido(reloj.del_servidor()))
        yield


app = FastAPI(title="Auto Ibaguer", version="0.1.0", lifespan=ciclo_de_vida)
app.mount(
    "/estaticos",
    StaticFiles(directory=Path(__file__).parent / "web" / "estaticos"),
    name="estaticos",
)
for modulo in (entrar, fila, clientes, configuracion, probar_bot):
    app.include_router(modulo.router)
app.add_exception_handler(comun.NecesitaEntrar, comun.responder_sin_sesion)

SesionBD = Annotated[Session, Depends(obtener_sesion)]


@app.get("/salud")
def salud(sesion: SesionBD) -> JSONResponse:
    """Dice si el servidor y la base de datos están funcionando.

    Lo usa la alerta de la nube (hito 8) para avisar si el sistema se cae.
    """
    try:
        sesion.execute(text("SELECT 1"))
    except SQLAlchemyError:
        return JSONResponse(
            status_code=503, content={"estado": "con problemas", "base_de_datos": "sin conexión"}
        )
    return JSONResponse(content={"estado": "ok", "base_de_datos": "ok"})
