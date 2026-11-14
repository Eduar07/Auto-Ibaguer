"""La pantalla de clientes: buscarlos, ver su ficha, corregir sus datos y borrarlos a pedido.

Como en la fila, aquí no hay reglas: se llaman las de app.clientes.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session

from app.clientes import consultas
from app.clientes import servicio as clientes
from app.clientes.errores import NoExiste
from app.errores import ReglaDelNegocio
from app.web.comun import Admin, SesionBD, con_eventos, html, pagina, verificar_origen

router = APIRouter(dependencies=[Depends(verificar_origen)])


def _lista(sesion: Session, q: str, numero_de_pagina: int) -> dict[str, Any]:
    return {"lista": consultas.buscar_clientes(sesion, q, pagina=numero_de_pagina), "q": q.strip()}


def _ficha(sesion: Session, cliente_id: int) -> consultas.FichaDelCliente:
    try:
        return consultas.ficha_del_cliente(sesion, cliente_id)
    except NoExiste as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get("/clientes", response_class=HTMLResponse)
def pagina_clientes(
    request: Request,
    sesion: SesionBD,
    _: Admin,
    q: str = "",
    numero: int = 1,
    cliente: int | None = None,
) -> Response:
    ficha = None
    if cliente is not None:
        try:
            ficha = consultas.ficha_del_cliente(sesion, cliente)
        except NoExiste:
            ficha = None
    contexto = {
        "total": consultas.contar_clientes(sesion),
        "ficha": ficha,
        **_lista(sesion, q, numero),
    }
    return pagina(request, sesion, "clientes.html", contexto, seccion="lavadero/clientes")


@router.get("/clientes/lista", response_class=HTMLResponse)
def lista_de_clientes(
    request: Request, sesion: SesionBD, _: Admin, q: str = "", numero: int = 1
) -> Response:
    return html(request, "_lista_clientes.html", _lista(sesion, q, numero))


@router.get("/clientes/{cliente_id}/ficha", response_class=HTMLResponse)
def ficha_del_cliente(request: Request, sesion: SesionBD, _: Admin, cliente_id: int) -> Response:
    return html(request, "_ficha_cliente.html", {"ficha": _ficha(sesion, cliente_id)})


def _ficha_actualizada(request: Request, sesion: Session, cliente_id: int, aviso: str) -> Response:
    """Tras guardar: la ficha nueva en su lugar, el diálogo cerrado y la lista al día."""
    respuesta = html(request, "_ficha_cliente.html", {"ficha": _ficha(sesion, cliente_id)})
    respuesta.headers["HX-Retarget"] = "#ficha-cliente"
    respuesta.headers["HX-Reswap"] = "innerHTML"
    return con_eventos(
        respuesta, cerrar_dialogo=True, aviso=aviso, despues={"lista-cambiada": True}
    )


# --- Editar datos -------------------------------------------------------------------------


@router.get("/clientes/{cliente_id}/editar", response_class=HTMLResponse)
def dialogo_editar(request: Request, sesion: SesionBD, _: Admin, cliente_id: int) -> Response:
    ficha = _ficha(sesion, cliente_id)
    formulario = {"nombre": ficha.nombre, "celular": ficha.telefono or ""}
    return html(request, "dialogos/editar_cliente.html", {"ficha": ficha, "formulario": formulario})


@router.post("/clientes/{cliente_id}/editar", response_class=HTMLResponse)
def editar(
    request: Request,
    sesion: SesionBD,
    _: Admin,
    cliente_id: int,
    nombre: Annotated[str, Form()] = "",
    celular: Annotated[str, Form()] = "",
) -> Response:
    try:
        cliente = clientes.editar_cliente(sesion, cliente_id, nombre=nombre, telefono=celular)
        guardado = cliente.nombre
        sesion.commit()
    except ReglaDelNegocio as error:
        sesion.rollback()
        contexto = {
            "ficha": _ficha(sesion, cliente_id),
            "formulario": {"nombre": nombre, "celular": celular},
            "error": str(error),
        }
        return html(request, "dialogos/editar_cliente.html", contexto)
    return _ficha_actualizada(
        request, sesion, cliente_id, f"Datos de {guardado.rstrip('.')} guardados."
    )


# --- Borrar datos (Ley 1581) ----------------------------------------------------------------


@router.get("/clientes/{cliente_id}/borrar", response_class=HTMLResponse)
def dialogo_borrar(request: Request, sesion: SesionBD, _: Admin, cliente_id: int) -> Response:
    return html(request, "dialogos/borrar_cliente.html", {"ficha": _ficha(sesion, cliente_id)})


@router.post("/clientes/{cliente_id}/borrar", response_class=HTMLResponse)
def borrar(request: Request, sesion: SesionBD, _: Admin, cliente_id: int) -> Response:
    try:
        nombre = clientes.borrar_datos(sesion, cliente_id)
        sesion.commit()
    except ReglaDelNegocio as error:
        sesion.rollback()
        contexto = {"ficha": _ficha(sesion, cliente_id), "error": str(error)}
        return html(request, "dialogos/borrar_cliente.html", contexto)
    respuesta = html(request, "_sin_ficha.html", {"borrado": nombre})
    respuesta.headers["HX-Retarget"] = "#ficha-cliente"
    respuesta.headers["HX-Reswap"] = "innerHTML"
    return con_eventos(
        respuesta,
        cerrar_dialogo=True,
        aviso=f"Se borraron los datos de {nombre.rstrip('.')}.",
        despues={"lista-cambiada": True},
    )
