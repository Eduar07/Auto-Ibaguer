"""Leer números que escribió una persona (en un formulario o en la dirección) sin que el
servidor se caiga.

Tres trampas que aquí se evitan:
- "²" y "³" son dígitos para Python (str.isdigit), pero int("²") falla: se usa str.isdecimal.
- int() no convierte textos de más de 4.300 cifras (Python lo prohíbe para que nadie tumbe el
  servidor con un número gigante): antes de convertir se miran las cifras.
- PostgreSQL guarda los números en columnas con tope (INTEGER llega a 2.147.483.647): un
  número con más cifras de las que existen en el negocio no se pasa a la base de datos.
"""

import re

# Un entero escrito (un número de ticket, unos minutos, el id de algo) tiene a lo sumo estas
# cifras: así cabe en un INTEGER de PostgreSQL. Más que eso no es un número de verdad.
CIFRAS_DE_UN_ENTERO = 9

# Una cantidad de plata con más cifras que esto es un error al escribir: se lee como DEMASIADO,
# que pasa de cualquier tope, y la regla del negocio dice que se revisen los ceros.
CIFRAS_DE_LA_PLATA = 15
DEMASIADO = 10**CIFRAS_DE_LA_PLATA


def entero(texto: str) -> int | None:
    """ "12" -> 12. Vacío, "abc", "-3", "1.5", "²" o un número de más de 9 cifras -> None."""
    if not texto.isdecimal() or len(texto) > CIFRAS_DE_UN_ENTERO:
        return None
    return int(texto)


def pesos(texto: str) -> int | None:
    """ "$40.000" o "40000" -> 40000; sin cifras -> None (por ejemplo, para cobrar el precio
    del menú). Con más de 15 cifras, DEMASIADO: la regla dirá que es demasiado alto."""
    cifras = re.sub(r"\D", "", texto)
    if not cifras:
        return None
    if len(cifras.lstrip("0")) > CIFRAS_DE_LA_PLATA:
        return DEMASIADO
    return int(cifras)
