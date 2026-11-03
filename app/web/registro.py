"""Lo que el servidor anota de cada petición (el registro que se ve en los logs de Render).

Cada petición deja una línea como  GET /clientes/lista HTTP/1.1 200 OK. Lo que va después
del "?" en la dirección no se anota: ahí viajan las búsquedas del dashboard (un nombre,
un celular, una placa) y la palabra secreta con la que Meta verifica el webhook.
"""

import logging


class SinParametrosEnElRegistro(logging.Filter):
    """Recorta la dirección de cada línea del registro de peticiones de uvicorn.

    uvicorn anota (cliente, método, dirección, versión de HTTP, estado); aquí solo se
    cambia la dirección.
    """

    def filter(self, registro: logging.LogRecord) -> bool:
        datos = registro.args
        if isinstance(datos, tuple) and len(datos) == 5:
            cliente, metodo, direccion, version, estado = datos
            if isinstance(direccion, str) and "?" in direccion:
                registro.args = (cliente, metodo, direccion.partition("?")[0], version, estado)
        return True


def proteger_el_registro_de_peticiones() -> None:
    """Le pone el filtro al registro de peticiones de uvicorn, una sola vez."""
    peticiones = logging.getLogger("uvicorn.access")
    if not any(isinstance(filtro, SinParametrosEnElRegistro) for filtro in peticiones.filters):
        peticiones.addFilter(SinParametrosEnElRegistro())
