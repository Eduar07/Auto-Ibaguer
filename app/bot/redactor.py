"""Lo que el bot le pide a un redactor (Gemini) cuando el mensaje no es un pedido: una
pregunta sobre el negocio ("¿reciben Nequi?") o charla ("estoy cansado", "te amo").

Es lo único que se redacta: los precios, la fila y los turnos salen del sistema. El
redactor escribe solo con la ficha del negocio (ficha_completa) y con lo último que se
dijeron (para seguir la conversación sin repetirse). Antes de mandarle la respuesta al
cliente, el bot la revisa con reglas fijas (revisar). Si no las cumple, o el redactor no
responde, el bot usa un texto fijo.
"""

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from app import formato
from app.bot.interprete import Anterior, Catalogo, Intencion
from app.catalogo.parametros import Parametros

LARGO_MAXIMO = 400  # caracteres: se lee de un vistazo en WhatsApp
EMOJIS_MAXIMOS = 2  # con más, se le quitan todos: emojis sí, pero con moderación


@dataclass(frozen=True)
class Respuesta:
    texto: str
    sabe: bool = True  # False: era una pregunta y la ficha no tiene el dato


class Redactor(Protocol):
    """Quien escribe la respuesta a una pregunta o a la charla; en el hito 7b, Gemini.

    Como una interfaz de Java: el bot solo sabe que tiene `redactar`. Si no responde a
    tiempo o falla, devuelve None y el bot usa un texto fijo. `antes` es lo último que se
    dijeron, para que la respuesta siga la conversación.
    """

    def redactar(
        self, tipo: Intencion, mensaje: str, ficha: str, antes: Sequence[Anterior] = ()
    ) -> Respuesta | None: ...

    def con_sus_palabras(
        self, texto: str, mensaje: str, ficha: str, antes: Sequence[Anterior] = ()
    ) -> str | None:
        """Un texto fijo del bot (un saludo, una despedida) dicho con sus palabras según la
        conversación: la misma idea, para no repetirse. None si no responde."""
        ...


def ficha_completa(parametros: Parametros, catalogo: Catalogo) -> str:
    """Todo lo que el redactor sabe del negocio: lo que dice el sistema (horario, teléfono,
    vehículos y servicios, sin precios) y la ficha que escribió el administrador."""
    horario = formato.horario(
        parametros.dias_atencion, parametros.hora_apertura, parametros.hora_cierre
    )
    lineas = [
        f"Nombre: {parametros.nombre_negocio}.",
        f"Horario: da turnos {horario}; los vehículos que ya están en la fila a la hora de "
        "cierre se atienden igual.",
        f"Teléfono: {formato.telefono(parametros.telefono_contacto)}.",
        f"Vehículos que lava: {', '.join(catalogo.vehiculos)}.",
        f"Servicios: {', '.join(catalogo.servicios)}. Los precios y lo que incluye cada uno "
        "están en el menú del bot, en Servicios y precios.",
    ]
    ficha = parametros.ficha_negocio.strip()
    if ficha:
        lineas.append(ficha)
    return "\n".join(lineas)


# --- Revisar lo que escribió ------------------------------------------------------------------

_ENLACE = re.compile(r"https?://\S+|www\.\S+|\b[\w-]+\.(?:com|co|net|org|app|me)\b", re.I)
_NUMERO = re.compile(r"\d(?:[\d.,\s-]*\d)?")
_HORA = re.compile(r"\b\d{1,2}:\d{2}\b")  # "8:15": una hora que no esté en la ficha, no
_EMOJI = re.compile(r"[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F\u200D]")
# Los que se ven como un emoji (sin contar el tono de piel ni los que los unen).
_EMOJI_QUE_SE_VE = re.compile(
    r"[\U0001F000-\U0001F3FA\U0001F400-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF]"
)
_TAPADOS = ("[placa]", "[número]", "[numero]", "[correo]")
# Lo que no escribe si la ficha no lo dice: ni precios con palabras ("cuarenta mil pesos",
# "40 lucas") ni nada que el negocio no ofrece.
_PROMESAS = re.compile(
    r"\b(?:gratis|descuent|promoci|ofert|regal|obsequi|cortesi|rebaj|bono\b|2x1|minuto|"
    r"pesos\b|lucas?\b)"
)
# Es el asistente virtual: nunca dice que es una persona ("no soy una persona" sí se vale).
_DICE_SER_PERSONA = re.compile(
    r"(?<!no )\bsoy (?:una persona|un humano|humano|humana)\b|"
    r"\bno soy (?:un bot|un robot|una maquina|un asistente virtual)\b"
)


def _sin_tildes(texto: str) -> str:
    descompuesto = unicodedata.normalize("NFD", texto.lower())
    return "".join(letra for letra in descompuesto if unicodedata.category(letra) != "Mn")


def _numeros(texto: str) -> set[str]:
    """Los números de 3 cifras o más, sin puntos ni espacios: "40.000" -> "40000"."""
    cifras = (re.sub(r"\D", "", numero) for numero in _NUMERO.findall(texto))
    return {numero for numero in cifras if len(numero) >= 3}


def revisar(texto: str, ficha: str) -> str | None:
    """La respuesta lista para mandar, o None si rompe una regla (el bot usa un texto fijo).

    Estas reglas no dependen de que Gemini obedezca: un precio, un descuento, un enlace, una
    hora o un número que no estén en la ficha no salen, diga lo que diga el mensaje del
    cliente. `ficha` es todo lo que se puede decir: la ficha del negocio y, si Gemini está
    diciendo un texto fijo con sus palabras, ese texto. Emojis, hasta dos.
    """
    if len(_EMOJI_QUE_SE_VE.findall(texto)) > EMOJIS_MAXIMOS:
        texto = _EMOJI.sub("", texto)
    limpio = " ".join(texto.split())
    if not limpio or not _EMOJI.sub("", limpio).strip() or len(limpio) > LARGO_MAXIMO:
        return None
    normal, en_la_ficha = _sin_tildes(limpio), _sin_tildes(ficha)
    if any(tapado in normal for tapado in _TAPADOS):
        return None  # repitió un dato del cliente que se había tapado
    if any(enlace.lower() not in ficha.lower() for enlace in _ENLACE.findall(limpio)):
        return None
    if any(simbolo in limpio and simbolo not in ficha for simbolo in ("$", "%")):
        return None
    if not _numeros(limpio) <= _numeros(ficha):
        return None  # un precio, un teléfono o una cifra que se inventó
    if any(hora not in ficha for hora in _HORA.findall(limpio)):
        return None  # una hora que no sale de la ficha ("te esperamos a las 8:15")
    if any(promesa not in en_la_ficha for promesa in _PROMESAS.findall(normal)):
        return None
    if _DICE_SER_PERSONA.search(normal):
        return None
    return limpio


# --- Mensajes graves --------------------------------------------------------------------------

# Dice que quiere morir o hacerse daño. Se reconoce sin Gemini: aquí el bot no improvisa.
AYUDA_URGENTE = (
    "suicid",
    "quitarme la vida",
    "acabar con mi vida",
    "no quiero vivir",
    "no quiero seguir viviendo",
    "no vale la pena vivir",
    "quiero morir",
    "quiero morirme",
    "me voy a matar",
    "matarme",
    "hacerme dano",
)
# "Me quiero morir de la risa" es una forma de hablar, no un pedido de ayuda.
_FORMAS_DE_HABLAR = re.compile(
    r"^(?:me)?\s*(?:de la |de |del )(?:risa|pena|verguenza|hambre|calor|sueno|amor|"
    r"aburrimiento|emocion|felicidad)"
)
_NEGACIONES = frozenset({"no", "nunca", "jamas"})


def pide_ayuda_urgente(normal: str) -> bool:
    """`normal` es el mensaje sin tildes y en minúscula (conversacion.normalizar)."""
    for frase in AYUDA_URGENTE:
        inicio = normal.find(frase)
        while inicio != -1:
            antes = normal[:inicio].split()[-2:]
            despues = normal[inicio + len(frase) :]
            negado = bool(_NEGACIONES & set(antes)) and not frase.startswith("no ")
            if not negado and not _FORMAS_DE_HABLAR.match(despues):
                return True
            inicio = normal.find(frase, inicio + 1)
    return False
