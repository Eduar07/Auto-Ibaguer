"""Cómo se escriben pesos y horas en los mensajes, a la colombiana."""

from datetime import datetime, time


def pesos(valor: int) -> str:
    """40000 -> "$40.000" """
    return f"${valor:,}".replace(",", ".")


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
