"""Probar el bot: chatear desde el dashboard con el bot de verdad, antes de conectar WhatsApp.

Usa la misma conversación que usará WhatsApp (app.bot.conversacion), así que lo que se
pida aquí entra a la fila de verdad de tu computador. Los avisos que la fila deja en la
bandeja de salida para ese celular (llamado, vehículo listo...) y lo que le escriba el
administrador desde Comentarios también aparecen en la charla.

Lo que se dicen queda anotado en las bandejas, como por WhatsApp: así cuenta para las 24
horas de WhatsApp y se ve en el diálogo de responder de Comentarios. Nada de esto sale por
WhatsApp. Solo existe en desarrollo: en producción la página no está.
"""

import secrets
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import tiempo
from app.bot import conversacion
from app.bot.mensajes import Entrada, Mensaje
from app.catalogo.parametros import leer_parametros
from app.clientes import servicio as clientes
from app.clientes.errores import TelefonoInvalido
from app.clientes.reglas import PREFIJO_DE_PRUEBA, celular_inventado, normalizar_telefono
from app.config import obtener_entorno
from app.integraciones import bandeja_salida, gemini
from app.integraciones.bandeja_salida import TIPO_DEL_ADMINISTRADOR
from app.integraciones.tablas import EstadoMensaje, MensajeEntrante, MensajeSaliente
from app.integraciones.whatsapp import mensaje_del_bot
from app.turnos.estados import TipoAviso
from app.web.comun import Admin, SesionBD, html, pagina, verificar_origen


def solo_en_desarrollo() -> None:
    if obtener_entorno().es_produccion:
        raise HTTPException(status_code=404)


router = APIRouter(dependencies=[Depends(solo_en_desarrollo), Depends(verificar_origen)])

# Lo que le llega a la charla desde afuera: los avisos de la fila y lo que le escribe el
# administrador desde Comentarios (las respuestas del bot ya llegan con cada mensaje).
TIPOS_DE_AVISO = [tipo.value for tipo in TipoAviso]
TIPOS_QUE_LLEGAN = [*TIPOS_DE_AVISO, TIPO_DEL_ADMINISTRADOR]


def _ultimo_aviso(sesion: Session, telefono: str) -> int:
    ultimo = sesion.scalar(
        select(func.max(MensajeSaliente.id)).where(MensajeSaliente.telefono == telefono)
    )
    return ultimo or 0


@router.get("/probar-bot", response_class=HTMLResponse)
def pagina_probar_bot(request: Request, sesion: SesionBD, _: Admin, celular: str = "") -> Response:
    error = None
    telefono = celular_inventado()
    if celular.strip():
        try:
            telefono = normalizar_telefono(celular)
        except TelefonoInvalido as invalido:
            error = str(invalido)
    contexto = {
        "telefono": telefono,
        "inventado": telefono.startswith(PREFIJO_DE_PRUEBA),
        # Solo los avisos que salgan desde ahora: los viejos de ese celular no se repiten.
        "desde": _ultimo_aviso(sesion, telefono),
        "error": error,
    }
    return pagina(request, sesion, "probar_bot.html", contexto, seccion="lavadero/configuracion")


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
    try:
        telefono = normalizar_telefono(telefono)
    except TelefonoInvalido as invalido:
        raise HTTPException(status_code=400, detail=str(invalido)) from invalido
    entrada = Entrada(telefono=telefono, texto=texto or None, opcion=opcion or None)
    ahora = tiempo.ahora()
    # Igual que por WhatsApp: si Gemini está conectado, entiende lo que escribes y contesta
    # las preguntas y la charla con la ficha del negocio. Aquí, además, se ve cómo decidió
    # el bot cada respuesta (la traza): si le preguntó a Gemini y, si no, por qué.
    con_gemini = gemini.para_el_bot(leer_parametros(sesion))
    respuestas, traza = conversacion.atender_con_traza(
        sesion, entrada, ahora=ahora, interprete=con_gemini
    )
    _anotar(sesion, entrada, titulo, respuestas, ahora)
    sesion.commit()
    contexto = {
        "telefono": telefono,
        "escrito": titulo or texto,
        "respuestas": respuestas,
        "traza": traza.texto(),
    }
    return html(request, "_charla_del_bot.html", contexto)


def _anotar(
    sesion: Session, entrada: Entrada, titulo: str, respuestas: list[Mensaje], ahora: datetime
) -> None:
    """Lo que se dijeron queda en las bandejas como si fuera por WhatsApp, pero ya atendido
    y ya enviado (aquí mismo): el reloj no lo vuelve a atender ni lo manda."""
    if entrada.opcion:
        tipo, contenido = "interactive", {"opcion": entrada.opcion, "titulo": titulo or None}
    else:
        tipo, contenido = "text", {"texto": entrada.texto or ""}
    sesion.add(
        MensajeEntrante(
            id_meta=f"prueba-{secrets.token_hex(12)}",
            telefono=entrada.telefono,
            tipo=tipo,
            contenido=contenido,
            recibido_en=ahora,
            procesado_en=ahora,
        )
    )
    cliente = clientes.buscar_por_telefono(sesion, entrada.telefono)
    for respuesta in respuestas:
        mensaje = bandeja_salida.encolar_respuesta(
            sesion,
            telefono=entrada.telefono,
            mensaje=respuesta,
            ahora=ahora,
            cliente_id=cliente.id if cliente else None,
        )
        mensaje.estado = EstadoMensaje.ENVIADO
        mensaje.enviado_en = ahora
    sesion.flush()


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
                MensajeSaliente.tipo.in_(TIPOS_QUE_LLEGAN),
            )
            .order_by(MensajeSaliente.id)
        )
    )
    contexto = {
        "telefono": telefono,
        "desde": nuevos[-1].id if nuevos else desde,
        "avisos": [_como_llega(sesion, mensaje) for mensaje in nuevos],
    }
    return html(request, "_avisos_del_bot.html", contexto)


def _como_llega(sesion: Session, mensaje: MensajeSaliente) -> dict[str, Any]:
    """El mensaje como lo ve el cliente, con una etiqueta que dice de dónde salió."""
    if mensaje.tipo == TIPO_DEL_ADMINISTRADOR:
        por = (mensaje.contenido or {}).get("por") or "el administrador"
        etiqueta, de_una_persona = f"Lo escribió {por} en Comentarios", True
    else:
        etiqueta, de_una_persona = "Aviso de la fila", False
    return {
        "mensaje": mensaje_del_bot(sesion, mensaje),
        "etiqueta": etiqueta,
        "de_una_persona": de_una_persona,
    }
