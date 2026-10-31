"""Cómo se escriben pesos, horas, fechas y teléfonos, a la colombiana."""

from datetime import date, datetime, time

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


def telefono_oculto(telefono: str | None) -> str:
    """ "573214564410" -> "321 *** 4410": suficiente para reconocerlo, sin mostrarlo entero."""
    if not telefono:
        return "Sin celular"
    local = telefono.removeprefix("57") if len(telefono) == 12 else telefono
    return f"{local[:3]} *** {local[-4:]}"
