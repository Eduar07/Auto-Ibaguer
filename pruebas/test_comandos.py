"""Los comandos de la terminal: preparar-base, crear-admin y simular-turno."""

import pytest
from sqlalchemy import Connection, Row, select
from sqlalchemy.exc import OperationalError

from app import cli
from app.clientes.tablas import Cliente
from app.config import obtener_entorno
from app.turnos.estados import EstadoTurno, OrigenTurno
from app.turnos.tablas import Turno
from app.web.claves import verificar_clave
from app.web.tablas import Administrador
from pruebas.fabricas import LUNES_8AM


def test_sin_base_de_datos_el_comando_dice_como_encenderla(monkeypatch, capsys):
    def base_apagada():
        raise OperationalError("SELECT 1", {}, Exception("server closed the connection"))

    monkeypatch.setattr(cli, "migrar", base_apagada)

    assert cli.main(["preparar-base"]) == 1
    error = capsys.readouterr().err
    assert "No hay conexión con PostgreSQL" in error
    assert "docker compose up -d" in error


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
