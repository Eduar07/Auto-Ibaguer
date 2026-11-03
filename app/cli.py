"""Comandos de Auto Ibaguer para la terminal.

uv run auto-ibaguer preparar-base             crea las tablas y carga los datos iniciales
uv run auto-ibaguer crear-admin --usuario X   crea un administrador del dashboard
uv run auto-ibaguer simular-turno             pide un turno como lo hará el bot (pruebas)
uv run auto-ibaguer registrar-whatsapp        registra el número del bot (el día de la SIM)
"""

import argparse
import getpass
import re
import secrets
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app import tiempo
from app.catalogo.datos_iniciales import cargar_datos_iniciales
from app.catalogo.tablas import TipoVehiculo
from app.clientes import servicio as clientes
from app.config import obtener_entorno
from app.db.sesion import obtener_motor
from app.errores import ReglaDelNegocio
from app.formato import duracion, hora_con_articulo
from app.integraciones import whatsapp
from app.turnos import casos_de_uso as casos
from app.turnos.errores import FueraDeHorario
from app.turnos.estados import OrigenTurno
from app.web.administradores import guardar_administrador

RAIZ_DEL_PROYECTO = Path(__file__).resolve().parent.parent

NOMBRES_PARA_MOSTRAR = {"tipos_de_vehiculo": "tipos de vehículo", "parametros": "parámetros"}

MENSAJE_SIN_BASE = (
    "No hay conexión con PostgreSQL. Lo más probable es que Docker o la base estén apagados.\n"
    "Enciéndela con: docker compose up -d\n"
    "y revisa que diga (healthy) con: docker compose ps"
)


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


# Lo que se escribe en --vehiculo y el tipo de vehículo que corresponde.
VEHICULOS_PARA_SIMULAR = {"carro": "Automóvil", "camioneta": "Camioneta", "moto": "Moto"}


def simular_turno(vehiculo: str, nombre: str | None, celular: str | None) -> int:
    """Pide un turno por WhatsApp como lo hará el bot (hito 5), con las mismas reglas.

    Sirve para probar el dashboard y el reloj mientras el bot no existe. Si no se da el
    celular, se inventa uno que empieza por 5799: ningún celular colombiano empieza así.
    """
    if obtener_entorno().es_produccion:
        print("simular-turno es solo para probar en tu computador.", file=sys.stderr)
        return 1
    telefono = celular or f"5799{secrets.randbelow(10**8):08d}"
    try:
        with Session(obtener_motor()) as sesion, sesion.begin():
            cliente = clientes.registrar_cliente(
                sesion,
                nombre=nombre or f"Prueba {telefono[-4:]}",
                telefono=telefono,
                acepta_datos=True,
            )
            tipo = sesion.scalars(
                select(TipoVehiculo).where(TipoVehiculo.nombre == VEHICULOS_PARA_SIMULAR[vehiculo])
            ).one()
            vehiculo_del_cliente = clientes.registrar_vehiculo(
                sesion, cliente_id=cliente.id, tipo_vehiculo_id=tipo.id
            )
            nuevo = casos.pedir_turno(
                sesion, vehiculo_id=vehiculo_del_cliente.id, origen=OrigenTurno.WHATSAPP
            )
            resumen = [f"Turno {nuevo.numero} por WhatsApp (simulado) para {cliente.nombre}."]
            if nuevo.llamado:
                limite = hora_con_articulo(tiempo.hora_local(nuevo.gracia_hasta))
                resumen += [
                    f"Había un puesto libre: quedó llamado y tiene hasta {limite} para llegar.",
                    "Si no llega, el reloj lo pasa al final de la fila a esa hora "
                    "(con el servidor encendido).",
                ]
            else:
                adelante = "turno" if nuevo.adelante == 1 else "turnos"
                espera = (
                    f"; entra en aprox. {duracion(nuevo.espera_min)}" if nuevo.espera_min else ""
                )
                resumen.append(
                    f"Quedó en la fila con {nuevo.adelante} {adelante} adelante{espera}."
                )
    except ReglaDelNegocio as error:
        print(f"No se dio el turno: {error}", file=sys.stderr)
        if isinstance(error, FueraDeHorario):
            print(
                "Para probar a esta hora, amplía el horario en Configuración > Negocio y horario.",
                file=sys.stderr,
            )
        return 1
    print("\n".join(resumen))
    return 0


def registrar_whatsapp() -> int:
    """Registra el número del bot en WhatsApp con el PIN de la verificación en dos pasos.

    El PIN se escribe sin que se vea y no se guarda en ninguna parte.
    """
    entorno = obtener_entorno()
    datos = {
        "WHATSAPP_TOKEN": entorno.whatsapp_token,
        "WHATSAPP_ID_NUMERO": entorno.whatsapp_id_numero,
    }
    faltan = [nombre for nombre, valor in datos.items() if not valor]
    if faltan:
        print(f"Faltan {' y '.join(faltan)} en el archivo .env.", file=sys.stderr)
        return 1
    pin = getpass.getpass("PIN de 6 dígitos (no se ve mientras lo escribes): ").strip()
    if not re.fullmatch(r"[0-9]{6}", pin):
        print("El PIN son 6 números. No se registró nada.", file=sys.stderr)
        return 1
    try:
        whatsapp.registrar_numero(entorno, pin)
    except whatsapp.RegistroFallido as error:
        print(f"Meta no registró el número: {error}", file=sys.stderr)
        return 1
    print("Listo: el número quedó registrado en WhatsApp.")
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
    simular = comandos.add_parser(
        "simular-turno",
        help="Pide un turno como si el cliente escribiera por WhatsApp (solo para probar).",
    )
    simular.add_argument(
        "--vehiculo",
        choices=sorted(VEHICULOS_PARA_SIMULAR),
        default="carro",
        help="carro (si no se dice), camioneta o moto",
    )
    simular.add_argument("--nombre", help="Nombre del cliente (si no, uno de prueba)")
    simular.add_argument("--celular", help="Celular del cliente (si no, uno inventado)")
    comandos.add_parser(
        "registrar-whatsapp",
        help="Registra el número del bot en WhatsApp (una vez, el día que llega la SIM).",
    )

    argumentos = lector.parse_args(argv)
    try:
        if argumentos.comando == "preparar-base":
            return preparar_base()
        if argumentos.comando == "simular-turno":
            return simular_turno(argumentos.vehiculo, argumentos.nombre, argumentos.celular)
        if argumentos.comando == "registrar-whatsapp":
            return registrar_whatsapp()
        return crear_admin(argumentos.usuario)
    except OperationalError:
        print(MENSAJE_SIN_BASE, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
