"""Los periodos de un balance, iguales para los dos negocios: un día, una semana (de lunes a
domingo), un mes o los días que se escojan (hasta 92). Cada cobro cuenta el día en que se hizo,
en la hora de Bogotá.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import String, func, literal

from app import formato, tiempo

TIPOS_DE_PERIODO = ("dia", "semana", "mes")

# Un rango a escoger llega hasta unos tres meses: así la gráfica día por día se sigue leyendo
# en el celular y el día de la semana tiene varias muestras de cada uno.
DIAS_MAXIMOS_DEL_RANGO = 92


# --- El periodo ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Periodo:
    tipo: str  # "dia", "semana" (de lunes a domingo), "mes" o "rango" (los días que se escojan)
    desde: date
    hasta: date  # incluido

    @property
    def dias(self) -> list[date]:
        return [self.desde + timedelta(days=n) for n in range((self.hasta - self.desde).days + 1)]

    @property
    def cuantos_dias(self) -> int:
        return (self.hasta - self.desde).days + 1

    def anterior(self) -> "Periodo":
        if self.tipo == "rango":  # los mismos días, justo antes
            return Periodo(
                "rango",
                self.desde - timedelta(days=self.cuantos_dias),
                self.desde - timedelta(days=1),
            )
        return periodo(self.tipo, self.desde - timedelta(days=1))

    def siguiente(self) -> "Periodo":
        if self.tipo == "rango":
            return Periodo(
                "rango",
                self.hasta + timedelta(days=1),
                self.hasta + timedelta(days=self.cuantos_dias),
            )
        return periodo(self.tipo, self.hasta + timedelta(days=1))

    def contiene(self, dia: date) -> bool:
        return self.desde <= dia <= self.hasta

    @property
    def titulo(self) -> str:
        """ "Martes 12 de noviembre de 2026", "Semana del 9 al 15 de noviembre de 2026" o
        "Noviembre de 2026"."""
        if self.tipo == "mes":
            return f"{formato.MESES[self.desde.month - 1].capitalize()} de {self.desde.year}"
        if self.tipo == "semana":
            return f"Semana {_del_al(self.desde, self.hasta)}"
        if self.tipo == "rango":
            return f"Del {_del_al(self.desde, self.hasta)[4:]}"
        return f"{formato.fecha_larga(self.desde)} de {self.desde.year}"

    def nombre_del_anterior(self, hoy: date) -> str:
        """Con qué se compara: "Ayer (todo el día)" si es el de hoy (que va por la mitad), o
        "El día anterior" si ya pasó."""
        if self.tipo == "rango":
            return f"Los {self.cuantos_dias} días anteriores"
        if self.contiene(hoy):
            return {
                "dia": "Ayer (todo el día)",
                "semana": "La semana pasada (completa)",
                "mes": "El mes pasado (completo)",
            }[self.tipo]
        return {"dia": "El día anterior", "semana": "La semana anterior", "mes": "El mes anterior"}[
            self.tipo
        ]


def _del_al(desde: date, hasta: date) -> str:
    """ "del 9 al 15 de noviembre de 2026", "del 30 de noviembre al 6 de diciembre de 2026" """
    if desde.year != hasta.year:
        return f"del {formato.fecha(desde)} al {formato.fecha(hasta)}"
    if desde.month != hasta.month:
        return f"del {desde.day} de {formato.MESES[desde.month - 1]} al {formato.fecha(hasta)}"
    return f"del {desde.day} al {formato.fecha(hasta)}"


def periodo(tipo: str, dia: date) -> Periodo:
    """El día, la semana (de lunes a domingo) o el mes que contiene `dia`."""
    if tipo == "semana":
        lunes = dia - timedelta(days=dia.weekday())
        return Periodo("semana", lunes, lunes + timedelta(days=6))
    if tipo == "mes":
        primero = dia.replace(day=1)
        siguiente = (primero + timedelta(days=32)).replace(day=1)
        return Periodo("mes", primero, siguiente - timedelta(days=1))
    return Periodo("dia", dia, dia)


def rango(desde: date, hasta: date) -> Periodo:
    """Los días que se escojan, hasta 92: si vienen al revés se voltean, y si son más, se
    cuentan los 92 que terminan en `hasta` (lo más reciente es lo que se quiere ver)."""
    if hasta < desde:
        desde, hasta = hasta, desde
    desde = max(desde, hasta - timedelta(days=DIAS_MAXIMOS_DEL_RANGO - 1))
    return Periodo("rango", desde, hasta)


def limites(p: Periodo) -> tuple[datetime, datetime]:
    """Desde la medianoche del primer día hasta la medianoche después del último, en Bogotá."""
    zona = tiempo.zona_horaria()
    inicio = datetime.combine(p.desde, datetime.min.time(), zona)
    fin = datetime.combine(p.hasta + timedelta(days=1), datetime.min.time(), zona)
    return inicio, fin


def dia_en_bogota(columna: Any) -> Any:
    """El día (en Bogotá) de una fecha y hora guardada con zona horaria, para una consulta.

    La zona va escrita en la consulta (no como parámetro): así el SELECT y el GROUP BY
    tienen la misma expresión y PostgreSQL acepta agrupar por ella."""
    zona = literal(tiempo.zona_horaria().key, String, literal_execute=True)
    return func.date(func.timezone(zona, columna))
