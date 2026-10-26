"""Servidor web de Auto Ibaguer.

Por ahora solo tiene /salud. Aquí llegarán el webhook de WhatsApp y el dashboard.
Para correrlo: uv run fastapi dev app/main.py
"""

from typing import Annotated

from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.sesion import obtener_sesion

app = FastAPI(title="Auto Ibaguer", version="0.1.0")

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
