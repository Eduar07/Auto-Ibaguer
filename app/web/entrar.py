"""Entrar al dashboard y salir.

La contraseña se verifica con Argon2 y la sesión queda en una cookie firmada
(app.web.sesiones). Quien falla 5 veces seguidas queda bloqueado 15 minutos. Al entrar se
llega a /inicio, donde se escoge el negocio: el lavadero o el parqueadero.
"""

import time
from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import select

from app.web import sesiones
from app.web.claves import cifrar_clave, verificar_clave
from app.web.comun import (
    SesionBD,
    administrador_de_la_cookie,
    html,
    poner_cookie,
    valor_de_cookie,
    verificar_origen,
)
from app.web.tablas import Administrador

router = APIRouter(dependencies=[Depends(verificar_origen)])

LIMITE_DE_INTENTOS = sesiones.LimiteDeIntentos()


@lru_cache
def _clave_cifrada_de_relleno() -> str:
    """Si el usuario no existe, se verifica contra esta para tardar lo mismo que con uno
    que sí existe: así nadie puede averiguar qué usuarios hay midiendo el tiempo."""
    return cifrar_clave("usuario-que-no-existe")


# --- Entrar y salir --------------------------------------------------------------------


@router.get("/", include_in_schema=False)
def raiz() -> RedirectResponse:
    return RedirectResponse("/inicio", status_code=303)


@router.get("/entrar", response_class=HTMLResponse)
def pagina_entrar(request: Request, sesion: SesionBD) -> Response:
    if administrador_de_la_cookie(request, sesion) is not None:
        return RedirectResponse("/inicio", status_code=303)
    return html(request, "entrar.html")


@router.post("/entrar", response_class=HTMLResponse)
def entrar(
    request: Request,
    sesion: SesionBD,
    usuario: Annotated[str, Form()] = "",
    clave: Annotated[str, Form()] = "",
) -> Response:
    usuario = usuario.strip().lower()
    ahora = time.time()
    if LIMITE_DE_INTENTOS.bloqueado(usuario, ahora):
        return html(
            request,
            "entrar.html",
            {"usuario": usuario, "error": "Demasiados intentos fallidos. Espera 15 minutos."},
            status=429,
        )
    administrador = sesion.scalars(
        select(Administrador).where(Administrador.usuario == usuario)
    ).one_or_none()
    cifrada = administrador.clave_cifrada if administrador else _clave_cifrada_de_relleno()
    if not (verificar_clave(cifrada, clave) and administrador and administrador.activo):
        LIMITE_DE_INTENTOS.registrar_fallo(usuario, ahora)
        return html(
            request,
            "entrar.html",
            {"usuario": usuario, "error": "Usuario o contraseña incorrectos."},
            status=401,
        )
    LIMITE_DE_INTENTOS.registrar_exito(usuario)
    respuesta = RedirectResponse("/inicio", status_code=303)
    poner_cookie(respuesta, valor_de_cookie(administrador))
    return respuesta


@router.post("/salir")
def salir() -> RedirectResponse:
    respuesta = RedirectResponse("/entrar", status_code=303)
    respuesta.delete_cookie(sesiones.NOMBRE_COOKIE, path="/")
    return respuesta
