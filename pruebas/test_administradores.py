"""Administradores del dashboard y sus contraseñas."""

import pytest
from sqlalchemy.orm import Session

from app.web.administradores import guardar_administrador
from app.web.claves import verificar_clave

CLAVE = "lavadero-2026"


def test_la_contrasena_se_guarda_cifrada(sesion: Session):
    administrador, creado = guardar_administrador(sesion, "Eduar", CLAVE)

    assert creado
    assert administrador.usuario == "eduar"
    assert CLAVE not in administrador.clave_cifrada
    assert verificar_clave(administrador.clave_cifrada, CLAVE)
    assert not verificar_clave(administrador.clave_cifrada, "otra-contrasena")


def test_guardar_otra_vez_cambia_la_contrasena(sesion: Session):
    guardar_administrador(sesion, "eduar", CLAVE)
    administrador, creado = guardar_administrador(sesion, "eduar", "nueva-clave-segura")

    assert not creado
    assert verificar_clave(administrador.clave_cifrada, "nueva-clave-segura")
    assert not verificar_clave(administrador.clave_cifrada, CLAVE)


def test_rechaza_contrasenas_cortas(sesion: Session):
    with pytest.raises(ValueError, match="al menos 10"):
        guardar_administrador(sesion, "eduar", "corta")


@pytest.mark.parametrize("usuario", ["ed", "eduar guerrero", "eduar@correo"])
def test_rechaza_usuarios_con_formato_invalido(sesion: Session, usuario: str):
    with pytest.raises(ValueError, match="El usuario"):
        guardar_administrador(sesion, usuario, CLAVE)


def test_una_clave_danada_no_rompe_la_verificacion():
    assert not verificar_clave("esto-no-es-una-clave-cifrada", CLAVE)
