"""Lo que el bot le pide a Gemini cuando el cliente escribe con sus palabras.

Una sola consulta por mensaje: Gemini entiende qué quiere el cliente (de una lista
cerrada) y, cuando toca conversar, escribe también la respuesta. Como un mesero que en
un solo viaje a la cocina lleva el pedido y vuelve con lo que hay que decirle a la mesa:
antes eran dos viajes (uno para entender y otro para escribir), y con el plan gratis de
Google la cocina se saturaba.

Lo que el bot hace sigue saliendo del sistema: si Gemini dice "quiere los precios de la
moto", el bot muestra los precios de la base, como si el cliente hubiera tocado el
botón. Gemini solo escribe la conversación (saludos, despedidas, preguntas sobre el
negocio, charla) y el bot lo revisa antes de mandarlo (app.bot.redactor.revisar).

Para entender el mensaje nuevo, Gemini también ve lo último que se dijeron (la memoria
del bot, `Anterior`) y en qué va la conversación (qué le está preguntando el bot): así
un "sí porfa" o un "y la camioneta?" se entienden por lo que venía antes. Antes de
mandárselo se le tapan los datos personales que se reconocen (placas, celulares y
correos): no los necesita para entender qué quiere el cliente.
"""

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol


class Intencion(StrEnum):
    SALUDO = "saludo"  # solo saluda
    DESPEDIDA = "despedida"  # se despide o agradece, sin pedir nada más
    PEDIR_TURNO = "pedir_turno"
    VER_FILA = "ver_fila"
    PRECIOS = "precios"
    MI_TURNO = "mi_turno"
    CANCELAR = "cancelar"
    LLEGUE = "llegue"  # avisa que ya está en el lavadero
    EN_CAMINO = "en_camino"  # avisa que va para allá
    PICO_Y_PLACA = "pico_y_placa"
    MIS_LAVADAS = "mis_lavadas"  # cuántas lleva con el lavadero
    QUEJA = "queja"  # se queja de algo que ya pasó
    FELICITACION = "felicitacion"
    HABLAR_CON_PERSONA = "hablar_con_persona"  # quiere que le responda alguien, no el bot
    PREGUNTA = "pregunta"  # sobre el negocio: horarios, pagos, dirección, sus datos...
    CHARLA = "charla"  # no pide nada: cuenta cómo está, bromea, habla de otro tema
    CRISIS = "crisis"  # dice en serio que quiere morir o hacerse daño: un texto fijo de ayuda
    OTRO = "otro"  # de verdad no se entiende qué quiere


# Lo que Gemini contesta en "vehiculo" fuera de los del catálogo:
CARRO = "carro"  # dijo "carro" sin decir si es automóvil o camioneta: el bot pregunta
OTRO_VEHICULO = "otro"  # un vehículo que el lavadero no tiene (un camión, una tractomula)
NINGUNO = "ninguno"  # no nombra un vehículo, un servicio o un sí o un no

# Lo que contesta a una pregunta de sí o no del bot.
SI, NO = "si", "no"


@dataclass(frozen=True)
class Catalogo:
    """Los vehículos y servicios que existen: Gemini solo puede elegir entre estos."""

    vehiculos: tuple[str, ...]  # "Automóvil", "Camioneta", "Moto"
    servicios: tuple[str, ...]  # "Básico", "Encerado"...


@dataclass(frozen=True)
class Anterior:
    """Un mensaje de lo que se venían diciendo, del más viejo al más nuevo (sale de
    app.integraciones.historial). Va sin tapar: los datos se tapan al mandárselo a Gemini."""

    quien: str  # "cliente", "bot" o "administrador"
    texto: str
    opciones: tuple[str, ...] = ()  # los botones que traía, si era del bot


@dataclass(frozen=True)
class Contexto:
    """Todo lo que Gemini necesita, además del mensaje, para entenderlo y contestar."""

    catalogo: Catalogo
    ficha: str = ""  # lo único que sabe del negocio (app.bot.redactor.ficha_completa)
    antes: tuple[Anterior, ...] = ()  # lo último que se dijeron
    en_que_va: str = ""  # qué le está preguntando el bot ("" si nada)
    # Lo que el bot diría con un texto fijo ("saludo", "despedida", "felicitacion"):
    # Gemini lo dice con sus palabras, con la misma idea (app.bot.textos.CON_SUS_PALABRAS).
    guias: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class Interpretacion:
    """Lo que entendió Gemini y, si toca conversar, lo que escribió (todavía sin revisar)."""

    intencion: Intencion
    vehiculo: str | None = None  # uno del catálogo, CARRO u OTRO_VEHICULO
    servicio: str | None = None  # uno del catálogo
    si_o_no: str | None = None  # SI o NO, si contesta una pregunta de sí o no del bot
    respuesta: str = ""  # lo que escribió para el cliente; "" si contesta el sistema
    sabe: bool = True  # en una pregunta: False si la ficha no tiene el dato


class Interprete(Protocol):
    """Quien lee lo que el cliente escribe con sus palabras; en el bot de verdad, Gemini.

    Como una interfaz de Java: el bot solo sabe que tiene `interpretar`. Si no responde a
    tiempo o falla, devuelve None, anota por qué en `ultimo_problema` y el bot sigue sin
    él (con los botones y unas palabras que reconoce solo).
    """

    ultimo_problema: str | None

    def interpretar(self, mensaje: str, contexto: Contexto) -> Interpretacion | None: ...


def leer_respuesta(datos: Any, catalogo: Catalogo) -> Interpretacion | None:
    """Lo que contestó Gemini, revisado: lo que no está en la lista no se cree."""
    if not isinstance(datos, dict):
        return None
    try:
        intencion = Intencion(datos.get("intencion"))
    except ValueError:
        return None
    vehiculo = datos.get("vehiculo")
    servicio = datos.get("servicio")
    si_o_no = datos.get("si_o_no")
    respuesta = datos.get("respuesta")
    sabe = datos.get("sabe")
    validos = (*catalogo.vehiculos, CARRO, OTRO_VEHICULO)
    return Interpretacion(
        intencion,
        vehiculo=vehiculo if vehiculo in validos else None,
        servicio=servicio if servicio in catalogo.servicios else None,
        si_o_no=si_o_no if si_o_no in (SI, NO) else None,
        respuesta=respuesta.strip() if isinstance(respuesta, str) else "",
        sabe=sabe if isinstance(sabe, bool) else True,
    )


# --- Tapar los datos personales ---------------------------------------------------------

_CORREO = re.compile(r"\S+@\S+\.\w+")
# Placas de carro (ABC123) y de moto (ABC12D), con o sin un espacio o guion en medio.
_PLACA = re.compile(r"\b[A-Za-z]{3}[\s-]?\d{2}[0-9A-Za-z]\b")
# Un número largo (con espacios, puntos o guiones): un celular o un documento. Los
# precios hasta 999.999 se dejan, porque no son de nadie.
_NUMERO = re.compile(r"\+?\d[\d\s.\-]{6,}\d")
LARGO_MAXIMO = 500  # un mensaje más largo se corta: para entender qué quiere, basta


def ocultar_datos(mensaje: str) -> str:
    """ "Mi placa es ABC123, llámame al 300 123 4567" ->
    "Mi placa es [placa], llámame al [número]" """
    texto = _CORREO.sub("[correo]", mensaje)
    texto = _PLACA.sub("[placa]", texto)
    texto = _NUMERO.sub("[número]", texto)
    return texto[:LARGO_MAXIMO]
