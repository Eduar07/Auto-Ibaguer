"""Comandos de Auto Ibaguer para la terminal.

uv run auto-ibaguer preparar-base             crea las tablas y carga los datos iniciales
uv run auto-ibaguer crear-admin --usuario X   crea un administrador del dashboard
"""

import argparse
import getpass
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy.orm import Session

from app.catalogo.datos_iniciales import cargar_datos_iniciales
from app.db.sesion import obtener_motor
from app.web.administradores import guardar_administrador

RAIZ_DEL_PROYECTO = Path(__file__).resolve().parent.parent

NOMBRES_PARA_MOSTRAR = {"tipos_de_vehiculo": "tipos de vehículo", "parametros": "parámetros"}


def configuracion_alembic(url: str | None = None, configurar_logs: bool = True) -> Config:
    """Configuración de Alembic. Si se pasa una URL, se usa esa base en vez de DATABASE_URL."""
    configuracion = Config(str(RAIZ_DEL_PROYECTO / "alembic.ini"))
    configuracion.attributes["configurar_logs"] = configurar_logs
    if url:
        # Para Alembic el "%" es especial: una contraseña con "%" se escribe "%%".
        configuracion.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return configuracion


def migrar(url: str | None = None, configurar_logs: bool = True) -> None:
    """Lleva las tablas a la última versión (aplica las migraciones que falten)."""
    command.upgrade(configuracion_alembic(url, configurar_logs), "head")


def preparar_base() -> int:
    print("1/2 Creando o actualizando las tablas...")
    migrar()
    print("2/2 Cargando los datos iniciales de Auto Ibaguer...")
    with Session(obtener_motor()) as sesion, sesion.begin():
        creados = cargar_datos_iniciales(sesion)
    print("    Registros nuevos:")
    for tipo, cantidad in creados.items():
        print(f"      {NOMBRES_PARA_MOSTRAR.get(tipo, tipo)}: {cantidad}")
    print("Listo: la base de datos está preparada.")
    return 0


def crear_admin(usuario: str) -> int:
    clave = getpass.getpass("Contraseña (mínimo 10 caracteres): ")
    if clave != getpass.getpass("Repite la contraseña: "):
        print("Las contraseñas no coinciden. No se guardó nada.", file=sys.stderr)
        return 1
    try:
        with Session(obtener_motor()) as sesion, sesion.begin():
            administrador, creado = guardar_administrador(sesion, usuario, clave)
            nombre = administrador.usuario
    except ValueError as error:
        print(f"{error} No se guardó nada.", file=sys.stderr)
        return 1
    accion = "creado" if creado else "actualizado (nueva contraseña)"
    print(f"Administrador '{nombre}' {accion}.")
    return 0


def main(argv: list[str] | None = None) -> int:
    lector = argparse.ArgumentParser(
        prog="auto-ibaguer", description="Comandos del sistema de turnos de Auto Ibaguer."
    )
    comandos = lector.add_subparsers(dest="comando", required=True)
    comandos.add_parser(
        "preparar-base", help="Crea o actualiza las tablas y carga los datos iniciales."
    )
    admin = comandos.add_parser(
        "crear-admin", help="Crea un administrador del dashboard o le cambia la contraseña."
    )
    admin.add_argument("--usuario", required=True, help="Nombre de usuario, por ejemplo: eduar")

    argumentos = lector.parse_args(argv)
    if argumentos.comando == "preparar-base":
        return preparar_base()
    return crear_admin(argumentos.usuario)


if __name__ == "__main__":
    sys.exit(main())
