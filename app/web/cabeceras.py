"""Cabeceras de seguridad en cada respuesta del servidor.

Le dicen al navegador que no adivine qué tipo de archivo es cada cosa, que ninguna otra
página puede mostrar el dashboard dentro de la suya (así nadie lo disfraza para que el
administrador toque algo sin darse cuenta) y que no le cuente a otros sitios de qué
pantalla viene. En la nube, además, que siempre use HTTPS.
"""

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config import obtener_entorno

CABECERAS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "frame-ancestors 'none'",
    "Referrer-Policy": "same-origin",
}
# Un año: el navegador ya no intenta entrar sin HTTPS.
SOLO_HTTPS = "max-age=31536000"


class CabecerasDeSeguridad:
    """Se escribe "a mano" (ASGI puro) para no estorbar a la fila en vivo, que es una
    respuesta que nunca termina."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def con_cabeceras(mensaje: Message) -> None:
            if mensaje["type"] == "http.response.start":
                encabezados = MutableHeaders(scope=mensaje)
                for nombre, valor in CABECERAS.items():
                    encabezados.setdefault(nombre, valor)
                if obtener_entorno().es_produccion:
                    encabezados.setdefault("Strict-Transport-Security", SOLO_HTTPS)
            await send(mensaje)

        await self.app(scope, receive, con_cabeceras)
