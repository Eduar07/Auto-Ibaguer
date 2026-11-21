"""La pantalla de mensajes de WhatsApp: lo último que entró y salió, y si WhatsApp está
conectado. Sirve para revisar, por ejemplo, por qué a un cliente no le llegó un aviso."""

from dataclasses import dataclass
from datetime import datetime
from typing import Annotated
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import select

from app import tiempo
from app.bot.mensajes import recortar
from app.clientes.reglas import TelefonoInvalido, normalizar_contacto
from app.config import obtener_entorno
from app.integraciones import diagnostico, whatsapp
from app.integraciones.bandeja_salida import TIPO_DEL_ADMINISTRADOR, TIPO_RESPUESTA
from app.integraciones.historial import lo_que_vio_gemini
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
    visto = lo_que_vio_gemini(mensaje.tipo, contenido)
    if visto is not None:
        return recortar(visto, 120)
    if contenido.get("texto"):
        return recortar(contenido["texto"], 120)
    return f"Mandó {QUE_MANDO.get(mensaje.tipo, 'algo que no es texto')}"


def _lo_que_dice(mensaje: MensajeSaliente) -> str:
    if mensaje.tipo == TIPO_RESPUESTA:
        contenido = mensaje.contenido or {}
        video = "Video del servicio: " if contenido.get("video") is not None else ""
        return recortar(video + contenido.get("texto", ""), 120)
    if mensaje.tipo == TIPO_DEL_ADMINISTRADOR:
        contenido = mensaje.contenido or {}
        por = contenido.get("por") or "Administrador"
        return recortar(f"{por}: {contenido.get('texto', '')}", 120)
    return QUE_AVISO.get(mensaje.tipo, mensaje.tipo)


def _el_celular(texto: str) -> tuple[str | None, str | None]:
    """El celular que buscan, como lo guarda WhatsApp, o por qué no sirve."""
    if not texto.strip():
        return None, None
    try:
        return normalizar_contacto(texto), None
    except TelefonoInvalido:
        return None, "Escribe el celular con sus 10 dígitos, por ejemplo 315 123 4567."


@router.get("/mensajes", response_class=HTMLResponse)
def pagina_mensajes(request: Request, sesion: SesionBD, _: Admin, celular: str = "") -> Response:
    """Lo último que entró y salió. Con `celular`, solo lo de ese número: así se ve de una
    si le escribió al bot y qué pasó con la respuesta."""
    entorno = obtener_entorno()
    buscado, error_del_celular = _el_celular(celular)
    de_entrada = select(MensajeEntrante).order_by(MensajeEntrante.id.desc()).limit(CUANTOS)
    de_salida = select(MensajeSaliente).order_by(MensajeSaliente.id.desc()).limit(CUANTOS)
    if buscado is not None:
        de_entrada = de_entrada.where(MensajeEntrante.telefono == buscado)
        de_salida = de_salida.where(MensajeSaliente.telefono == buscado)
    entrantes = [
        Entrante(
            tiempo.hora_local(m.recibido_en),
            m.telefono,
            _lo_que_dijo(m),
            m.procesado_en is not None,
            m.error,
        )
        for m in sesion.scalars(de_entrada)
    ]
    salientes = [_saliente(m) for m in sesion.scalars(de_salida)]
    contexto = {
        "conectado": entorno.whatsapp_conectado,
        "faltan": [
            nombre for nombre, campo in DATOS_DE_CONEXION.items() if not getattr(entorno, campo)
        ],
        "entrantes": entrantes,
        "salientes": salientes,
        "celular": celular.strip(),
        "buscado": buscado,
        "error_del_celular": error_del_celular,
    }
    return pagina(request, sesion, "mensajes.html", contexto, seccion="lavadero/configuracion")


# --- Diagnóstico: ¿llegó el mensaje?, ¿está bien el número en Meta? --------------------------


def obtener_http_de_meta() -> httpx.Client | None:
    """Con qué se le pregunta a Meta (las pruebas lo cambian por uno de mentira)."""
    return None


HttpDeMeta = Annotated[httpx.Client | None, Depends(obtener_http_de_meta)]


@router.get("/mensajes/diagnostico", response_class=HTMLResponse)
def pagina_diagnostico(
    request: Request, sesion: SesionBD, _: Admin, http: HttpDeMeta, enlazada: str = ""
) -> Response:
    """Cada aviso que llegó de Meta y lo que Meta dice del número del bot."""
    entorno = obtener_entorno()
    avisos = diagnostico.ultimos(sesion)
    contexto = {
        "revision": diagnostico.revisar_meta(entorno, http),
        "avisos": [(tiempo.hora_local(a.recibido_en), a) for a in avisos],
        "id_numero": entorno.whatsapp_id_numero,
        "puede_enlazar": entorno.whatsapp_conectado and bool(entorno.whatsapp_id_cuenta),
        "enlazada": enlazada,
    }
    return pagina(request, sesion, "diagnostico.html", contexto, seccion="lavadero/configuracion")


@router.post("/mensajes/diagnostico/enlazar")
def enlazar_otra_vez(_: Admin, http: HttpDeMeta) -> Response:
    """Enlaza otra vez la app con la cuenta de WhatsApp Business. No daña nada: si ya estaba
    enlazada, Meta contesta lo mismo."""
    entorno = obtener_entorno()
    try:
        apps = whatsapp.enlazar_la_app(entorno, http)
        resultado = "si:" + ", ".join(apps)
    except whatsapp.RegistroFallido as error:
        resultado = "no:" + str(error)
    return RedirectResponse(
        "/mensajes/diagnostico?" + urlencode({"enlazada": resultado[:300]}), status_code=303
    )
