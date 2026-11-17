"""Usuarios: quién entra al dashboard, con qué rol (administrador o cajero) y a qué negocio.
Solo la abre un administrador (app.web.permisos)."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.caja.estados import NOMBRE_DEL_NEGOCIO, Negocio
from app.errores import ReglaDelNegocio
from app.web import administradores
from app.web.comun import Admin, SesionBD, con_eventos, html, pagina, verificar_origen
from app.web.tablas import NOMBRE_DEL_ROL, Administrador, Rol

router = APIRouter(dependencies=[Depends(verificar_origen)])

ROLES = [
    (Rol.ADMINISTRADOR.value, "Administrador", "Todo, en los dos negocios"),
    (Rol.CAJERO.value, "Cajero", "Opera y cobra; no cambia precios ni corrige"),
]
NEGOCIOS = [("", "Los dos")] + [(n.value, NOMBRE_DEL_NEGOCIO[n]) for n in Negocio]


def _contexto(sesion: Session, quien: Administrador) -> dict[str, Any]:
    return {
        "usuarios": list(sesion.scalars(select(Administrador).order_by(Administrador.usuario))),
        "yo": quien.id,
        "nombre_del_rol": NOMBRE_DEL_ROL,
        "nombre_del_negocio": NOMBRE_DEL_NEGOCIO,
    }


def _dialogo(
    request: Request, usuario: Administrador | None, formulario: dict, error: str | None = None
) -> Response:
    contexto = {"u": usuario, "f": formulario, "roles": ROLES, "negocios": NEGOCIOS, "error": error}
    return html(request, "dialogos/usuario.html", contexto)


def _lista(request: Request, sesion: Session, quien: Administrador, aviso: str) -> Response:
    respuesta = html(request, "_usuarios.html", _contexto(sesion, quien))
    respuesta.headers["HX-Retarget"] = "#usuarios"
    respuesta.headers["HX-Reswap"] = "outerHTML"
    return con_eventos(respuesta, cerrar_dialogo=True, aviso=aviso)


@router.get("/usuarios", response_class=HTMLResponse)
def pagina_usuarios(request: Request, sesion: SesionBD, quien: Admin) -> Response:
    return pagina(request, sesion, "usuarios.html", _contexto(sesion, quien), seccion="usuarios")


@router.get("/usuarios/nuevo", response_class=HTMLResponse)
def nuevo(request: Request, _: Admin) -> Response:
    return _dialogo(request, None, {"rol": Rol.CAJERO.value, "activo": True})


@router.post("/usuarios", response_class=HTMLResponse)
def crear(
    request: Request,
    sesion: SesionBD,
    quien: Admin,
    usuario: Annotated[str, Form()] = "",
    clave: Annotated[str, Form()] = "",
    rol: Annotated[str, Form()] = "",
    negocio: Annotated[str, Form()] = "",
) -> Response:
    formulario = {"usuario": usuario, "rol": rol, "negocio": negocio, "activo": True}
    try:
        nuevo = administradores.crear_usuario(sesion, usuario, clave, rol, negocio or None)
        sesion.commit()
    except ReglaDelNegocio as error:
        sesion.rollback()
        return _dialogo(request, None, formulario, str(error))
    return _lista(request, sesion, quien, f"Usuario {nuevo.usuario} creado. Ya puede entrar.")


def _usuario_o_404(sesion: Session, usuario_id: int) -> Administrador:
    usuario = sesion.get(Administrador, usuario_id)
    if usuario is None:
        raise HTTPException(status_code=404, detail="Ese usuario no existe.")
    return usuario


@router.get("/usuarios/{usuario_id}", response_class=HTMLResponse)
def editar(request: Request, sesion: SesionBD, _: Admin, usuario_id: int) -> Response:
    u = _usuario_o_404(sesion, usuario_id)
    formulario = {
        "rol": u.rol.value,
        "negocio": u.negocio.value if u.negocio else "",
        "activo": u.activo,
    }
    return _dialogo(request, u, formulario)


@router.post("/usuarios/{usuario_id}", response_class=HTMLResponse)
def cambiar(
    request: Request,
    sesion: SesionBD,
    quien: Admin,
    usuario_id: int,
    rol: Annotated[str, Form()] = "",
    negocio: Annotated[str, Form()] = "",
    activo: Annotated[str, Form()] = "",
    clave: Annotated[str, Form()] = "",
) -> Response:
    u = _usuario_o_404(sesion, usuario_id)
    formulario = {"rol": rol, "negocio": negocio, "activo": activo == "si"}
    try:
        administradores.cambiar_usuario(
            sesion,
            quien,
            usuario_id,
            rol=rol,
            negocio=negocio or None,
            activo=activo == "si",
            clave=clave,
        )
        sesion.commit()
    except ReglaDelNegocio as error:
        sesion.rollback()
        return _dialogo(request, _usuario_o_404(sesion, usuario_id), formulario, str(error))
    return _lista(request, sesion, quien, f"Usuario {u.usuario} actualizado.")
