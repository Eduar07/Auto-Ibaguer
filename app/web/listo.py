"""Lista para abrir: lo que falta configurar antes de trabajar en serio (app.listo)."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response

from app import listo
from app.web.comun import Admin, SesionBD, pagina, verificar_origen

router = APIRouter(dependencies=[Depends(verificar_origen)])


@router.get("/listo", response_class=HTMLResponse)
def pagina_listo(request: Request, sesion: SesionBD, _: Admin) -> Response:
    grupos = listo.revisar(sesion)
    contexto = {"grupos": grupos, "pendientes": listo.pendientes(grupos)}
    return pagina(request, sesion, "listo.html", contexto, seccion="listo")
