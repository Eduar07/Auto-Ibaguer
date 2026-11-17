"""La política de privacidad: la página pública que piden Meta (para publicar la app de
WhatsApp) y la Ley 1581 de 2012. No hace falta entrar al dashboard para verla.

Los datos del negocio (nombre, NIT, dirección, teléfono) salen de la configuración: lo
que el administrador puso en Configuración, nada escrito aquí.
"""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response

from app.catalogo.parametros import leer_parametros
from app.formato import telefono
from app.turnos.recibo import leer_ajustes
from app.web.comun import SesionBD, html

router = APIRouter()


@router.get("/privacidad", response_class=HTMLResponse)
def politica_de_privacidad(request: Request, sesion: SesionBD) -> Response:
    parametros = leer_parametros(sesion)
    recibo = leer_ajustes(sesion)
    contexto = {
        "negocio": parametros.nombre_negocio,
        "telefono": telefono(parametros.telefono_contacto),
        "nit": recibo.nit,
        "direccion": recibo.direccion,
    }
    return html(request, "privacidad.html", contexto)
