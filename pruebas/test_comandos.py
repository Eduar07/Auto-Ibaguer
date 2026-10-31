"""Los comandos de la terminal: preparar-base y crear-admin."""

import pytest
from sqlalchemy import Connection, select
from sqlalchemy.exc import OperationalError

from app import cli
from app.web.claves import verificar_clave
from app.web.tablas import Administrador


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
