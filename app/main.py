"""Servidor web de Auto Ibaguer: el dashboard y /salud. Aquí llegará el webhook de WhatsApp.

Para correrlo: uv run fastapi dev app/main.py   y abre http://localhost:8000
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.sesion import obtener_sesion
from app.web import dashboard, en_vivo


@asynccontextmanager
async def ciclo_de_vida(_: FastAPI) -> AsyncIterator[None]:
    """Lo que pasa al arrancar el servidor y al apagarlo."""
    en_vivo.cerrar_al_apagar()
    yield


app = FastAPI(title="Auto Ibaguer", version="0.1.0", lifespan=ciclo_de_vida)
app.mount(
    "/estaticos",
    StaticFiles(directory=Path(__file__).parent / "web" / "estaticos"),
    name="estaticos",
)
app.include_router(dashboard.router)
app.add_exception_handler(dashboard.NecesitaEntrar, dashboard.responder_sin_sesion)

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
