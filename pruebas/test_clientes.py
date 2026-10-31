"""Clientes y vehículos: teléfono, nombre, autorización de datos y placas."""

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.clientes import servicio as clientes
from app.clientes.errores import (
    FaltaAutorizacion,
    NombreInvalido,
    PlacaDeOtroCliente,
    PlacaInvalida,
    TelefonoInvalido,
)
from app.clientes.reglas import (
    nombre_bonito,
    normalizar_placa,
    normalizar_telefono,
    validar_placa,
)
from app.clientes.tablas import Cliente
from pruebas.fabricas import LUNES_8AM, cliente_con_vehiculo, tipo_vehiculo

PLACA_CARRO = r"^[A-Z]{3}[0-9]{3}$"
PLACA_MOTO = r"^[A-Z]{3}[0-9]{2}[A-Z]$"

# --- Reglas, sin base de datos ---------------------------------------------------------


@pytest.mark.parametrize(
    "texto", ["3227556850", "+57 322 755 6850", "57-322-755-6850", "573227556850"]
)
def test_el_telefono_se_guarda_como_lo_trae_whatsapp(texto):
    assert normalizar_telefono(texto) == "573227556850"


@pytest.mark.parametrize("texto", ["123", "no tengo", ""])
def test_telefono_invalido(texto):
    with pytest.raises(TelefonoInvalido):
        normalizar_telefono(texto)


def test_nombre_con_mayuscula_inicial():
    assert nombre_bonito("  eduar   GUERRERO ") == "Eduar Guerrero"
    assert nombre_bonito("maría josé") == "María José"


def test_el_nombre_no_puede_ir_vacio():
    with pytest.raises(NombreInvalido):
        nombre_bonito("   ")


@pytest.mark.parametrize(
    ("texto", "placa"), [("abc-123", "ABC123"), (" abc 12d ", "ABC12D"), ("abc.123", "ABC123")]
)
def test_la_placa_se_guarda_limpia(texto, placa):
    assert normalizar_placa(texto) == placa


def test_formato_de_placa_segun_el_vehiculo():
    assert validar_placa("abc-123", PLACA_CARRO, "Automóvil") == "ABC123"
    assert validar_placa("abc12d", PLACA_MOTO, "Moto") == "ABC12D"
    with pytest.raises(PlacaInvalida, match="placa de moto"):
        validar_placa("ABC123", PLACA_MOTO, "Moto")
    with pytest.raises(PlacaInvalida, match="placa de automóvil"):
        validar_placa("ABC12D", PLACA_CARRO, "Automóvil")


# --- Registro en la base ---------------------------------------------------------------


def contar_clientes(sesion: Session) -> int:
    return sesion.scalar(select(func.count()).select_from(Cliente))


def test_sin_aceptar_el_aviso_de_datos_no_se_guarda_nada(sesion: Session):
    antes = contar_clientes(sesion)
    with pytest.raises(FaltaAutorizacion):
        clientes.registrar_cliente(
            sesion, nombre="Ana", telefono="3001234567", acepta_datos=False, ahora=LUNES_8AM
        )
    assert contar_clientes(sesion) == antes


def test_el_telefono_identifica_al_cliente_y_el_nombre_se_guarda_una_vez(sesion: Session):
    primero = clientes.registrar_cliente(
        sesion, nombre="eduar guerrero", telefono="322 755 6850", acepta_datos=True, ahora=LUNES_8AM
    )
    assert primero.telefono == "573227556850"
    assert primero.nombre == "Eduar Guerrero"
    assert primero.fecha_aceptacion == LUNES_8AM

    otra_vez = clientes.registrar_cliente(
        sesion, nombre="Otro Nombre", telefono="+57 3227556850", acepta_datos=True
    )
    assert otra_vez.id == primero.id
    assert otra_vez.nombre == "Eduar Guerrero"
    assert clientes.buscar_por_telefono(sesion, "3227556850").id == primero.id


def test_cliente_del_local_sin_celular(sesion: Session):
    cliente = clientes.registrar_cliente(sesion, nombre="don josé", acepta_datos=True)
    assert cliente.telefono is None
    assert cliente.nombre == "Don José"


def test_el_vehiculo_sin_placa_se_reutiliza(sesion: Session):
    carro = cliente_con_vehiculo(sesion)
    automovil = tipo_vehiculo(sesion, "Automóvil").id
    otra_vez = clientes.registrar_vehiculo(
        sesion, cliente_id=carro.cliente_id, tipo_vehiculo_id=automovil
    )
    assert otra_vez.id == carro.id
    moto = clientes.registrar_vehiculo(
        sesion, cliente_id=carro.cliente_id, tipo_vehiculo_id=tipo_vehiculo(sesion, "Moto").id
    )
    assert moto.id != carro.id


def test_la_misma_placa_del_mismo_cliente_es_el_mismo_vehiculo(sesion: Session):
    carro = cliente_con_vehiculo(sesion, placa="ABC123")
    otra_vez = clientes.registrar_vehiculo(
        sesion,
        cliente_id=carro.cliente_id,
        tipo_vehiculo_id=carro.tipo_vehiculo_id,
        placa="abc 123",
    )
    assert otra_vez.id == carro.id


def test_la_placa_de_otro_cliente_se_rechaza(sesion: Session):
    cliente_con_vehiculo(sesion, placa="ABC123")
    with pytest.raises(PlacaDeOtroCliente, match="ABC123"):
        cliente_con_vehiculo(sesion, placa="ABC123")
