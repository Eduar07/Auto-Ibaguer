"""El error base de las reglas del negocio."""


class ReglaDelNegocio(Exception):
    """Algo que las reglas del lavadero no permiten.

    No es una falla del programa: es una respuesta. El mensaje está escrito para la
    persona, así que el bot o el dashboard lo pueden mostrar tal cual.
    """
