"""El diagnóstico de WhatsApp: saber con datos por qué a alguien no le contestó el bot.

Dos preguntas, en Mensajes > Diagnóstico:

1. ¿Llegó su mensaje al servidor? Cada aviso que Meta manda al webhook se anota
   (`anotar`), con lo que traía y qué se hizo con cada mensaje. Si un cliente escribió y
   aquí no aparece nada a esa hora, Meta nunca lo mandó: el problema no es del bot.
2. ¿Está bien el número del bot en Meta? `revisar_meta` le pregunta a Meta cuál es el
   número, su estado, su calidad, a dónde manda los avisos y qué apps los reciben.

Nada de lo que escribió el cliente se guarda aquí: del remitente, solo el celular a
medias ("322 *** 6850"); de cada mensaje, el tipo y los nombres de los campos.
"""

from dataclasses import dataclass, field
from typing import Any

import httpx
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app import formato
from app.clientes.reglas import es_usuario_de_whatsapp
from app.config import ConfiguracionEntorno
from app.integraciones import whatsapp
from app.integraciones.tablas import QUEDAN_AVISOS, AvisoDeMeta

FIRMA_INVALIDA = (
    "La firma no es de Meta: se rechazó. Si pasa con todos, revisa WHATSAPP_SECRETO_APP."
)
NO_ES_JSON = "El aviso no tenía la forma que usa Meta: se rechazó."


def _quien(mensaje: dict[str, Any]) -> str:
    celular, usuario = mensaje.get("from"), mensaje.get("from_user_id")
    if isinstance(celular, str) and celular.isdigit():
        return formato.telefono_oculto(celular)
    if isinstance(usuario, str) and es_usuario_de_whatsapp(usuario):
        return formato.SIN_CELULAR_VISIBLE
    return "Sin remitente"


def _de_un_mensaje(mensaje: dict[str, Any]) -> dict[str, Any]:
    atendible = whatsapp.leer_mensaje(mensaje) is not None
    return {
        "clase": "mensaje",
        "quien": _quien(mensaje),
        "con_usuario": bool(mensaje.get("from_user_id")),
        "tipo": str(mensaje.get("type") or "?")[:30],
        "campos": whatsapp.campos(mensaje)[:20],
        "atendible": atendible,
        "motivo": None if atendible else whatsapp.por_que_se_descarta(mensaje),
    }


def _de_un_estado(estado: dict[str, Any]) -> dict[str, Any]:
    fallo = estado.get("status") == "failed"
    return {
        "clase": "estado",
        "estado": str(estado.get("status") or "?")[:20],
        "motivo": whatsapp.motivo_del_estado(estado) if fallo else None,
    }


def resumir(datos: dict[str, Any], id_numero: str) -> AvisoDeMeta:
    """Lo que traía un aviso de Meta, sin datos de nadie."""
    aviso = AvisoDeMeta(firma_valida=True, mensajes=0, estados=0, detalle=[])
    otros_campos: set[str] = set()
    for entrada in whatsapp._lista(datos.get("entry")):
        for cambio in whatsapp._lista(entrada.get("changes")):
            if cambio.get("field") != "messages":
                otros_campos.add(str(cambio.get("field")))
                continue
            valor = cambio.get("value") or {}
            para = (valor.get("metadata") or {}).get("phone_number_id")
            aviso.para_el_numero = str(para)[:40] if para is not None else None
            mensajes = whatsapp._lista(valor.get("messages"))
            estados = whatsapp._lista(valor.get("statuses"))
            aviso.mensajes += len(mensajes)
            aviso.estados += len(estados)
            aviso.detalle += [_de_un_mensaje(m) for m in mensajes]
            aviso.detalle += [_de_un_estado(e) for e in estados]
    if aviso.para_el_numero is not None and aviso.para_el_numero != id_numero:
        aviso.problema = (
            f"Era para otro número de WhatsApp (id {aviso.para_el_numero}), no para el del "
            "bot: no se atendió."
        )
    elif otros_campos and not aviso.detalle:
        aviso.problema = f"Era de otro tema de Meta ({', '.join(sorted(otros_campos))})."
    return aviso


def anotar(sesion: Session, aviso: AvisoDeMeta) -> None:
    """Guarda el aviso y borra los más viejos. Si algo falla aquí, el webhook sigue: el
    diagnóstico nunca puede impedir que el bot conteste."""
    try:
        with sesion.begin_nested():
            sesion.add(aviso)
            sesion.flush()
            corte = sesion.scalar(
                select(AvisoDeMeta.id)
                .order_by(AvisoDeMeta.id.desc())
                .offset(QUEDAN_AVISOS)
                .limit(1)
            )
            if corte is not None:
                sesion.execute(delete(AvisoDeMeta).where(AvisoDeMeta.id <= corte))
    except Exception:  # noqa: BLE001 - el diagnóstico es un extra
        whatsapp.logger.exception("No se pudo anotar el aviso de Meta en el diagnóstico.")


def rechazado(problema: str) -> AvisoDeMeta:
    return AvisoDeMeta(firma_valida=False, mensajes=0, estados=0, detalle=[], problema=problema)


def ultimos(sesion: Session, cuantos: int = 60) -> list[AvisoDeMeta]:
    return list(sesion.scalars(select(AvisoDeMeta).order_by(AvisoDeMeta.id.desc()).limit(cuantos)))


# --- Lo que dice Meta del número del bot -----------------------------------------------------

CAMPOS_DEL_NUMERO = (
    "display_phone_number,verified_name,status,quality_rating,platform_type,"
    "code_verification_status,name_status,account_mode,messaging_limit_tier,"
    "webhook_configuration"
)
ESTADOS = {
    "CONNECTED": "Conectado",
    "PENDING": "Pendiente",
    "DISCONNECTED": "Desconectado",
    "FLAGGED": "Marcado por Meta",
    "RESTRICTED": "Restringido",
    "BANNED": "Bloqueado",
    "MIGRATED": "Migrado",
    "RATE_LIMITED": "Limitado",
}
CALIDAD = {"GREEN": "Alta", "YELLOW": "Media", "RED": "Baja", "UNKNOWN": "Sin calificar"}


@dataclass
class RevisionDeMeta:
    """Lo que Meta dice del número del bot. Cada `error` dice qué no se pudo preguntar."""

    numero: str | None = None
    nombre: str | None = None
    estado: str | None = None
    calidad: str | None = None
    plataforma: str | None = None
    modo: str | None = None
    verificacion: str | None = None
    nombre_aprobado: str | None = None
    limite: str | None = None
    avisos_a: str | None = None
    apps: list[str] | None = None
    errores: list[str] = field(default_factory=list)
    alertas: list[str] = field(default_factory=list)


def revisar_meta(entorno: ConfiguracionEntorno, http: httpx.Client | None = None) -> RevisionDeMeta:
    """Le pregunta a Meta por el número del bot y por las apps que reciben sus avisos."""
    revision = RevisionDeMeta()
    if not entorno.whatsapp_conectado:
        revision.errores.append("WhatsApp no está configurado en el servidor.")
        return revision
    cliente = http or httpx.Client(timeout=httpx.Timeout(15.0, connect=5.0))
    base = f"{whatsapp.URL_DE_META}/{entorno.whatsapp_version_api}"
    encabezados = {"Authorization": f"Bearer {entorno.whatsapp_token}"}
    try:
        respuesta = cliente.get(
            f"{base}/{entorno.whatsapp_id_numero}",
            params={"fields": CAMPOS_DEL_NUMERO},
            headers=encabezados,
        )
        if respuesta.is_success:
            _leer_el_numero(revision, respuesta.json())
        else:
            revision.errores.append(f"Meta no dio los datos del número: {_por_que(respuesta)}")
        if entorno.whatsapp_id_cuenta:
            apps = cliente.get(
                f"{base}/{entorno.whatsapp_id_cuenta}/subscribed_apps", headers=encabezados
            )
            if apps.is_success:
                revision.apps = [
                    str((app.get("whatsapp_business_api_data") or {}).get("name") or "sin nombre")
                    for app in (apps.json().get("data") or [])
                    if isinstance(app, dict)
                ]
            else:
                revision.errores.append(
                    f"Meta no dijo qué apps reciben los avisos: {_por_que(apps)}"
                )
    except (httpx.HTTPError, ValueError, AttributeError) as error:
        revision.errores.append(f"No hubo conexión con Meta ({type(error).__name__}).")
    _alertas(revision, entorno)
    return revision


def _por_que(respuesta: httpx.Response) -> str:
    return str(whatsapp._por_que_no(respuesta))


def _leer_el_numero(revision: RevisionDeMeta, datos: dict[str, Any]) -> None:
    revision.numero = datos.get("display_phone_number")
    revision.nombre = datos.get("verified_name")
    estado = datos.get("status")
    revision.estado = ESTADOS.get(estado, estado)
    calidad = datos.get("quality_rating")
    revision.calidad = CALIDAD.get(calidad, calidad)
    revision.plataforma = datos.get("platform_type")
    revision.modo = datos.get("account_mode")
    revision.verificacion = datos.get("code_verification_status")
    revision.nombre_aprobado = datos.get("name_status")
    revision.limite = datos.get("messaging_limit_tier")
    configuracion = datos.get("webhook_configuration") or {}
    if isinstance(configuracion, dict):
        revision.avisos_a = (
            configuracion.get("phone_number")
            or configuracion.get("whatsapp_business_account")
            or configuracion.get("application")
        )


def _alertas(revision: RevisionDeMeta, entorno: ConfiguracionEntorno) -> None:
    if revision.estado and revision.estado != "Conectado":
        revision.alertas.append(f"El número no está conectado en Meta: {revision.estado}.")
    if revision.plataforma and revision.plataforma != "CLOUD_API":
        revision.alertas.append(
            f"El número no está en la Cloud API (está en {revision.plataforma}): hay que "
            "registrarlo otra vez."
        )
    if revision.modo and revision.modo != "LIVE":
        revision.alertas.append(f"El número está en modo {revision.modo}, no en LIVE.")
    if revision.avisos_a and not revision.avisos_a.rstrip("/").endswith("/webhook/whatsapp"):
        revision.alertas.append(
            f"Meta manda los avisos de este número a {revision.avisos_a}, no al webhook del bot."
        )
    if not entorno.whatsapp_id_cuenta:
        revision.alertas.append(
            "Falta WHATSAPP_ID_CUENTA en Render: sin él no se puede revisar ni arreglar qué "
            "apps reciben los mensajes de la cuenta."
        )
    elif revision.apps is not None and not revision.apps:
        revision.alertas.append(
            "Ninguna app está enlazada a la cuenta de WhatsApp: Meta no le avisa al bot de "
            "los mensajes. Toca «Enlazar la app otra vez»."
        )
