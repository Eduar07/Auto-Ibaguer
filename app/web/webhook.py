"""La dirección a la que Meta avisa cada mensaje de WhatsApp: el webhook.

- GET /webhook/whatsapp: Meta la verifica una sola vez, cuando la registras en la app.
- POST /webhook/whatsapp: cada mensaje nuevo de un cliente y cada mensaje nuestro que no
  se pudo entregar. Se revisa la firma de Meta, se guarda y se le contesta a Meta de una
  vez (si tarda, Meta lo reenvía). Los mensajes se atienden enseguida, aparte.

No lleva la sesión del administrador: lo protege la firma de Meta, que solo Meta puede
hacer con la clave secreta de la app.
"""

import hmac
import json
import logging
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse, Response

from app import tiempo
from app.config import obtener_entorno
from app.db.sesion import AbrirSesion, abrir_sesion
from app.integraciones import bandeja_entrada, bandeja_salida, whatsapp
from app.integraciones.bandeja_salida import Enviador
from app.web.comun import SesionBD

logger = logging.getLogger(__name__)

router = APIRouter()


def obtener_enviador() -> Enviador | None:
    return whatsapp.del_entorno()


def obtener_abrir() -> AbrirSesion:
    return abrir_sesion


# Meta dice que un aviso pesa a lo sumo 3 MB. Uno más grande no es de Meta: se rechaza
# sin terminar de leerlo, para que nadie llene la memoria del servidor.
TAMANO_MAXIMO_DEL_AVISO = 4 * 1024 * 1024


async def cuerpo_crudo(request: Request) -> bytes:
    """El aviso tal como llegó: la firma se calcula sobre estos bytes exactos."""
    cuerpo = bytearray()
    async for pedazo in request.stream():
        cuerpo += pedazo
        if len(cuerpo) > TAMANO_MAXIMO_DEL_AVISO:
            raise HTTPException(status_code=413, detail="El aviso es demasiado grande.")
    return bytes(cuerpo)


@router.get("/webhook/whatsapp")
def verificar_webhook(
    modo: Annotated[str, Query(alias="hub.mode")] = "",
    token: Annotated[str, Query(alias="hub.verify_token")] = "",
    reto: Annotated[str, Query(alias="hub.challenge")] = "",
) -> Response:
    """Meta pregunta "¿esta dirección es tuya?" con el token de verificación que escribiste
    al registrarla. Si coincide, se le devuelve el número que mandó (el reto)."""
    esperado = obtener_entorno().whatsapp_token_verificacion
    if modo == "subscribe" and esperado and hmac.compare_digest(token, esperado):
        return PlainTextResponse(reto)
    raise HTTPException(status_code=403, detail="Token de verificación incorrecto.")


@router.post("/webhook/whatsapp")
def recibir_de_whatsapp(
    request: Request,
    sesion: SesionBD,
    tareas: BackgroundTasks,
    cuerpo: Annotated[bytes, Depends(cuerpo_crudo)],
    enviador: Annotated[Enviador | None, Depends(obtener_enviador)],
    abrir: Annotated[AbrirSesion, Depends(obtener_abrir)],
) -> Response:
    entorno = obtener_entorno()
    if not entorno.whatsapp_conectado or enviador is None:
        raise HTTPException(status_code=503, detail="WhatsApp no está configurado.")
    firma = request.headers.get("x-hub-signature-256")
    if not whatsapp.firma_valida(cuerpo, firma, entorno.whatsapp_secreto_app):
        logger.warning("Llegó un aviso al webhook de WhatsApp sin una firma válida: se descartó.")
        raise HTTPException(status_code=403, detail="Firma inválida.")
    try:
        datos = json.loads(cuerpo)
    except ValueError as error:
        raise HTTPException(status_code=400, detail="El aviso no es JSON.") from error
    if not isinstance(datos, dict):
        raise HTTPException(status_code=400, detail="El aviso no tiene la forma que usa Meta.")
    recibidos, no_entregados = whatsapp.leer_aviso_de_meta(datos, entorno.whatsapp_id_numero)
    por_atender = bandeja_entrada.guardar(sesion, recibidos, ahora=tiempo.ahora())
    bandeja_salida.registrar_estados(sesion, ((n.id_meta, n.motivo) for n in no_entregados))
    sesion.commit()
    if por_atender:
        # Corre después de contestarle a Meta: atiende los mensajes y envía las respuestas.
        tareas.add_task(bandeja_entrada.atender_y_responder, abrir, enviador, por_atender)
    return Response(status_code=200)
