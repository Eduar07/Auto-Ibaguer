"""La pantalla Textos del bot (Configuración > Textos del bot): lo que el bot les dice a los
clientes, para cambiarlo sin programar.

Cada texto se guarda por separado y el bot lo usa desde el siguiente mensaje. Mientras se
escribe, la vista previa muestra cómo le llega al cliente, con datos de ejemplo. Las
reglas (qué datos lleva cada texto, cuáles no se pueden quitar, cuánto puede medir) viven
en app.bot.textos_editables.
"""

from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, Response

from app.bot import textos_editables
from app.bot.mensajes import Mensaje
from app.bot.textos_editables import DATOS, EDITABLES, GRUPOS, Editable, Grupo, TextoInvalido
from app.catalogo.parametros import Parametros, leer_parametros
from app.web.comun import Admin, SesionBD, con_eventos, html, pagina, verificar_origen

router = APIRouter(dependencies=[Depends(verificar_origen)])

# Un tope para lo que manda el navegador: más que esto no es un texto del bot.
MAXIMO_ESCRITO = 4000


@dataclass(frozen=True)
class TextoEnPantalla:
    editable: Editable
    texto: str  # lo que muestra el campo
    cambiado: bool  # está guardado distinto del original
    vista: Mensaje | None  # cómo le llega al cliente, si el texto sirve
    error: str | None
    largo: int  # caracteres, como los cuenta la revisión
    abierto: bool = False
    roto: bool = False  # el texto guardado ya no sirve y el bot está usando el original


def _en_pantalla(
    editable: Editable,
    parametros: Parametros,
    *,
    escrito: str | None = None,
    abierto: bool = False,
) -> TextoEnPantalla:
    guardado = parametros.textos_bot.get(editable.clave)
    texto = escrito if escrito is not None else (guardado or editable.original)
    vista, error = None, None
    try:
        vista = textos_editables.vista_previa(editable, texto, parametros)
    except TextoInvalido as problema:
        error = str(problema)
    roto = escrito is None and guardado is not None and error is not None
    return TextoEnPantalla(
        editable=editable,
        texto=texto,
        cambiado=guardado is not None,
        vista=vista,
        error=error,
        largo=len(textos_editables.limpiar(texto)),
        abierto=abierto or roto,
        roto=roto,
    )


def _editable(clave: str) -> Editable:
    editable = EDITABLES.get(clave)
    if editable is None:
        raise HTTPException(status_code=404, detail="Ese texto del bot no existe.")
    return editable


def _contexto(t: TextoEnPantalla) -> dict:
    return {"t": t, "DATOS": DATOS}


@dataclass(frozen=True)
class GrupoEnPantalla:
    grupo: Grupo
    textos: list[TextoEnPantalla]

    @property
    def cambiados(self) -> int:
        return sum(1 for t in self.textos if t.cambiado)


@router.get("/configuracion/textos", response_class=HTMLResponse)
def pagina_textos(request: Request, sesion: SesionBD, _: Admin) -> Response:
    parametros = leer_parametros(sesion)
    grupos = [
        GrupoEnPantalla(grupo, [_en_pantalla(e, parametros) for e in grupo.textos])
        for grupo in GRUPOS
    ]
    contexto = {"grupos": grupos, "DATOS": DATOS}
    return pagina(request, sesion, "textos_del_bot.html", contexto, seccion="configuracion")


Escrito = Annotated[str, Form(max_length=MAXIMO_ESCRITO)]


@router.post("/configuracion/textos/{clave}/vista-previa", response_class=HTMLResponse)
def vista_previa(
    request: Request, sesion: SesionBD, _: Admin, clave: str, texto: Escrito = ""
) -> Response:
    """Mientras el administrador escribe: cómo quedaría el mensaje, o qué le falta."""
    t = _en_pantalla(_editable(clave), leer_parametros(sesion), escrito=texto, abierto=True)
    return html(request, "_vista_previa_texto.html", {**_contexto(t), "con_error_aparte": True})


@router.post("/configuracion/textos/{clave}", response_class=HTMLResponse)
def guardar_texto(
    request: Request, sesion: SesionBD, _: Admin, clave: str, texto: Escrito = ""
) -> Response:
    editable = _editable(clave)
    try:
        distinto = textos_editables.guardar(sesion, clave, texto)
    except TextoInvalido:
        sesion.rollback()
        t = _en_pantalla(editable, leer_parametros(sesion), escrito=texto, abierto=True)
        respuesta = html(request, "_texto_del_bot.html", _contexto(t))
        return con_eventos(respuesta, error="No se guardó: revisa el texto marcado en rojo.")
    sesion.commit()
    t = _en_pantalla(editable, leer_parametros(sesion), abierto=True)
    respuesta = html(request, "_texto_del_bot.html", _contexto(t))
    if distinto:
        aviso = f"«{editable.titulo}» guardado. El bot lo usa desde el siguiente mensaje."
    else:
        aviso = f"«{editable.titulo}» quedó con el texto original."
    return con_eventos(respuesta, aviso=aviso)
