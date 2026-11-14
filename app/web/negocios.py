"""Los dos negocios del dashboard y sus secciones: el lavadero y el parqueadero.

Después de entrar se elige uno (la pantalla /inicio), y cada pantalla dice de cuál es: el
encabezado y la barra de abajo del celular muestran solo las secciones de ese negocio. Lo
que es del lavadero (el turno nuevo, el bot, los comentarios) no aparece en el parqueadero,
y al revés. Cambiar de negocio es un toque: el nombre del negocio en el encabezado.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Seccion:
    clave: str  # la que usa cada pantalla: "fila", "caja"...
    ruta: str
    nombre: str  # en el encabezado del computador
    corto: str  # en la barra de abajo del celular, donde cabe poco
    icono: str


@dataclass(frozen=True)
class Negocio:
    clave: str  # "lavadero" o "parqueadero"
    nombre: str
    icono: str
    descripcion: str  # en la pantalla de elegir
    secciones: tuple[Seccion, ...]

    @property
    def inicio(self) -> str:
        """A dónde lleva elegirlo: su primera sección."""
        return self.secciones[0].ruta


LAVADERO = Negocio(
    clave="lavadero",
    nombre="Lavadero",
    icono="gota",
    descripcion=(
        "La fila en vivo, la caja y el balance de lo cobrado, los clientes y sus comentarios."
    ),
    secciones=(
        Seccion("fila", "/fila", "Fila en vivo", "Fila", "fila"),
        Seccion("caja", "/caja", "Caja", "Caja", "caja"),
        Seccion("balance", "/balance", "Balance", "Balance", "grafica"),
        Seccion("clientes", "/clientes", "Clientes", "Clientes", "cliente"),
        # En la barra del celular caben seis si los nombres son cortos: "Buzón" de comentarios.
        Seccion("comentarios", "/comentarios", "Comentarios", "Buzón", "actividad"),
        Seccion("configuracion", "/configuracion", "Configuración", "Ajustes", "ajustes"),
    ),
)

PARQUEADERO = Negocio(
    clave="parqueadero",
    nombre="Parqueadero",
    icono="parqueo",
    descripcion="Entradas y salidas, la caja, las mensualidades y el balance.",
    secciones=(
        Seccion("entradas", "/parqueadero", "Entradas y salidas", "Entradas", "parqueo"),
        Seccion("caja", "/parqueadero/caja", "Caja", "Caja", "caja"),
        Seccion(
            "mensualidades",
            "/parqueadero/mensualidades",
            "Mensualidades",
            "Mensuales",
            "calendario",
        ),
        Seccion("balance", "/parqueadero/balance", "Balance", "Balance", "grafica"),
        Seccion("tarifas", "/parqueadero/tarifas", "Tarifas y ticket", "Tarifas", "ajustes"),
    ),
)

NEGOCIOS = {negocio.clave: negocio for negocio in (LAVADERO, PARQUEADERO)}


def de_la_seccion(seccion: str) -> tuple[Negocio | None, str]:
    """ "parqueadero/caja" → (el parqueadero, "caja"). La pantalla de elegir ("inicio") no es
    de ningún negocio.

    Una sección que no existe (un error al escribirla en una pantalla) se avisa de una vez,
    en vez de mostrar esa pantalla sin menú: las pruebas abren todas las pantallas.
    """
    if seccion in ("inicio", "resultados"):  # las de los dos negocios juntos
        return None, seccion
    clave_del_negocio, _, clave = seccion.partition("/")
    negocio = NEGOCIOS.get(clave_del_negocio)
    if negocio is None or clave not in {s.clave for s in negocio.secciones}:
        raise ValueError(f"Esa sección no existe: {seccion!r}")
    return negocio, clave
