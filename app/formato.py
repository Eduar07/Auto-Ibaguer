"""Cómo se escriben pesos, horas, fechas y teléfonos, a la colombiana."""

from collections.abc import Iterable
from datetime import date, datetime, time, timedelta

DIAS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")
MESES = (
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
)


def miles(valor: int) -> str:
    """40000 -> "40.000" """
    return f"{valor:,}".replace(",", ".")


def pesos(valor: int) -> str:
    """40000 -> "$40.000" """
    return f"${miles(valor)}"


def hora(momento: time | datetime) -> str:
    """time(5, 30) -> "5:30 a.m."; time(19, 30) -> "7:30 p.m."

    A un `datetime` se le toma la hora tal como viene: conviértelo antes a la hora de
    Bogotá (app.tiempo.hora_local).
    """
    horas = momento.hour % 12 or 12
    sufijo = "a.m." if momento.hour < 12 else "p.m."
    return f"{horas}:{momento.minute:02d} {sufijo}"


def hora_con_articulo(momento: time | datetime) -> str:
    """ "las 8:15 a.m.", pero "la 1:15 p.m.", como se dice en español."""
    texto = hora(momento)
    return ("la " if texto.startswith("1:") else "las ") + texto


def fecha_larga(dia: date) -> str:
    """date(2026, 10, 31) -> "Sábado 31 de octubre" """
    return f"{DIAS[dia.weekday()].capitalize()} {dia.day} de {MESES[dia.month - 1]}"


def duracion(minutos: int) -> str:
    """45 -> "45 min"; 65 -> "1 h 05 min"; 120 -> "2 h" """
    minutos = max(0, minutos)
    horas, resto = divmod(minutos, 60)
    if horas == 0:
        return f"{resto} min"
    if resto == 0:
        return f"{horas} h"
    return f"{horas} h {resto:02d} min"


def dias_de_atencion(dias: Iterable[int]) -> str:
    """{1..7} -> "todos los días"; {1..6} -> "de lunes a sábado"; {1, 3, 6} -> "los lunes,
    miércoles y sábados". Usa 1 = lunes ... 7 = domingo."""
    elegidos = sorted(set(dias))
    if elegidos == list(range(1, 8)):
        return "todos los días"
    if not elegidos:
        return "ningún día"
    if len(elegidos) >= 3 and elegidos == list(range(elegidos[0], elegidos[-1] + 1)):
        return f"de {DIAS[elegidos[0] - 1]} a {DIAS[elegidos[-1] - 1]}"
    # Sábado y domingo cambian en plural; los demás días se escriben igual.
    plurales = [DIAS[dia - 1] + ("s" if dia >= 6 else "") for dia in elegidos]
    if len(plurales) == 1:
        return f"los {plurales[0]}"
    return "los " + ", ".join(plurales[:-1]) + " y " + plurales[-1]


def horario(dias: Iterable[int], apertura: time, cierre: time) -> str:
    """ "todos los días de 5:30 a.m. a 7:30 p.m.": cuándo da turnos el bot."""
    return f"{dias_de_atencion(dias)} de {hora(apertura)} a {hora(cierre)}"


def telefono(numero: str) -> str:
    """ "573227556850" -> "322 755 6850": como se dice un celular en Colombia."""
    local = numero.removeprefix("57") if len(numero) == 12 else numero
    if len(local) != 10:
        return local
    return f"{local[:3]} {local[3:6]} {local[6:]}"


def telefono_oculto(telefono: str | None) -> str:
    """ "573214564410" -> "321 *** 4410": suficiente para reconocerlo, sin mostrarlo entero."""
    if not telefono:
        return "Sin celular"
    local = telefono.removeprefix("57") if len(telefono) == 12 else telefono
    return f"{local[:3]} *** {local[-4:]}"


def fecha(dia: date) -> str:
    """date(2026, 10, 3) -> "3 de octubre de 2026" """
    return f"{dia.day} de {MESES[dia.month - 1]} de {dia.year}"


def dia_relativo(dia: date, hoy: date) -> str:
    """ "Hoy", "Ayer", "17 de octubre" o, si fue otro año, "17 de octubre de 2025"."""
    if dia == hoy:
        return "Hoy"
    if dia == hoy - timedelta(days=1):
        return "Ayer"
    if dia.year == hoy.year:
        return f"{dia.day} de {MESES[dia.month - 1]}"
    return fecha(dia)
