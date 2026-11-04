"""Comandos de Auto Ibaguer para la terminal.

uv run auto-ibaguer preparar-base             crea las tablas y carga los datos iniciales
uv run auto-ibaguer revisar-base              dice si las tablas están al día y cuánto hay
uv run auto-ibaguer crear-admin --usuario X   crea un administrador del dashboard
uv run auto-ibaguer simular-turno             pide un turno como lo hará el bot (pruebas)
uv run auto-ibaguer registrar-whatsapp        registra el número del bot (el día de la SIM)
uv run auto-ibaguer probar-gemini             corre las frases de prueba contra Gemini
"""

import argparse
import getpass
import re
import sys
import time
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, select
from sqlalchemy.exc import ArgumentError, OperationalError
from sqlalchemy.orm import Session

from app import tiempo
from app.bot import redactor
from app.bot.conversacion import catalogo_del_bot, servicios_de_la_ficha
from app.bot.frases_de_prueba import FRASES, FraseDePrueba
from app.bot.interprete import SI, Contexto, Intencion, Interpretacion
from app.bot.textos import Textos
from app.catalogo.datos_iniciales import cargar_datos_iniciales
from app.catalogo.parametros import leer_parametros
from app.catalogo.tablas import TipoVehiculo
from app.clientes import servicio as clientes
from app.clientes.reglas import celular_inventado
from app.config import con_psycopg, obtener_entorno
from app.db.revision import Revision, revisar
from app.db.sesion import obtener_motor
from app.errores import ReglaDelNegocio
from app.formato import duracion, hora, hora_con_articulo, miles
from app.formato import horario as horario_legible
from app.integraciones import gemini, whatsapp
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
MENSAJE_SIN_BASE_EN_LA_NUBE = (
    "No hay conexión con PostgreSQL. En el dashboard de Render revisa que la base\n"
    "auto-ibaguer-base diga Available y que el servicio tenga DATABASE_URL en Environment."
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


def revisar_base(url: str | None) -> int:
    """Dice en qué versión están las tablas y cuánto hay, sin mostrar ningún dato.

    Con `url` revisa otra base, por ejemplo una copia de seguridad recién restaurada.
    """
    migraciones = ScriptDirectory.from_config(configuracion_alembic(configurar_logs=False))
    if url is None:
        with Session(obtener_motor()) as sesion:
            revision = revisar(sesion.connection(), migraciones)
    else:
        try:
            motor = create_engine(
                con_psycopg(url), hide_parameters=True, connect_args={"connect_timeout": 10}
            )
        except ArgumentError:
            print(
                "Esa URL no se entiende. Tiene esta forma: "
                "postgresql://usuario:contraseña@servidor:puerto/base",
                file=sys.stderr,
            )
            return 1
        try:
            with Session(motor) as sesion:
                revision = revisar(sesion.connection(), migraciones)
        except OperationalError:
            print(
                "No hay conexión con la base de esa URL. Revisa el servidor, el puerto, "
                "el nombre de la base, el usuario y la contraseña.",
                file=sys.stderr,
            )
            return 1
        finally:
            motor.dispose()
    print("\n".join(_informe(revision)))
    return 0 if revision.al_dia else 1


def _informe(revision: Revision) -> list[str]:
    lineas = [f"Base de datos {revision.base_de_datos} (PostgreSQL {revision.postgresql})"]
    if revision.version is None:
        return lineas + [
            "Tablas: no hay, la base está vacía. Prepárala con: auto-ibaguer preparar-base"
        ]
    if not revision.conocida:
        lineas.append(
            f"Tablas: versión {revision.version}, que este código no conoce (la última que "
            f"conoce es la {revision.ultima}). ¿Viene de una versión más nueva del sistema?"
        )
    elif not revision.al_dia:
        lineas.append(
            f"Tablas: desactualizadas (versión {revision.version}; la última es la "
            f"{revision.ultima}). Actualízalas con: auto-ibaguer preparar-base"
        )
    else:
        lineas.append(f"Tablas: al día (versión {revision.version})")
    for nombre, cantidad in revision.cantidades.items():
        detalle = ""
        if nombre == "turnos" and revision.ultimo_turno is not None:
            local = tiempo.hora_local(revision.ultimo_turno)
            detalle = f" (el último: {local:%Y-%m-%d} {hora(local)})"
        lineas.append(f"  {nombre}: {miles(cantidad)}{detalle}")
    if revision.cantidades.get("administradores") == 0:
        lineas.append(
            "Todavía no hay administradores. Crea uno con: auto-ibaguer crear-admin --usuario eduar"
        )
    return lineas


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
    celular, se inventa uno que empieza por 5799 (app.clientes.reglas.PREFIJO_DE_PRUEBA).
    """
    if obtener_entorno().es_produccion:
        print("simular-turno es solo para probar en tu computador.", file=sys.stderr)
        return 1
    telefono = celular or celular_inventado()
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


def _como_salio(frase: FraseDePrueba, entendido: Interpretacion | None) -> str | None:
    """None si Gemini entendió lo que se esperaba; si no, qué se esperaba."""
    if entendido is None:
        return "no respondió"
    faltas = []
    if entendido.intencion not in (frase.intencion, *frase.tambien):
        faltas.append(f"intención {frase.intencion.value}")
    if frase.vehiculo and entendido.vehiculo != frase.vehiculo:
        faltas.append(f"vehículo {frase.vehiculo}")
    if frase.servicio and entendido.servicio != frase.servicio:
        faltas.append(f"servicio {frase.servicio}")
    if frase.si_o_no and entendido.si_o_no != frase.si_o_no:
        faltas.append(f"que conteste {'sí' if frase.si_o_no == SI else 'no'}")
    return "esperaba " + ", ".join(faltas) if faltas else None


def _lo_que_entendio(entendido: Interpretacion) -> str:
    partes = [entendido.intencion.value, entendido.vehiculo, entendido.servicio]
    if entendido.si_o_no:
        partes.append(f"contesta {entendido.si_o_no}")
    return ", ".join(parte for parte in partes if parte)


# Con estas el bot espera que Gemini escriba la respuesta (las demás las contesta el sistema).
CONVERSACION = frozenset(
    {
        Intencion.SALUDO,
        Intencion.DESPEDIDA,
        Intencion.FELICITACION,
        Intencion.PREGUNTA,
        Intencion.CHARLA,
        Intencion.OTRO,
    }
)


def _lo_que_contesta(entendido: Interpretacion, permitido: str) -> str | None:
    """Lo que el bot le diría al cliente con lo que escribió Gemini, como en WhatsApp."""
    if not entendido.respuesta:
        if entendido.intencion in CONVERSACION:
            return "no escribió la respuesta: sale un texto fijo"
        return None
    revision = redactor.revisar_con_motivo(entendido.respuesta, permitido)
    if revision.texto is None:
        return f"sale un texto fijo: lo que escribió {revision.motivo} «{entendido.respuesta}»"
    if entendido.intencion == Intencion.PREGUNTA and not entendido.sabe:
        return f"no está en la ficha: dice «{revision.texto}» y se la pasa al administrador"
    return f"contesta «{revision.texto}»"


def probar_gemini(modelo: str | None, pausa: float) -> int:
    """Corre las frases de prueba (app/bot/frases_de_prueba.py) contra el modelo de verdad.

    Sirve para decidir si un modelo nuevo se puede usar con clientes. Cada frase es una
    sola consulta, como en WhatsApp: muestra lo que entendió y lo que contestaría el bot
    con la ficha del negocio. Las frases "con memoria" van con lo que se venían diciendo
    antes. Entre consulta y consulta espera `pausa` segundos, para no pasarse del límite
    del plan gratis.
    """
    entorno = obtener_entorno()
    if not entorno.gemini_clave:
        print("Falta GEMINI_CLAVE en el archivo .env.", file=sys.stderr)
        return 1
    with Session(obtener_motor()) as sesion:
        parametros = leer_parametros(sesion)
        catalogo = catalogo_del_bot(sesion)
        servicios = servicios_de_la_ficha(sesion)
    ficha = redactor.ficha_completa(parametros, catalogo, servicios)
    textos = Textos(parametros.textos_bot)
    horario = horario_legible(
        parametros.dias_atencion, parametros.hora_apertura, parametros.hora_cierre
    )
    # Como le escribiría alguien que todavía no es cliente.
    guias = (
        ("saludo", textos("saludo", negocio=parametros.nombre_negocio, horario=horario)),
        ("despedida", textos("despedida")),
        ("felicitacion", textos("felicitacion")),
    )
    permitido = "\n".join([ficha, *(texto for _, texto in guias)])
    modelo = (modelo or parametros.modelo_gemini).strip().lower()
    interprete = gemini.Gemini(entorno.gemini_clave, modelo, negocio=parametros.nombre_negocio)
    print(
        f"Probando {len(FRASES)} frases con {modelo}: una consulta por frase, cada "
        f"{pausa:g} segundos."
    )
    bien = 0
    for numero, frase in enumerate(FRASES):
        if numero:
            time.sleep(pausa)
        contexto = Contexto(
            catalogo, ficha=ficha, antes=frase.antes, en_que_va=frase.en_que_va, guias=guias
        )
        entendido = interprete.interpretar(frase.texto, contexto)
        falla = _como_salio(frase, entendido)
        cual = (f"[{frase.prueba}] " if frase.prueba else "") + f"«{frase.texto}»"
        if frase.antes:
            cual += " (con memoria)"
        if frase.en_que_va:
            cual += " (en medio de una pregunta del bot)"
        if falla is None:
            bien += 1
            print(f"  bien   {cual} → {_lo_que_entendio(entendido)}")
        else:
            detalle = interprete.ultimo_problema or (
                f"entendió {_lo_que_entendio(entendido)}" if entendido else ""
            )
            print(f"  MAL    {cual} → {falla} ({detalle})")
        contesta = _lo_que_contesta(entendido, permitido) if entendido is not None else None
        if contesta:
            print(f"         {contesta}")
    porcentaje = round(100 * bien / len(FRASES))
    print(f"{bien} de {len(FRASES)} bien ({porcentaje} %).")
    print("Lo que contesta no cuenta en ese número: léelo y mira si te gusta el tono.")
    if bien < len(FRASES):
        print("Revisa las que salieron MAL antes de usar este modelo con clientes.")
        print("Si dicen «429», es la cuota del plan gratis: espera un rato o sube la --pausa.")
    return 0 if bien == len(FRASES) else 1


def main(argv: list[str] | None = None) -> int:
    lector = argparse.ArgumentParser(
        prog="auto-ibaguer", description="Comandos del sistema de turnos de Auto Ibaguer."
    )
    comandos = lector.add_subparsers(dest="comando", required=True)
    comandos.add_parser(
        "preparar-base", help="Crea o actualiza las tablas y carga los datos iniciales."
    )
    revisar_comando = comandos.add_parser(
        "revisar-base",
        help="Dice si las tablas están al día y cuántos datos hay (sin mostrar ninguno).",
    )
    revisar_comando.add_argument(
        "--url",
        help="Revisa otra base, por ejemplo una copia restaurada (si no, la de DATABASE_URL)",
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
    probar = comandos.add_parser(
        "probar-gemini",
        help="Corre las frases de prueba contra Gemini y muestra lo que contesta a las "
        "preguntas y a la charla (antes de cambiar el modelo).",
    )
    probar.add_argument("--modelo", help="El modelo a probar (si no, el de Configuración)")
    probar.add_argument(
        "--pausa",
        type=float,
        default=6.0,
        help="Segundos entre frase y frase (6 para el plan gratis; 0 con el de pago)",
    )

    argumentos = lector.parse_args(argv)
    try:
        if argumentos.comando == "preparar-base":
            return preparar_base()
        if argumentos.comando == "revisar-base":
            return revisar_base(argumentos.url)
        if argumentos.comando == "simular-turno":
            return simular_turno(argumentos.vehiculo, argumentos.nombre, argumentos.celular)
        if argumentos.comando == "registrar-whatsapp":
            return registrar_whatsapp()
        if argumentos.comando == "probar-gemini":
            return probar_gemini(argumentos.modelo, argumentos.pausa)
        return crear_admin(argumentos.usuario)
    except OperationalError:
        en_la_nube = obtener_entorno().es_produccion
        print(MENSAJE_SIN_BASE_EN_LA_NUBE if en_la_nube else MENSAJE_SIN_BASE, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
