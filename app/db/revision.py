"""Una revisión rápida de la base de datos: en qué versión están las tablas y cuánto hay.

Sirve en la nube después de publicar (¿se aplicaron las migraciones?) y después de
restaurar una copia de seguridad (¿quedó todo?). Solo cuenta filas: no muestra ningún
dato de los clientes.
"""

from dataclasses import dataclass
from datetime import datetime

from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, func, select

from app.clientes.tablas import Cliente, Vehiculo
from app.integraciones.tablas import MensajeEntrante, MensajeSaliente
from app.turnos.tablas import Turno
from app.web.tablas import Administrador

# Lo que se cuenta, con el nombre que se muestra.
QUE_SE_CUENTA = {
    "clientes": Cliente,
    "vehículos": Vehiculo,
    "turnos": Turno,
    "mensajes recibidos": MensajeEntrante,
    "mensajes salientes": MensajeSaliente,
    "administradores": Administrador,
}


@dataclass(frozen=True)
class Revision:
    base_de_datos: str
    postgresql: str  # la versión del servidor, por ejemplo "17.6"
    # La versión de las tablas (la última migración aplicada). None: la base está vacía.
    version: str | None
    # La última migración que trae el código.
    ultima: str
    # False si la base tiene una versión que el código no conoce (por ejemplo, una copia
    # sacada de una versión más nueva del sistema).
    conocida: bool
    cantidades: dict[str, int]
    ultimo_turno: datetime | None

    @property
    def al_dia(self) -> bool:
        return self.version == self.ultima


def revisar(conexion: Connection, migraciones: ScriptDirectory) -> Revision:
    version = MigrationContext.configure(conexion).get_current_revision()
    cantidades: dict[str, int] = {}
    ultimo_turno = None
    if version is not None:
        for nombre, tabla in QUE_SE_CUENTA.items():
            cantidades[nombre] = conexion.scalar(select(func.count()).select_from(tabla)) or 0
        ultimo_turno = conexion.scalar(select(func.max(Turno.creado_en)))
    conocidas = {migracion.revision for migracion in migraciones.walk_revisions()}
    return Revision(
        base_de_datos=conexion.engine.url.database or "",
        postgresql=".".join(str(parte) for parte in conexion.dialect.server_version_info or ()),
        version=version,
        ultima=migraciones.get_current_head() or "",
        conocida=version is None or version in conocidas,
        cantidades=cantidades,
        ultimo_turno=ultimo_turno,
    )
