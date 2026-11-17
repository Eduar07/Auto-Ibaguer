"""Quién puede abrir qué pantalla, según su rol y su negocio (app.web.tablas.Rol).

Todo se decide por la dirección, en un solo lugar: cada pedido pasa por `revisar` al saber
quién lo hace (app.web.comun.administrador_actual). Así una pantalla nueva queda protegida
sin acordarse de ponerle nada, y lo que el cajero no ve en el menú tampoco lo abre escribiendo
la dirección.
"""

import re

from app.caja.estados import NOMBRE_DEL_NEGOCIO, Negocio
from app.web.tablas import Administrador

# Lo que solo hace un administrador: precios, tarifas y configuración; corregir lo que ya se
# cobró o se contó; anular salidas; las cuentas (Balance, archivos para Excel, resultado del
# mes); los usuarios; y dar de alta, cambiar o dar de baja una mensualidad.
SOLO_ADMINISTRADOR = tuple(
    re.compile(patron)
    for patron in (
        r"^/(parqueadero/)?balance(/|$)",
        r"^/configuracion(/|$)",
        r"^/mensajes(/|$)",
        r"^/probar-bot(/|$)",
        r"^/parqueadero/tarifas(/|$)",
        r"^/resultados(/|$)",
        r"^/usuarios(/|$)",
        r"^/(parqueadero/)?caja/\d+/corregir",
        r"^/(parqueadero/)?caja/salidas/\d+/anular",
        r"^/(parqueadero/)?caja/exportar",
        r"^/parqueadero/exportar",
        r"^/turnos/\d+/(corregir-cobro|deshacer-cobro)",
        r"^/parqueadero/\d+/(corregir|deshacer-salida)",
        r"^/parqueadero/mensualidades/pagos/\d+/(corregir|anular)",
        r"^/parqueadero/mensualidades/nueva",
        r"^/parqueadero/mensualidades/\d+/(editar|baja)",
    )
)
# Las del lavadero; las del parqueadero empiezan por /parqueadero.
DEL_LAVADERO = re.compile(r"^/(fila|turnos|caja|clientes|comentarios)(/|$)")


def negocio_de(ruta: str) -> Negocio | None:
    if ruta == "/parqueadero" or ruta.startswith("/parqueadero/"):
        return Negocio.PARQUEADERO
    if DEL_LAVADERO.match(ruta):
        return Negocio.LAVADERO
    return None


def solo_administrador(metodo: str, ruta: str) -> bool:
    if metodo == "POST" and ruta == "/parqueadero/mensualidades":  # crear una mensualidad
        return True
    return any(patron.match(ruta) for patron in SOLO_ADMINISTRADOR)


def revisar(usuario: Administrador, metodo: str, ruta: str) -> str | None:
    """None si puede; si no, por qué (para decírselo)."""
    negocio = negocio_de(ruta)
    if negocio is not None and not usuario.entra_a(negocio):
        return f"Tu usuario no entra al {NOMBRE_DEL_NEGOCIO[negocio].lower()}."
    if not usuario.es_administrador and solo_administrador(metodo, ruta):
        return "Eso lo hace un administrador: pídeselo a él."
    return None
