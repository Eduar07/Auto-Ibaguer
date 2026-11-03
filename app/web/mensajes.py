"""La pantalla de mensajes de WhatsApp: lo último que entró y salió, y si WhatsApp está
conectado. Sirve para revisar, por ejemplo, por qué a un cliente no le llegó un aviso."""

from dataclasses import dataclass
from datetime import datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import select

from app import tiempo
from app.bot.mensajes import recortar
from app.config import obtener_entorno
from app.integraciones.bandeja_salida import TIPO_RESPUESTA
from app.integraciones.tablas import EstadoMensaje, MensajeEntrante, MensajeSaliente
from app.web.comun import Admin, SesionBD, pagina, verificar_origen

router = APIRouter(dependencies=[Depends(verificar_origen)])

CUANTOS = 40

QUE_AVISO = {
    "aviso_anticipado": "Aviso: ya casi le toca",
    "llamado": "Aviso: lo llamamos",
    "pasa_al_final": "Aviso: pasó al final",
    "no_llego": "Aviso: perdió el turno",
    "vehiculo_listo": "Aviso: vehículo listo",
}
QUE_MANDO = {
    "image": "una foto",
    "audio": "un audio",
    "video": "un video",
    "document": "un documento",
    "sticker": "un sticker",
    "location": "una ubicación",
    "contacts": "un contacto",
    "reaction": "una reacción",
}
COMO_VA = {
    EstadoMensaje.PENDIENTE: ("Por enviar", "gris"),
    EstadoMensaje.ENVIADO: ("Enviado", "verde"),
    EstadoMensaje.FALLIDO: ("No salió", "rojo"),
    EstadoMensaje.DESCARTADO: ("Ya no servía", "gris"),
}
# Lo que hace falta en el .env para conectar WhatsApp (solo los nombres, nunca los valores).
DATOS_DE_CONEXION = {
    "WHATSAPP_TOKEN": "whatsapp_token",
    "WHATSAPP_ID_NUMERO": "whatsapp_id_numero",
    "WHATSAPP_SECRETO_APP": "whatsapp_secreto_app",
    "WHATSAPP_TOKEN_VERIFICACION": "whatsapp_token_verificacion",
}


@dataclass(frozen=True)
class Entrante:
    cuando: datetime
    telefono: str
    dice: str
    atendido: bool
    error: str | None


@dataclass(frozen=True)
class Saliente:
    cuando: datetime
    telefono: str
    dice: str
    estado: str
    color: str
    intentos: int
    error: str | None
    # El error ya no importa: el mensaje salió al reintentar, o ya no servía.
    error_superado: bool
    # Si falló y espera otro intento: a qué hora.
    reintenta: datetime | None


def _saliente(mensaje: MensajeSaliente) -> Saliente:
    estado, color = COMO_VA[mensaje.estado]
    espera = mensaje.estado == EstadoMensaje.PENDIENTE and mensaje.intentos > 0
    return Saliente(
        cuando=tiempo.hora_local(mensaje.creado_en),
        telefono=mensaje.telefono,
        dice=_lo_que_dice(mensaje),
        estado=estado,
        color=color,
        intentos=mensaje.intentos,
        error=mensaje.ultimo_error,
        error_superado=mensaje.estado in {EstadoMensaje.ENVIADO, EstadoMensaje.DESCARTADO},
        reintenta=tiempo.hora_local(mensaje.proximo_intento_en) if espera else None,
    )


def _lo_que_dijo(mensaje: MensajeEntrante) -> str:
    contenido = mensaje.contenido or {}
    if contenido.get("titulo") or contenido.get("opcion"):
        return f"Tocó «{contenido.get('titulo') or contenido.get('opcion')}»"
    if contenido.get("texto"):
        return recortar(contenido["texto"], 120)
    return f"Mandó {QUE_MANDO.get(mensaje.tipo, 'algo que no es texto')}"


def _lo_que_dice(mensaje: MensajeSaliente) -> str:
    if mensaje.tipo == TIPO_RESPUESTA:
        return recortar((mensaje.contenido or {}).get("texto", ""), 120)
    return QUE_AVISO.get(mensaje.tipo, mensaje.tipo)


@router.get("/mensajes", response_class=HTMLResponse)
def pagina_mensajes(request: Request, sesion: SesionBD, _: Admin) -> Response:
    entorno = obtener_entorno()
    entrantes = [
        Entrante(
            tiempo.hora_local(m.recibido_en),
            m.telefono,
            _lo_que_dijo(m),
            m.procesado_en is not None,
            m.error,
        )
        for m in sesion.scalars(
            select(MensajeEntrante).order_by(MensajeEntrante.id.desc()).limit(CUANTOS)
        )
    ]
    salientes = [
        _saliente(m)
        for m in sesion.scalars(
            select(MensajeSaliente).order_by(MensajeSaliente.id.desc()).limit(CUANTOS)
        )
    ]
    contexto = {
        "conectado": entorno.whatsapp_conectado,
        "faltan": [
            nombre for nombre, campo in DATOS_DE_CONEXION.items() if not getattr(entorno, campo)
        ],
        "entrantes": entrantes,
        "salientes": salientes,
    }
    return pagina(request, sesion, "mensajes.html", contexto, seccion="configuracion")
