"""Gemini (Google): entiende lo que el cliente escribe con sus palabras.

Recibe el mensaje (con los datos personales tapados: app.bot.interprete.ocultar_datos) y
devuelve qué quiere el cliente, de una lista cerrada, en JSON con un formato fijo. Si no
responde en 5 segundos, falla o contesta algo raro, devuelve None y el bot sigue con
botones. El mensaje del cliente nunca se escribe en el registro del servidor.

La clave va en el .env (GEMINI_CLAVE) y el modelo se elige en Configuración.
"""

import json
import logging
import re
import threading
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import httpx

from app.bot.interprete import (
    CARRO,
    NINGUNO,
    Catalogo,
    Intencion,
    Interpretacion,
    leer_respuesta,
    ocultar_datos,
)
from app.catalogo.configuracion import MODELO_GEMINI
from app.catalogo.parametros import Parametros
from app.config import obtener_entorno

logger = logging.getLogger(__name__)

URL_DE_GEMINI = "https://generativelanguage.googleapis.com/v1beta/models"
ESPERA = 5.0  # segundos: si no ha respondido, el bot muestra el menú

# --- Lo que se le pide -----------------------------------------------------------------------

INSTRUCCIONES = """\
Eres el intérprete del bot de WhatsApp de {negocio}, un lavadero de carros y motos en \
Colombia. Te llega el mensaje de un cliente. No le contestas: solo dices qué quiere, con \
el formato pedido.

intencion:
- pedir_turno: quiere lavar su vehículo o pedir un turno. Por ejemplo: "quiero lavar la \
moto", "me separa un turno", "hay cupo para ahorita?".
- ver_fila: pregunta cómo está la fila, si hay mucha gente o cuánto tendría que esperar. \
Por ejemplo: "está muy lleno?", "cuánto me toca esperar si voy ya".
- precios: pregunta cuánto vale algo o qué servicios hay. Por ejemplo: "cuánto vale lavar \
la camionetica", "qué incluye el general".
- mi_turno: pregunta por un turno que ya pidió. Por ejemplo: "ya casi me toca?", "ya está \
lista mi moto?".
- cancelar: ya no va a ir o quiere cancelar su turno. Por ejemplo: "ya no voy a poder ir", \
"me salió un imprevisto, no alcanzo".
- pico_y_placa: pregunta por el pico y placa.
- otro: cualquier otra cosa (horarios, formas de pago, dirección, saludos, quejas) o si no \
está claro qué quiere.

vehiculo: el vehículo del que habla, uno de: {vehiculos}. "carro" si dice carro sin decir \
cuál de esos es. "ninguno" si no habla de un vehículo.

servicio: el servicio que nombra, uno de: {servicios}. "ninguno" si no nombra ninguno.

Lo que aparece como [placa], [número] o [correo] es un dato del cliente que se tapó."""


def esquema(catalogo: Catalogo) -> dict[str, Any]:
    """El formato fijo de la respuesta: Gemini no puede contestar otra cosa."""
    return {
        "type": "object",
        "properties": {
            "intencion": {"type": "string", "enum": [i.value for i in Intencion]},
            "vehiculo": {"type": "string", "enum": [*catalogo.vehiculos, CARRO, NINGUNO]},
            "servicio": {"type": "string", "enum": [*catalogo.servicios, NINGUNO]},
        },
        "required": ["intencion", "vehiculo", "servicio"],
        "additionalProperties": False,
    }


def _sin_pensar(modelo: str) -> dict[str, Any]:
    """Para clasificar un mensaje no hace falta que el modelo "piense" antes: así responde
    rápido. Los modelos 2.x lo apagan con thinkingBudget; desde los 3, thinkingBudget da
    error y se usa thinkingLevel, que en los Pro no baja de LOW."""
    if modelo.startswith("gemini-2"):
        return {} if "pro" in modelo else {"thinkingConfig": {"thinkingBudget": 0}}
    return {"thinkingConfig": {"thinkingLevel": "LOW" if "pro" in modelo else "MINIMAL"}}


def peticion(mensaje: str, catalogo: Catalogo, *, modelo: str, negocio: str) -> dict[str, Any]:
    instrucciones = INSTRUCCIONES.format(
        negocio=negocio,
        vehiculos=", ".join(catalogo.vehiculos),
        servicios=", ".join(catalogo.servicios),
    )
    return {
        "systemInstruction": {"parts": [{"text": instrucciones}]},
        "contents": [{"role": "user", "parts": [{"text": ocultar_datos(mensaje)}]}],
        # Los nombres que entiende la API de Gemini de AI Studio (generateContent). El
        # "responseFormat" nuevo es solo de Vertex AI: aquí daría error.
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseJsonSchema": esquema(catalogo),
            "maxOutputTokens": 256,
            **_sin_pensar(modelo),
        },
    }


# --- Lo que responde -------------------------------------------------------------------------


def texto_de_la_respuesta(datos: Any) -> str | None:
    """El texto que escribió el modelo (sin lo que "pensó"), o None si no escribió nada."""
    if not isinstance(datos, dict):
        return None
    for candidato in datos.get("candidates") or []:
        partes = ((candidato or {}).get("content") or {}).get("parts") or []
        texto = "".join(
            str(parte.get("text", ""))
            for parte in partes
            if isinstance(parte, dict) and not parte.get("thought")
        )
        if texto.strip():
            return texto
    return None


def _sin_cercas(texto: str) -> str:
    """Por si el modelo lo envuelve en ```json ... ```."""
    return re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", texto)


# Un problema se escribe en el registro una vez cada 5 minutos, no con cada mensaje.
_ULTIMA_VEZ: dict[str, float] = {}
_CANDADO = threading.Lock()


def _avisar(problema: str) -> None:
    """Lo escribe en el registro del servidor (nunca el mensaje del cliente)."""
    ahora = time.monotonic()
    with _CANDADO:
        if ahora - _ULTIMA_VEZ.get(problema, -1e9) < 300:
            return
        _ULTIMA_VEZ[problema] = ahora
    logger.warning("Gemini %s El bot siguió con los botones.", problema)


def _detalle_del_error(respuesta: httpx.Response) -> str:
    try:
        error = respuesta.json().get("error") or {}
    except (ValueError, AttributeError):
        return ""
    return str(error.get("message") or "")[:200]


class Gemini:
    """El intérprete de verdad: la API de Gemini (generateContent)."""

    def __init__(
        self,
        clave: str,
        modelo: str,
        *,
        negocio: str = "un lavadero",
        http: httpx.Client | None = None,
    ) -> None:
        self.modelo = modelo
        self.negocio = negocio
        self.url = f"{URL_DE_GEMINI}/{modelo}:generateContent"
        self._encabezados = {"x-goog-api-key": clave}
        self.http = http or _http()
        # Por qué no respondió la última vez (para las frases de prueba). None si respondió.
        self.ultimo_problema: str | None = None

    def interpretar(self, mensaje: str, catalogo: Catalogo) -> Interpretacion | None:
        self.ultimo_problema = None
        cuerpo = peticion(mensaje, catalogo, modelo=self.modelo, negocio=self.negocio)
        try:
            respuesta = self.http.post(self.url, json=cuerpo, headers=self._encabezados)
        except httpx.TimeoutException:
            return self._no_sirvio(f"no respondió en {ESPERA:.0f} segundos.")
        except httpx.HTTPError as error:
            return self._no_sirvio(f"no se pudo conectar ({type(error).__name__}).")
        if respuesta.status_code == 429:
            return self._no_sirvio("no atendió más mensajes por ahora: se acabó la cuota (429).")
        if not respuesta.is_success:
            detalle = _detalle_del_error(respuesta)
            return self._no_sirvio(
                f"respondió {respuesta.status_code}{': ' + detalle if detalle else ''}."
            )
        try:
            texto = texto_de_la_respuesta(respuesta.json())
            crudo = json.loads(_sin_cercas(texto)) if texto else None
        except ValueError:
            crudo = None
        interpretacion = leer_respuesta(crudo, catalogo)
        if interpretacion is None:
            return self._no_sirvio("contestó algo que no tiene el formato pedido.")
        return interpretacion

    def _no_sirvio(self, problema: str) -> None:
        self.ultimo_problema = problema
        _avisar(problema)
        return None


@lru_cache
def _http() -> httpx.Client:
    return httpx.Client(timeout=httpx.Timeout(ESPERA, connect=3.0))


# --- ¿Se usa? ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Estado:
    """Lo que la pantalla de Configuración dice de Gemini."""

    usar: bool
    texto: str
    color: str  # verde, naranja o gris


def estado(parametros: Parametros) -> Estado:
    entorno = obtener_entorno()
    if not entorno.gemini_clave:
        return Estado(False, "Falta GEMINI_CLAVE en el archivo .env.", "gris")
    if not parametros.gemini_activo:
        return Estado(False, "Apagado: el bot entiende solo los botones y unas palabras.", "gris")
    if not MODELO_GEMINI.fullmatch(parametros.modelo_gemini):
        return Estado(False, "El nombre del modelo no sirve: corrígelo aquí.", "naranja")
    if entorno.gemini_plan_de_pago:
        return Estado(True, "Conectado, con el plan de pago.", "verde")
    if entorno.es_produccion:
        return Estado(
            False,
            "Apagado en la nube: el plan gratis no se usa con clientes de verdad. Pon "
            "GEMINI_PLAN_DE_PAGO=true cuando el proyecto de Google tenga facturación.",
            "naranja",
        )
    return Estado(
        True,
        "Conectado, con el plan gratis: Google puede leer los mensajes, así que úsalo solo "
        "para probar con mensajes tuyos.",
        "naranja",
    )


def para_el_bot(parametros: Parametros) -> Gemini | None:
    """El intérprete que usa el bot, o None si Gemini no se debe usar ahora."""
    if not estado(parametros).usar:
        return None
    return Gemini(
        obtener_entorno().gemini_clave, parametros.modelo_gemini, negocio=parametros.nombre_negocio
    )
