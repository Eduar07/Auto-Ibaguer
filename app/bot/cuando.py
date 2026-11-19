"""Cuándo quiere venir el cliente, dicho con sus palabras.

"mañana a las 10", "el sábado en la tarde", "hoy a las 3:30 pm", "pasado mañana", "el 25
de noviembre", "ahorita"... Lo entiende el bot solo, sin Gemini: así una reserva o un cambio
de hora nunca dependen de que Gemini responda.

Lo que no se entiende queda vacío y el bot le muestra las opciones. Una hora sin "a.m." ni
"p.m." se entiende como la diría alguien en Colombia para un lavadero: de 6 a 11, en la
mañana; de 12 a 5, en la tarde ("a las 3" son las 3 p.m.).

Lo que dice que NO puede no cuenta: en "ya no puedo mañana, mejor el sábado a las 10" el día
es el sábado. Si solo dice lo que no puede ("ya no puedo hoy"), no hay día: el bot le
muestra los días para que elija. Equivocarse de día sería peor que preguntar.
"""

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, time, timedelta

DIAS = {
    "lunes": 0,
    "martes": 1,
    "miercoles": 2,
    "jueves": 3,
    "viernes": 4,
    "sabado": 5,
    "domingo": 6,
}
MESES = {
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "septiembre": 9,
    "setiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
}
# Partes del día: desde qué hora se le muestran los horarios.
FRANJAS = {"manana": time(6), "tarde": time(12), "noche": time(18)}
YA = ("ahorita", "ahora mismo", "ya mismo", "lo antes posible", "lo mas pronto", "de una vez")


@dataclass(frozen=True)
class Dicho:
    """Lo que dijo de cuándo viene. Cualquiera puede faltar."""

    fecha: date | None = None
    hora: time | None = None
    franja: str | None = None  # "manana", "tarde" o "noche", si no dijo la hora
    ya: bool = False  # "ahorita", "lo antes posible"

    def como_dato(self) -> dict[str, str | bool]:
        """Para guardarlo en la conversación (JSON)."""
        dato: dict[str, str | bool] = {}
        if self.fecha is not None:
            dato["fecha"] = self.fecha.isoformat()
        if self.hora is not None:
            dato["hora"] = self.hora.strftime("%H:%M")
        if self.franja is not None:
            dato["franja"] = self.franja
        if self.ya:
            dato["ya"] = True
        return dato

    @classmethod
    def desde_dato(cls, dato: object) -> "Dicho | None":
        if not isinstance(dato, dict):
            return None
        try:
            fecha = date.fromisoformat(dato["fecha"]) if dato.get("fecha") else None
            hora = time.fromisoformat(dato["hora"]) if dato.get("hora") else None
        except (TypeError, ValueError):
            return None
        franja = dato.get("franja") if dato.get("franja") in FRANJAS else None
        return cls(fecha, hora, franja, bool(dato.get("ya")))


# Lo que parte lo que dijo en pedazos: la puntuación y estas palabras. "no puedo hoy, mejor
# mañana" son dos pedazos; el primero dice lo que no puede.
_PEDAZOS = re.compile(r"[,;.!?¡¿\n]+|\b(mejor|sino|pero|entonces|que|o)\b")
# Después de estas, lo que dice corrige lo de antes: "el jueves, mejor el viernes".
CORRIGE = frozenset({"mejor", "sino", "pero"})
NIEGA = frozenset({"no", "ni"})


def _normal(escrito: str) -> str:
    """Minúsculas, sin tildes, "a.m." como "am" y "10.30" como "10:30". Deja la
    puntuación: con ella se parte lo que dijo."""
    texto = unicodedata.normalize("NFKD", escrito.lower())
    texto = "".join(letra for letra in texto if not unicodedata.combining(letra))
    # "6pm", "6:30p.m.", "6:30 p. m." -> "6 pm", "6:30 pm".
    texto = re.sub(r"(?<=[\d\s])([ap])\s*\.?\s*m\b\.?", r" \1m", texto)
    return re.sub(r"\b(\d{1,2})\.(\d{2})\b(?!\.\d)", r"\1:\2", texto)


def _limpio(pedazo: str) -> str:
    """Solo letras, números, los dos puntos de "10:30" y la barra de "25/11"."""
    return " ".join(re.sub(r"[^a-z0-9:/]+", " ", pedazo).split())


def _pedazos(escrito: str) -> list[str]:
    """Lo que dijo, por pedazos, en el orden en que conviene leerlos: primero lo que
    corrige (el último "mejor..." gana), después lo demás en el orden en que lo dijo. Lo
    que dice que no puede ("no puedo hoy", "mañana no") se queda por fuera."""
    trozos = _PEDAZOS.split(_normal(escrito))
    pedazos = [(_limpio(trozos[0]), False)]
    for separador, trozo in zip(trozos[1::2], trozos[2::2], strict=True):
        pedazos.append((_limpio(trozo), separador in CORRIGE))
    validos = [(p, corrige) for p, corrige in pedazos if p and not NIEGA & set(p.split())]
    correcciones = [p for p, corrige in reversed(validos) if corrige]
    return correcciones + [p for p, corrige in validos if not corrige]


def entender(escrito: str, hoy: date) -> Dicho | None:
    """Lo que dijo de cuándo viene, o None si no dijo nada de eso."""
    pedazos = _pedazos(escrito)
    ya = any(frase in pedazo for pedazo in pedazos for frase in YA)
    fecha = next((f for p in pedazos if (f := _fecha(p, hoy)) is not None), None)
    hora = next((h for p in pedazos if (h := _hora(p)) is not None), None)
    franja = None
    if hora is None:
        franja = next((f for p in pedazos if (f := _franja(p)) is not None), None)
    if fecha is None and hora is None and franja is None and not ya:
        return None
    return Dicho(fecha=fecha, hora=hora, franja=franja, ya=ya and fecha in (None, hoy))


def _fecha(texto: str, hoy: date) -> date | None:
    palabras = texto.split()
    if "pasado manana" in texto:
        return hoy + timedelta(days=2)
    # "mañana" es el día de mañana, salvo "en la mañana", "esta mañana", "de la mañana".
    for i, palabra in enumerate(palabras):
        if palabra == "manana" and (i == 0 or palabras[i - 1] not in ("la", "esta", "pasado")):
            return hoy + timedelta(days=1)
    numerica = re.search(r"\b(\d{1,2})/(\d{1,2})\b", texto)
    if numerica:
        return _dia_del_mes(int(numerica.group(1)), int(numerica.group(2)), hoy)
    con_mes = re.search(r"\b(\d{1,2}) de (" + "|".join(MESES) + r")\b", texto)
    if con_mes:
        return _dia_del_mes(int(con_mes.group(1)), MESES[con_mes.group(2)], hoy)
    for nombre, dia in DIAS.items():
        if nombre in palabras:
            adelante = (dia - hoy.weekday()) % 7
            if adelante == 0 and ("proximo" in palabras or "siguiente" in palabras):
                adelante = 7
            return hoy + timedelta(days=adelante)
    solo_dia = re.search(r"\bel (\d{1,2})\b(?! y)(?!:)", texto)
    if solo_dia and not re.search(r"\bel \d{1,2} (am|pm)\b", texto):
        dia = int(solo_dia.group(1))
        mes = hoy.month if dia >= hoy.day else hoy.month % 12 + 1
        return _dia_del_mes(dia, mes, hoy)
    if re.search(r"\b(hoy|ahorita|esta tarde|esta noche|esta manana)\b", texto):
        return hoy
    return None


def _dia_del_mes(dia: int, mes: int, hoy: date) -> date | None:
    """El próximo `dia` de `mes` (este año o, si ya pasó, el siguiente)."""
    for anio in (hoy.year, hoy.year + 1):
        try:
            fecha = date(anio, mes, dia)
        except ValueError:
            return None
        if fecha >= hoy:
            return fecha
    return None


def _hora(texto: str) -> time | None:
    if re.search(r"\b(medio dia|mediodia)\b", texto):
        return time(12)
    con_minutos = re.search(r"\b(\d{1,2}):(\d{2})(?: (am|pm))?\b", texto)
    if con_minutos:
        horas, minutos, sufijo = con_minutos.groups()
        return _armar(int(horas), int(minutos), sufijo, texto)
    con_sufijo = re.search(r"\b(\d{1,2}) (am|pm)\b", texto)
    if con_sufijo:
        return _armar(int(con_sufijo.group(1)), 0, con_sufijo.group(2), texto)
    a_las = re.search(
        r"\b(?:a las|a la|las|tipo|como a las|eso de las) (\d{1,2})(?: y (media|cuarto))?\b", texto
    )
    if a_las:
        minutos = {"media": 30, "cuarto": 15}.get(a_las.group(2) or "", 0)
        return _armar(int(a_las.group(1)), minutos, None, texto)
    return None


def _armar(horas: int, minutos: int, sufijo: str | None, texto: str) -> time | None:
    if not (0 <= horas <= 23 and 0 <= minutos <= 59):
        return None
    tarde = sufijo == "pm" or re.search(r"\b(de|en|por) la (tarde|noche)\b", texto)
    manana = sufijo == "am" or re.search(r"\b(de|en|por) la manana\b", texto)
    if horas <= 12:
        if tarde and horas < 12:
            horas += 12
        elif manana and horas == 12:
            horas = 0
        elif not manana and 1 <= horas <= 5:
            horas += 12  # "a las 3": las 3 de la tarde
    return time(horas, minutos)


def _franja(texto: str) -> str | None:
    encontrada = re.search(r"\b(?:en|por|de|esta) la (manana|tarde|noche)\b", texto)
    if encontrada:
        return encontrada.group(1)
    encontrada = re.search(r"\besta (tarde|noche)\b", texto)
    return encontrada.group(1) if encontrada else None
