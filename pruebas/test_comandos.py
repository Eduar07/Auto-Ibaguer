"""Los comandos de la terminal: preparar-base, actualizar-tablas, revisar-base, crear-admin y
simular-turno."""

from collections.abc import Iterator

import pytest
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, Row, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app import cli
from app.clientes.tablas import Cliente
from app.config import obtener_entorno
from app.turnos.estados import EstadoTurno, OrigenTurno
from app.turnos.tablas import Turno
from app.web.claves import verificar_clave
from app.web.tablas import Administrador
from pruebas.base_de_pruebas import URL_PRUEBAS, borrar_base, recrear_base
from pruebas.fabricas import LUNES_8AM, nuevo_turno


def base_apagada():
    raise OperationalError("SELECT 1", {}, Exception("server closed the connection"))


def test_sin_base_de_datos_el_comando_dice_como_encenderla(monkeypatch, capsys):
    monkeypatch.setattr(cli, "migrar", base_apagada)

    assert cli.main(["preparar-base"]) == 1
    error = capsys.readouterr().err
    assert "No hay conexión con PostgreSQL" in error
    assert "docker compose up -d" in error


def test_en_la_nube_sin_base_de_datos_dice_que_revisar_en_render(monkeypatch, capsys):
    monkeypatch.setattr(cli, "migrar", base_apagada)
    monkeypatch.setattr(obtener_entorno(), "entorno", "produccion")

    assert cli.main(["preparar-base"]) == 1
    error = capsys.readouterr().err
    assert "No hay conexión con PostgreSQL" in error
    assert "Render" in error
    assert "docker" not in error  # en la nube no hay Docker que encender


@pytest.fixture
def base_de_pruebas(conexion: Connection, monkeypatch: pytest.MonkeyPatch) -> Connection:
    """Hace que los comandos usen la base de pruebas, sin dejar nada guardado."""
    monkeypatch.setattr(cli, "obtener_motor", lambda: conexion)
    return conexion


def escribir_contrasenas(monkeypatch: pytest.MonkeyPatch, *respuestas: str) -> None:
    """Simula lo que la persona escribe cuando el comando le pide la contraseña."""
    pendientes = iter(respuestas)
    monkeypatch.setattr(cli.getpass, "getpass", lambda _pregunta="": next(pendientes))


def test_preparar_base(base_de_pruebas, monkeypatch, capsys):
    migraciones = []
    monkeypatch.setattr(cli, "migrar", lambda: migraciones.append("aplicadas"))

    assert cli.main(["preparar-base"]) == 0

    salida = capsys.readouterr().out
    assert migraciones == ["aplicadas"]
    assert "servicios: 0" in salida  # los datos ya estaban cargados
    assert "tipos de vehículo: 0" in salida
    assert "Listo" in salida


def test_actualizar_tablas_solo_migra_y_no_toca_los_datos(monkeypatch, capsys):
    """Lo corre la nube cada vez que enciende: las tablas al día y nada más (los datos
    iniciales se cargan una vez, con preparar-base)."""
    migraciones = []
    monkeypatch.setattr(cli, "migrar", lambda: migraciones.append("aplicadas"))
    monkeypatch.setattr(
        cli, "cargar_datos_iniciales", lambda _sesion: pytest.fail("no debe tocar los datos")
    )

    assert cli.main(["actualizar-tablas"]) == 0

    assert migraciones == ["aplicadas"]
    assert "Tablas al día." in capsys.readouterr().out


def test_actualizar_tablas_sin_base_de_datos_dice_que_revisar(monkeypatch, capsys):
    monkeypatch.setattr(cli, "migrar", base_apagada)
    monkeypatch.setattr(obtener_entorno(), "entorno", "produccion")

    assert cli.main(["actualizar-tablas"]) == 1
    assert "No hay conexión con PostgreSQL" in capsys.readouterr().err


# --- revisar-base: si las tablas están al día y cuánto hay ---------------------------------


def ultima_version() -> str:
    """La última migración que trae el código."""
    migraciones = ScriptDirectory.from_config(cli.configuracion_alembic(configurar_logs=False))
    return migraciones.get_current_head() or ""


def test_revisar_la_base(base_de_pruebas, sesion: Session, capsys):
    turno = nuevo_turno(sesion)
    turno.creado_en = LUNES_8AM
    sesion.flush()

    assert cli.main(["revisar-base"]) == 0

    salida = capsys.readouterr().out
    assert "(PostgreSQL " in salida
    assert f"Tablas: al día (versión {ultima_version()})" in salida
    assert "  clientes: 1\n" in salida
    assert "  turnos: 1 (el último: 2026-10-26 8:00 a.m.)\n" in salida
    assert "  administradores: 0\n" in salida
    assert "Crea uno con: auto-ibaguer crear-admin" in salida


def test_revisar_una_base_desactualizada(base_de_pruebas, capsys):
    base_de_pruebas.execute(text("UPDATE alembic_version SET version_num = '0003'"))

    assert cli.main(["revisar-base"]) == 1

    salida = capsys.readouterr().out
    assert f"desactualizadas (versión 0003; la última es la {ultima_version()})" in salida
    assert "Actualízalas con: auto-ibaguer preparar-base" in salida


def test_revisar_una_base_de_una_version_mas_nueva_del_sistema(base_de_pruebas, capsys):
    base_de_pruebas.execute(text("UPDATE alembic_version SET version_num = '0099'"))

    assert cli.main(["revisar-base"]) == 1
    assert "versión 0099, que este código no conoce" in capsys.readouterr().out


def url_de_prueba(**cambios: str) -> str:
    return make_url(URL_PRUEBAS).set(**cambios).render_as_string(hide_password=False)


@pytest.fixture
def base_vacia(motor) -> Iterator[str]:
    """Una base recién creada, sin tablas (pide `motor`: sin PostgreSQL, para con un aviso)."""
    url = url_de_prueba(database="auto_ibaguer_pruebas_vacia")
    recrear_base(url)
    yield url
    borrar_base(url)


def test_revisar_una_base_vacia(base_vacia: str, capsys):
    assert cli.main(["revisar-base", "--url", base_vacia]) == 1
    assert "Tablas: no hay, la base está vacía." in capsys.readouterr().out


@pytest.mark.usefixtures("motor")
def test_revisar_otra_base_con_la_url_como_la_da_render(capsys):
    url = url_de_prueba(drivername="postgresql")  # postgresql://, sin +psycopg

    assert cli.main(["revisar-base", "--url", url]) == 0

    salida = capsys.readouterr().out
    assert f"Base de datos {make_url(URL_PRUEBAS).database} (PostgreSQL " in salida
    assert "Tablas: al día" in salida


def test_revisar_con_una_url_equivocada_no_muestra_la_contrasena(capsys):
    url = url_de_prueba(database="no_existe_prueba", password="clave-secreta-123")

    assert cli.main(["revisar-base", "--url", url]) == 1

    salida = capsys.readouterr()
    assert "No hay conexión con la base de esa URL" in salida.err
    assert "clave-secreta-123" not in salida.out + salida.err


def test_revisar_con_una_url_que_no_se_entiende(capsys):
    assert cli.main(["revisar-base", "--url", "la base de render"]) == 1
    assert "Esa URL no se entiende" in capsys.readouterr().err


def test_crear_admin(base_de_pruebas, monkeypatch, capsys):
    escribir_contrasenas(monkeypatch, "clave-del-lavadero", "clave-del-lavadero")

    assert cli.main(["crear-admin", "--usuario", "Eduar"]) == 0

    assert "Administrador 'eduar' creado." in capsys.readouterr().out
    clave_cifrada = base_de_pruebas.execute(
        select(Administrador.clave_cifrada).where(Administrador.usuario == "eduar")
    ).scalar_one()
    assert verificar_clave(clave_cifrada, "clave-del-lavadero")


def test_crear_admin_con_contrasenas_distintas(base_de_pruebas, monkeypatch, capsys):
    escribir_contrasenas(monkeypatch, "clave-del-lavadero", "clave-distinta-123")

    assert cli.main(["crear-admin", "--usuario", "eduar"]) == 1
    assert "no coinciden" in capsys.readouterr().err


def test_crear_admin_con_contrasena_corta(base_de_pruebas, monkeypatch, capsys):
    escribir_contrasenas(monkeypatch, "corta", "corta")

    assert cli.main(["crear-admin", "--usuario", "eduar"]) == 1
    assert "al menos 10" in capsys.readouterr().err


# --- simular-turno: un turno como si lo pidieran por WhatsApp ------------------------------


@pytest.fixture
def para_simular(base_de_pruebas: Connection, reloj: dict) -> Connection:
    """La base de pruebas con la hora fija en el lunes a las 8:00 a.m.

    El SAVEPOINT hace que, si el comando falla y deshace lo suyo, no deshaga nada más.
    """
    base_de_pruebas.begin_nested()
    return base_de_pruebas


def turno_simulado(conexion: Connection) -> Row:
    consulta = select(Turno.origen, Turno.estado, Cliente.telefono, Cliente.nombre).join(
        Cliente, Cliente.id == Turno.cliente_id
    )
    return conexion.execute(consulta).one()


def test_simular_un_turno_por_whatsapp(para_simular, capsys):
    argumentos = ["simular-turno", "--nombre", "ana  ruiz", "--celular", "300 123 4567"]

    assert cli.main(argumentos) == 0

    salida = capsys.readouterr().out
    assert "Turno C-01 por WhatsApp (simulado) para Ana Ruiz." in salida
    assert "quedó llamado y tiene hasta las 8:15 a.m. para llegar" in salida
    turno = turno_simulado(para_simular)
    assert (turno.origen, turno.estado) == (OrigenTurno.WHATSAPP, EstadoTurno.LLAMADO)
    assert turno.telefono == "573001234567"


def test_simular_sin_datos_inventa_un_celular_que_no_es_de_nadie(para_simular, capsys):
    assert cli.main(["simular-turno", "--vehiculo", "moto"]) == 0

    turno = turno_simulado(para_simular)
    assert turno.telefono.startswith("5799") and len(turno.telefono) == 12
    assert turno.nombre == f"Prueba {turno.telefono[-4:]}"
    assert f"Turno M-01 por WhatsApp (simulado) para {turno.nombre}." in capsys.readouterr().out


def test_simular_fuera_de_horario_dice_como_probar(para_simular, reloj, capsys):
    reloj["ahora"] = LUNES_8AM.replace(hour=22)

    assert cli.main(["simular-turno"]) == 1

    error = capsys.readouterr().err
    assert "No se dio el turno: Los turnos por WhatsApp se dan de 5:30 a.m. a 7:30 p.m." in error
    assert "amplía el horario en Configuración" in error
    # No quedó nada a medias: ni el cliente de prueba.
    assert para_simular.execute(select(Cliente.id)).first() is None


def test_simular_no_se_usa_en_produccion(monkeypatch, capsys):
    monkeypatch.setattr(obtener_entorno(), "entorno", "produccion")

    assert cli.main(["simular-turno"]) == 1
    assert "solo para probar en tu computador" in capsys.readouterr().err
