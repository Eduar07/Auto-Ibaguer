"""Lo que Gemini sabe del negocio (la ficha) y las reglas fijas con las que el bot revisa
lo que escribe antes de mandarlo.

Gemini solo escribe la conversación (saludos, despedidas, preguntas sobre el negocio,
charla): los precios, la fila y los turnos salen del sistema. Escribe solo con la ficha
(ficha_completa) y con lo último que se dijeron. Antes de mandarle la respuesta al
cliente, el bot la revisa (revisar): si rompe una regla, usa un texto fijo. Es como el
corrector de un periódico: no importa qué tan bien escriba el periodista, un precio que no
está en la ficha no sale.
"""

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass

from app import formato
from app.bot.interprete import Catalogo
from app.catalogo.parametros import Parametros

LARGO_MAXIMO = 400  # caracteres: se lee de un vistazo en WhatsApp
EMOJIS_MAXIMOS = 1  # con más, se le quitan todos: un emoji sí, pero con moderación


def _y(cosas: Sequence[str]) -> str:
    """("Automóvil", "Camioneta", "Moto") -> "Automóvil, Camioneta y Moto"."""
    return cosas[0] if len(cosas) == 1 else ", ".join(cosas[:-1]) + " y " + cosas[-1]


def _sin_la_pregunta(aviso: str) -> str:
    """El aviso de datos sin el "¿Aceptas?" del final: en la ficha es información."""
    return re.sub(r"\s*¿[^?]*\?\s*$", "", aviso.strip())


def ficha_completa(
    parametros: Parametros, catalogo: Catalogo, servicios: Sequence[str] = ()
) -> str:
    """Todo lo que Gemini sabe del negocio: lo que dice el sistema y la ficha que escribió
    el administrador. Del sistema: el horario, cómo funcionan los turnos, el teléfono, qué
    vehículos lava, qué incluye cada servicio (sin precios: esos los da el bot) y qué se
    hace con los datos personales. `servicios`: una línea por fila (servicios_de_la_ficha).
    """
    horario = formato.horario(
        parametros.dias_atencion, parametros.hora_apertura, parametros.hora_cierre
    )
    abre = [dia for numero, dia in enumerate(formato.DIAS, 1) if numero in parametros.dias_atencion]
    cierra = [dia for dia in formato.DIAS if dia not in abre]
    dias = f"abre {_y(abre)}" + (f"; no abre {_y(cierra)}" if cierra else "")
    minutos = int(parametros.gracia.total_seconds() // 60)
    lineas = [
        f"Nombre: {parametros.nombre_negocio}.",
        f"Horario: da turnos {horario} ({dias}). Los vehículos que ya están en la fila a "
        "la hora de cierre se atienden igual.",
        "Turnos: se piden por WhatsApp para el mismo día y se atienden en el orden de la "
        "fila; no se apartan para otro día ni para una hora exacta. Cuando llaman al "
        f"cliente, tiene {minutos} minutos para llegar; si no llega, pasa al final de la "
        "fila, y la segunda vez pierde el turno.",
        f"Teléfono: {formato.telefono(parametros.telefono_contacto)}.",
        f"Vehículos que lava: {_y(catalogo.vehiculos)}. Para otros vehículos no hay "
        "servicios ni precios.",
    ]
    if servicios:
        lineas.append("Servicios y qué incluyen (los precios los da el bot):")
        lineas.extend(f"- {linea}" for linea in servicios)
    else:
        lineas.append(f"Servicios: {_y(catalogo.servicios)}. Los precios los da el bot.")
    lineas.append(
        f"Datos personales: {_sin_la_pregunta(parametros.aviso_datos)} Si alguien que no es "
        "cliente deja una queja o una pregunta, su número solo se guarda si acepta que el "
        "administrador le responda."
    )
    ficha = parametros.ficha_negocio.strip()
    if ficha:
        lineas.append(f"Lo que escribió el administrador:\n{ficha}")
    return "\n".join(lineas)


# --- Revisar lo que escribió ------------------------------------------------------------------

_ENLACE = re.compile(r"https?://\S+|www\.\S+|\b[\w-]+\.(?:com|co|net|org|app|me)\b", re.I)
_NUMERO = re.compile(r"\d(?:[\d.,\s-]*\d)?")
_HORA = re.compile(r"\b\d{1,2}:\d{2}\b")  # "8:15": una hora que no esté en la ficha, no
# "15 minutos", "media hora": un tiempo de espera que no esté en la ficha, no.
_TIEMPO = re.compile(
    r"\b(?:\d{1,3}|media|una|dos|tres|cuatro|cinco|diez|quince|veinte|treinta)\s*"
    r"(?:min\b|minutos?\b|horas?\b)"
)
# Los días sí están todos en la ficha (los que abre y los que no); los festivos, no.
_FESTIVO = re.compile(r"\bfestivos?\b")
_EMOJI = re.compile(r"[\U0001F000-\U0001FAFF☀-➿⬀-⯿️‍]")
# Los que se ven como un emoji (sin contar el tono de piel ni los que los unen).
_EMOJI_QUE_SE_VE = re.compile(r"[\U0001F000-\U0001F3FA\U0001F400-\U0001FAFF☀-➿⬀-⯿]")
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
# Lo que solo hace el sistema: Gemini nunca dice que apartó, confirmó o canceló un turno.
_DICE_QUE_HIZO = re.compile(
    r"\b(?:te|le|les) (?:aparte|reserve|separe|agende|cancele|confirme)\b|"
    r"\b(?:quedo|queda|esta|ya esta) (?:apartado|reservado|separado|agendado|cancelado|"
    r"confirmado)\b"
)


def _sin_tildes(texto: str) -> str:
    descompuesto = unicodedata.normalize("NFD", texto.lower())
    return "".join(letra for letra in descompuesto if unicodedata.category(letra) != "Mn")


def _numeros(texto: str) -> set[str]:
    """Los números de 3 cifras o más, sin puntos ni espacios: "40.000" -> "40000"."""
    cifras = (re.sub(r"\D", "", numero) for numero in _NUMERO.findall(texto))
    return {numero for numero in cifras if len(numero) >= 3}


@dataclass(frozen=True)
class Revision:
    """Lo que se puede mandar (o None) y, si no sirve, por qué: lo ve el administrador."""

    texto: str | None
    motivo: str | None = None


def revisar_con_motivo(texto: str, permitido: str) -> Revision:
    """La respuesta lista para mandar o, si rompe una regla, cuál (el bot usa un texto fijo).

    Estas reglas no dependen de que Gemini obedezca: un precio, un descuento, un enlace, una
    hora, un tiempo de espera o un número que no estén en lo permitido no salen,
    diga lo que diga el mensaje del cliente. `permitido` es todo lo que se puede decir: la
    ficha del negocio y los textos fijos que Gemini dice con sus palabras. Un emoji, como
    mucho.
    """
    if len(_EMOJI_QUE_SE_VE.findall(texto)) > EMOJIS_MAXIMOS:
        texto = _EMOJI.sub("", texto)
    limpio = " ".join(texto.split())
    if not limpio or not _EMOJI.sub("", limpio).strip():
        return Revision(None, "venía vacía.")
    if len(limpio) > LARGO_MAXIMO:
        return Revision(None, f"era muy larga (más de {LARGO_MAXIMO} caracteres).")
    normal, en_la_ficha = _sin_tildes(limpio), _sin_tildes(permitido)
    if any(tapado in normal for tapado in _TAPADOS):
        return Revision(None, "repetía un dato del cliente que se había tapado.")
    if any(enlace.lower() not in permitido.lower() for enlace in _ENLACE.findall(limpio)):
        return Revision(None, "traía un enlace que no está en la ficha.")
    if any(simbolo in limpio and simbolo not in permitido for simbolo in ("$", "%")):
        return Revision(None, "traía un precio o un porcentaje.")
    if not _numeros(limpio) <= _numeros(permitido):
        return Revision(None, "traía un número que no está en la ficha.")
    if any(hora not in permitido for hora in _HORA.findall(limpio)):
        return Revision(None, "traía una hora que no está en la ficha.")
    if any(tiempo not in en_la_ficha for tiempo in _TIEMPO.findall(normal)):
        return Revision(None, "traía un tiempo de espera que no está en la ficha.")
    if _FESTIVO.search(normal) and "festivo" not in en_la_ficha:
        return Revision(None, "hablaba de festivos y la ficha no dice nada de eso.")
    if any(promesa not in en_la_ficha for promesa in _PROMESAS.findall(normal)):
        return Revision(None, "prometía algo que la ficha no dice (gratis, descuentos...).")
    if _DICE_SER_PERSONA.search(normal):
        return Revision(None, "decía que es una persona.")
    if _DICE_QUE_HIZO.search(normal):
        return Revision(None, "decía que hizo algo con un turno (eso solo lo hace el bot).")
    return Revision(limpio)


def revisar(texto: str, permitido: str) -> str | None:
    """La respuesta lista para mandar, o None si rompe una regla (revisar_con_motivo)."""
    return revisar_con_motivo(texto, permitido).texto


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
