"""Probar el bot: chatear desde el dashboard con el bot de verdad, antes de conectar WhatsApp.

Usa la misma conversación que usará WhatsApp (app.bot.conversacion), así que lo que se
pida aquí entra a la fila de verdad de tu computador. Los avisos que la fila deja en la
bandeja de salida para ese celular (llamado, vehículo listo...) también aparecen en la
charla. Solo existe en desarrollo: en producción la página no está.
"""

import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.bot import conversacion
from app.bot.avisos import mensaje_del_aviso
from app.bot.mensajes import Entrada
from app.catalogo.parametros import leer_parametros
from app.clientes.errores import TelefonoInvalido
from app.clientes.reglas import normalizar_telefono
from app.config import obtener_entorno
from app.integraciones import gemini
from app.integraciones.tablas import MensajeSaliente
from app.turnos.estados import TipoAviso
from app.web.comun import Admin, SesionBD, html, pagina, verificar_origen


def solo_en_desarrollo() -> None:
    if obtener_entorno().es_produccion:
        raise HTTPException(status_code=404)


router = APIRouter(dependencies=[Depends(solo_en_desarrollo), Depends(verificar_origen)])

TIPOS_DE_AVISO = [tipo.value for tipo in TipoAviso]


def celular_de_prueba() -> str:
    """Un celular inventado, que empieza por 5799: ningún celular colombiano empieza así."""
    return f"5799{secrets.randbelow(10**8):08d}"


def _ultimo_aviso(sesion: Session, telefono: str) -> int:
    ultimo = sesion.scalar(
        select(func.max(MensajeSaliente.id)).where(MensajeSaliente.telefono == telefono)
    )
    return ultimo or 0


@router.get("/probar-bot", response_class=HTMLResponse)
def pagina_probar_bot(request: Request, sesion: SesionBD, _: Admin, celular: str = "") -> Response:
    error = None
    telefono = celular_de_prueba()
    if celular.strip():
        try:
            telefono = normalizar_telefono(celular)
        except TelefonoInvalido as invalido:
            error = str(invalido)
    contexto = {
        "telefono": telefono,
        "inventado": telefono.startswith("5799"),
        # Solo los avisos que salgan desde ahora: los viejos de ese celular no se repiten.
        "desde": _ultimo_aviso(sesion, telefono),
        "error": error,
    }
    return pagina(request, sesion, "probar_bot.html", contexto, seccion="configuracion")


@router.post("/probar-bot/mensaje", response_class=HTMLResponse)
def escribirle_al_bot(
    request: Request,
    sesion: SesionBD,
    _: Admin,
    telefono: Annotated[str, Form()],
    texto: Annotated[str, Form()] = "",
    opcion: Annotated[str, Form()] = "",
    titulo: Annotated[str, Form()] = "",
) -> Response:
    entrada = Entrada(telefono=telefono, texto=texto or None, opcion=opcion or None)
    # Igual que por WhatsApp: si Gemini está conectado, entiende lo que escribes y contesta
    # las preguntas y la charla con la ficha del negocio.
    con_gemini = gemini.para_el_bot(leer_parametros(sesion))
    respuestas = conversacion.atender(sesion, entrada, interprete=con_gemini, redactor=con_gemini)
    sesion.commit()
    contexto = {"telefono": telefono, "escrito": titulo or texto, "respuestas": respuestas}
    return html(request, "_charla_del_bot.html", contexto)


@router.get("/probar-bot/avisos", response_class=HTMLResponse)
def avisos_para_el_celular(
    request: Request, sesion: SesionBD, _: Admin, telefono: str, desde: int = 0
) -> Response:
    nuevos = list(
        sesion.scalars(
            select(MensajeSaliente)
            .where(
                MensajeSaliente.telefono == telefono,
                MensajeSaliente.id > desde,
                MensajeSaliente.tipo.in_(TIPOS_DE_AVISO),
            )
            .order_by(MensajeSaliente.id)
        )
    )
    parametros = leer_parametros(sesion)
    contexto = {
        "telefono": telefono,
        "desde": nuevos[-1].id if nuevos else desde,
        "avisos": [mensaje_del_aviso(m.tipo, m.contenido, parametros) for m in nuevos],
    }
    return html(request, "_avisos_del_bot.html", contexto)
