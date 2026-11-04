"""La pantalla Comentarios: las quejas, las felicitaciones, quién pidió hablar con una
persona y las preguntas que el bot no supo responder, como llegan por WhatsApp. El
administrador los lee y los marca como atendidos.

El número que sale junto a "Comentarios" en el menú se vuelve a pedir solo cada minuto, y
también apenas el administrador marca o borra uno.
"""

from collections.abc import Callable

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session

from app.comentarios import consultas
from app.comentarios import servicio as comentarios
from app.comentarios.servicio import ComentarioNoExiste
from app.web.comun import (
    Admin,
    NecesitaEntrar,
    SesionBD,
    administrador_de_la_cookie,
    con_eventos,
    html,
    pagina,
    verificar_origen,
)

router = APIRouter(dependencies=[Depends(verificar_origen)])

# ?ver=... -> si se muestran solo los que faltan por atender
VISTAS = {"pendientes": True, "todos": False}


@router.get("/comentarios", response_class=HTMLResponse)
def pagina_comentarios(
    request: Request, sesion: SesionBD, _: Admin, ver: str = "pendientes"
) -> Response:
    ver = ver if ver in VISTAS else "pendientes"
    contexto = {"ver": ver, "comentarios": consultas.lista(sesion, solo_por_atender=VISTAS[ver])}
    return pagina(request, sesion, "comentarios.html", contexto, seccion="comentarios")


@router.get("/comentarios/contador", response_class=HTMLResponse)
def contador(request: Request, sesion: SesionBD) -> Response:
    """El número del menú. Como se pide solo, revisa la sesión sin renovarla: una pantalla
    abierta y sin usar no deja la sesión abierta para siempre."""
    if administrador_de_la_cookie(request, sesion) is None:
        raise NecesitaEntrar()
    contexto = {"comentarios_pendientes": consultas.por_atender(sesion), "solo_contadores": True}
    return html(request, "_contador_comentarios.html", contexto)


def _el_comentario(sesion: Session, comentario_id: int) -> consultas.ComentarioEnLista:
    try:
        return consultas.uno(sesion, comentario_id)
    except ComentarioNoExiste as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


def _cambiar(
    request: Request, sesion: Session, comentario_id: int, hacer: Callable[[], object], aviso: str
) -> Response:
    """Marca el comentario (atendido o pendiente) y devuelve su tarjeta nueva."""
    try:
        hacer()
        sesion.commit()
    except ComentarioNoExiste as error:
        sesion.rollback()
        raise HTTPException(status_code=404, detail=str(error)) from error
    respuesta = html(request, "_comentario.html", {"c": _el_comentario(sesion, comentario_id)})
    return con_eventos(respuesta, aviso=aviso, comentarios_cambiados=True)


@router.post("/comentarios/{comentario_id}/atendido", response_class=HTMLResponse)
def marcar_atendido(
    request: Request, sesion: SesionBD, administrador: Admin, comentario_id: int
) -> Response:
    return _cambiar(
        request,
        sesion,
        comentario_id,
        lambda: comentarios.marcar_atendido(sesion, comentario_id, administrador.id),
        "Listo, quedó como atendido.",
    )


@router.post("/comentarios/{comentario_id}/pendiente", response_class=HTMLResponse)
def volver_a_pendiente(
    request: Request, sesion: SesionBD, _: Admin, comentario_id: int
) -> Response:
    return _cambiar(
        request,
        sesion,
        comentario_id,
        lambda: comentarios.volver_a_pendiente(sesion, comentario_id),
        "Volvió a quedar por atender.",
    )


@router.get("/comentarios/{comentario_id}/borrar", response_class=HTMLResponse)
def dialogo_borrar(request: Request, sesion: SesionBD, _: Admin, comentario_id: int) -> Response:
    contexto = {"c": _el_comentario(sesion, comentario_id)}
    return html(request, "dialogos/borrar_comentario.html", contexto)


@router.post("/comentarios/{comentario_id}/borrar", response_class=HTMLResponse)
def borrar(request: Request, sesion: SesionBD, _: Admin, comentario_id: int) -> Response:
    try:
        comentarios.borrar(sesion, comentario_id)
        sesion.commit()
    except ComentarioNoExiste as error:
        sesion.rollback()
        raise HTTPException(status_code=404, detail=str(error)) from error
    # La tarjeta sale de la lista y el diálogo se cierra.
    respuesta = Response(
        status_code=200,
        headers={"HX-Retarget": f"#comentario-{comentario_id}", "HX-Reswap": "delete"},
    )
    return con_eventos(
        respuesta, cerrar_dialogo=True, aviso="Comentario borrado.", comentarios_cambiados=True
    )
