"""Lo que el bot le pide a un intérprete (Gemini) cuando el cliente escribe con sus palabras.

El intérprete solo dice qué quiere el cliente, de una lista cerrada, y de qué vehículo o
servicio habla. Nunca redacta lo que contesta el bot ni inventa precios, turnos o
tiempos: eso sale del sistema, como cuando el cliente toca un botón. Lo único que se
redacta son las respuestas a una pregunta sobre el negocio o a la charla, y eso lo hace
otro (app.bot.redactor), solo con la ficha del negocio.

Antes de mandarle el mensaje se le tapan los datos personales que se reconocen (placas,
celulares y correos): el intérprete no los necesita para entender qué quiere el cliente.
"""

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol


class Intencion(StrEnum):
    PEDIR_TURNO = "pedir_turno"
    VER_FILA = "ver_fila"
    PRECIOS = "precios"
    MI_TURNO = "mi_turno"
    CANCELAR = "cancelar"
    PICO_Y_PLACA = "pico_y_placa"
    MIS_LAVADAS = "mis_lavadas"  # cuántas lleva con el lavadero
    QUEJA = "queja"  # se queja de algo que ya pasó
    FELICITACION = "felicitacion"
    HABLAR_CON_PERSONA = "hablar_con_persona"  # quiere que le responda alguien, no el bot
    PREGUNTA = "pregunta"  # sobre el negocio: horarios, pagos, dirección (app.bot.redactor)
    CHARLA = "charla"  # no pide nada: saluda, cuenta cómo se siente, bromea (app.bot.redactor)
    CRISIS = "crisis"  # dice en serio que quiere morir o hacerse daño: un texto fijo de ayuda
    OTRO = "otro"  # no se entiende qué quiere


# Cuando el cliente dice "carro" sin decir si es automóvil o camioneta: el bot pregunta.
CARRO = "carro"
# Lo que responde el intérprete cuando el cliente no nombra un vehículo o un servicio.
NINGUNO = "ninguno"


@dataclass(frozen=True)
class Catalogo:
    """Los vehículos y servicios que existen: el intérprete solo puede elegir entre estos."""

    vehiculos: tuple[str, ...]  # "Automóvil", "Camioneta", "Moto"
    servicios: tuple[str, ...]  # "Básico", "Encerado"...


@dataclass(frozen=True)
class Interpretacion:
    intencion: Intencion
    vehiculo: str | None = None  # uno del catálogo, o CARRO
    servicio: str | None = None  # uno del catálogo


class Interprete(Protocol):
    """Quien entiende el texto libre; en el hito 7, Gemini.

    Como una interfaz de Java: el bot solo sabe que tiene `interpretar`. Si el
    intérprete no responde a tiempo o falla, devuelve None y el bot sigue con botones.
    """

    def interpretar(self, mensaje: str, catalogo: Catalogo) -> Interpretacion | None: ...


def leer_respuesta(datos: Any, catalogo: Catalogo) -> Interpretacion | None:
    """La respuesta del intérprete, revisada: lo que no está en la lista no se cree."""
    if not isinstance(datos, dict):
        return None
    try:
        intencion = Intencion(datos.get("intencion"))
    except ValueError:
        return None
    vehiculo = datos.get("vehiculo")
    servicio = datos.get("servicio")
    return Interpretacion(
        intencion,
        vehiculo=vehiculo if vehiculo in catalogo.vehiculos or vehiculo == CARRO else None,
        servicio=servicio if servicio in catalogo.servicios else None,
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
