"""La pantalla Comentarios: las quejas, las felicitaciones, quién pidió hablar con una
persona y las preguntas que el bot no supo responder, como llegan por WhatsApp. El
administrador los lee, le responde al cliente por WhatsApp y los marca como atendidos.

El número que sale junto a "Comentarios" en el menú se vuelve a pedir solo cada minuto, y
también apenas el administrador marca o borra uno. La conversación con el cliente (en el
diálogo de responder) también se vuelve a pedir sola, cada 10 segundos, mientras el
diálogo está abierto.
"""

import hashlib
from collections.abc import Callable
from datetime import date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session

from app import tiempo
from app.bot import pausa
from app.catalogo.parametros import leer_parametros
from app.comentarios import consultas, respuestas
from app.comentarios import servicio as comentarios
from app.comentarios.respuestas import Linea, NoSePuedeResponder
from app.comentarios.servicio import ComentarioNoExiste
from app.config import obtener_entorno
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
from app.web.tablas import Administrador

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


def _sin_renovar_la_sesion(request: Request, sesion: Session) -> None:
    """Lo que la página pide sola revisa la sesión sin renovarla: una pantalla abierta y sin
    usar no deja la sesión abierta para siempre."""
    if administrador_de_la_cookie(request, sesion) is None:
        raise NecesitaEntrar()


@router.get("/comentarios/contador", response_class=HTMLResponse)
def contador(request: Request, sesion: SesionBD) -> Response:
    """El número del menú."""
    _sin_renovar_la_sesion(request, sesion)
    contexto = {"comentarios_pendientes": consultas.por_atender(sesion), "solo_contadores": True}
    return html(request, "_contador_comentarios.html", contexto)


def _el_comentario(
    sesion: Session, comentario_id: int, ahora: datetime | None = None
) -> consultas.ComentarioEnLista:
    try:
        return consultas.uno(sesion, comentario_id, ahora=ahora)
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


# --- Responderle por WhatsApp (hito 7b, parte 3) ---------------------------------------------


def _con_celular(
    sesion: Session, comentario_id: int, ahora: datetime
) -> consultas.ComentarioEnLista:
    c = _el_comentario(sesion, comentario_id, ahora)
    if not c.telefono:
        raise HTTPException(
            status_code=404, detail="Este comentario no tiene celular: no hay a quién responderle."
        )
    return c


def _por_dia(lineas: list[Linea]) -> list[tuple[date, list[Linea]]]:
    """Los mensajes agrupados por día (en Bogotá), como en WhatsApp."""
    dias: list[tuple[date, list[Linea]]] = []
    for linea in lineas:
        dia = tiempo.hora_local(linea.cuando).date()
        if not dias or dias[-1][0] != dia:
            dias.append((dia, []))
        dias[-1][1].append(linea)
    return dias


def _charla(sesion: Session, c: consultas.ComentarioEnLista, ahora: datetime) -> dict[str, Any]:
    """La conversación y en qué va: lo que se muestra en el diálogo y se vuelve a pedir."""
    telefono = c.telefono or ""
    lineas = respuestas.charla_con(sesion, telefono, leer_parametros(sesion))
    hasta = respuestas.hasta_cuando_se_puede(sesion, telefono, ahora)
    # Si nada de esto cambió, la conversación que se ve está al día (ver `charla`).
    huella = hashlib.sha256(
        repr((lineas, hasta, c.pausa_hasta, c.atendido_en)).encode()
    ).hexdigest()[:16]
    return {"c": c, "dias": _por_dia(lineas), "hasta": hasta, "huella": huella}


def _dialogo(
    request: Request,
    sesion: Session,
    c: consultas.ComentarioEnLista,
    administrador: Administrador,
    ahora: datetime,
    *,
    texto: str | None = None,
    error: str | None = None,
    con_tarjeta: bool = False,
) -> Response:
    contexto = _charla(sesion, c, ahora)
    if texto is None:
        # El primer mensaje empieza diciendo quién le escribe; los siguientes, no.
        ya_le_escribe = c.pausa_hasta is not None
        texto = (
            ""
            if ya_le_escribe or contexto["hasta"] is None
            else respuestas.saludo_sugerido(
                c.cliente, administrador.usuario, leer_parametros(sesion).nombre_negocio
            )
        )
    contexto |= {
        "texto": texto,
        "error": error,
        "largo_maximo": respuestas.LARGO_MAXIMO,
        "whatsapp_conectado": obtener_entorno().whatsapp_conectado,
        # La tarjeta del comentario, detrás del diálogo, también se actualiza.
        "con_tarjeta": con_tarjeta,
    }
    return html(request, "dialogos/responder_comentario.html", contexto)


@router.get("/comentarios/{comentario_id}/responder", response_class=HTMLResponse)
def dialogo_responder(
    request: Request, sesion: SesionBD, administrador: Admin, comentario_id: int
) -> Response:
    ahora = tiempo.ahora()
    c = _con_celular(sesion, comentario_id, ahora)
    return _dialogo(request, sesion, c, administrador, ahora)


@router.post("/comentarios/{comentario_id}/responder", response_class=HTMLResponse)
def responder(
    request: Request,
    sesion: SesionBD,
    administrador: Admin,
    comentario_id: int,
    texto: Annotated[str, Form()] = "",
) -> Response:
    """Le deja el mensaje en la bandeja de salida y pone al bot en pausa con ese cliente. El
    diálogo sigue abierto, con el mensaje en la conversación: así se ve cuando sale."""
    ahora = tiempo.ahora()
    try:
        respuestas.responder(
            sesion,
            comentario_id,
            texto,
            administrador_id=administrador.id,
            usuario=administrador.usuario,
            ahora=ahora,
        )
        sesion.commit()
    except ComentarioNoExiste as error:
        sesion.rollback()
        raise HTTPException(status_code=404, detail=str(error)) from error
    except NoSePuedeResponder as error:
        sesion.rollback()
        c = _con_celular(sesion, comentario_id, ahora)
        return _dialogo(request, sesion, c, administrador, ahora, texto=texto, error=str(error))
    c = _el_comentario(sesion, comentario_id, ahora)
    respuesta = _dialogo(request, sesion, c, administrador, ahora, texto="", con_tarjeta=True)
    return con_eventos(respuesta, comentarios_cambiados=True)


@router.post("/comentarios/{comentario_id}/devolver-al-bot", response_class=HTMLResponse)
def devolver_al_bot(request: Request, sesion: SesionBD, _: Admin, comentario_id: int) -> Response:
    """Terminó de hablar con el cliente: el bot vuelve a contestarle desde ya."""
    ahora = tiempo.ahora()
    c = _con_celular(sesion, comentario_id, ahora)
    pausa.devolver_al_bot(sesion, c.telefono or "")
    sesion.commit()
    contexto = {"c": _el_comentario(sesion, comentario_id, ahora), "devuelto": True}
    return html(request, "_pausa_del_bot.html", contexto)


@router.get("/comentarios/{comentario_id}/charla", response_class=HTMLResponse)
def charla(request: Request, sesion: SesionBD, comentario_id: int, huella: str = "") -> Response:
    """La conversación, que el diálogo pide sola. Si no cambió nada desde la que tiene (la
    misma huella), no se manda otra vez: 204, sin contenido."""
    _sin_renovar_la_sesion(request, sesion)
    ahora = tiempo.ahora()
    contexto = _charla(sesion, _con_celular(sesion, comentario_id, ahora), ahora)
    if contexto["huella"] == huella:
        return Response(status_code=204)
    # Algo cambió: puede que el cliente haya escrito (el comentario vuelve a "por atender")
    # o que la pausa haya pasado. Llegan también la pausa y la tarjeta, al día.
    respuesta = html(request, "_charla_con_el_cliente.html", contexto | {"al_dia": True})
    return con_eventos(respuesta, comentarios_cambiados=True)
