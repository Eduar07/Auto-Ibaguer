"""Valores imposibles: un cero de más, un número de miles de cifras o un "²" escrito en un
formulario (o en la dirección) nunca tumban el servidor.

Lo que tiene que cumplirse siempre:
- la plata pasa por un tope: un cobro (lavado, salida, mensualidad, tarifa) hasta $5.000.000 y
  lo que hay en una caja (la base, lo contado) hasta $100.000.000. Más que eso se explica
  ("revisa los ceros") y no se guarda nada;
- un número se lee sin caerse: "²" no es un número, y uno de miles de cifras tampoco;
- si aun así llega a la base de datos un valor que no cabe (un id de 20 cifras en la
  dirección), se responde "Ese valor no es posible" y no "El servidor tuvo un problema", y el
  registro no guarda los datos.
"""

import logging
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import numeros
from app.caja.errores import ValorInvalido
from app.caja.reglas import VALOR_MAXIMO_DE_UN_COBRO, VALOR_MAXIMO_EN_LA_CAJA, sin_ceros_de_mas
from app.caja.tablas import Caja
from app.catalogo.configuracion import PRECIO_MAXIMO
from app.catalogo.tablas import Fila, TipoVehiculo
from app.parqueadero import casos_de_uso as parqueo
from app.parqueadero.errores import MensualidadInvalida, TarifaInvalida
from app.parqueadero.estados import TipoParqueo
from app.parqueadero.reglas import Tarifa, placa_o_ticket, validar_tarifa
from app.parqueadero.tablas import PagoMensualidad
from app.turnos import caja as caja_lavadero
from app.turnos import casos_de_uso as casos
from app.turnos.errores import PrecioInvalido
from app.turnos.estados import MedioPago
from pruebas.fabricas import LUNES_8AM, minutos, servicio, tipo_vehiculo, turno_listo
from pruebas.navegador import HTMX, error
from pruebas.test_cobro_por_dias import TARIFAS
from pruebas.test_pantallas_clientes_y_configuracion import formulario_de_configuracion

VEINTE_CIFRAS = "9" * 20
MILES_DE_CIFRAS = "9" * 5000  # Python no convierte a número un texto de más de 4.300 cifras
SUPERINDICE = "²"  # un dígito para str.isdigit(), pero int("²") falla
ABSURDOS = (VEINTE_CIFRAS, MILES_DE_CIFRAS, SUPERINDICE)
REVISA_LOS_CEROS = "Ese valor es demasiado alto: revisa los ceros"


# --- Leer números --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("texto", "numero"),
    [
        ("12", 12),
        ("999999999", 999_999_999),
        ("", None),
        ("abc", None),
        ("-3", None),
        ("1.5", None),
        (" 7", None),  # quien llama quita los espacios
        (SUPERINDICE, None),
        ("1234567890", None),  # más de 9 cifras: no cabe en un INTEGER de PostgreSQL
        (MILES_DE_CIFRAS, None),
    ],
)
def test_leer_un_entero(texto: str, numero: int | None):
    assert numeros.entero(texto) == numero


@pytest.mark.parametrize(
    ("texto", "valor"),
    [
        ("$40.000", 40_000),
        ("40000", 40_000),
        ("0", 0),
        ("000000000000000000000040.000", 40_000),  # los ceros de adelante no cuentan
        ("", None),
        ("abc", None),
        (SUPERINDICE, None),  # no es un dígito para la plata: es como no escribir nada
        (VEINTE_CIFRAS, numeros.DEMASIADO),
        (MILES_DE_CIFRAS, numeros.DEMASIADO),
    ],
)
def test_leer_pesos(texto: str, valor: int | None):
    assert numeros.pesos(texto) == valor


def test_demasiado_pasa_de_cualquier_tope():
    assert numeros.DEMASIADO > max(VALOR_MAXIMO_DE_UN_COBRO, VALOR_MAXIMO_EN_LA_CAJA)


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [("123", (None, 123)), ("#45", (None, 45)), ("abc123", ("ABC123", None))],
)
def test_buscar_por_placa_o_ticket(texto: str, esperado: tuple):
    assert placa_o_ticket(texto) == esperado


@pytest.mark.parametrize("texto", [SUPERINDICE, "1234567890", MILES_DE_CIFRAS])
def test_un_numero_imposible_no_es_un_ticket(texto: str):
    _, ticket = placa_o_ticket(texto)

    assert ticket is None


# --- Los topes -----------------------------------------------------------------------------


def test_un_precio_del_menu_siempre_se_puede_cobrar():
    """Si el menú aceptara más de lo que se puede cobrar, ese servicio no se podría cobrar."""
    assert VALOR_MAXIMO_DE_UN_COBRO == PRECIO_MAXIMO


def test_el_tope_deja_pasar_hasta_el_maximo():
    assert sin_ceros_de_mas(VALOR_MAXIMO_DE_UN_COBRO, VALOR_MAXIMO_DE_UN_COBRO) == 5_000_000

    with pytest.raises(ValorInvalido, match=r"revisa los ceros \(el máximo es \$5\.000\.000\)"):
        sin_ceros_de_mas(VALOR_MAXIMO_DE_UN_COBRO + 1, VALOR_MAXIMO_DE_UN_COBRO)
    with pytest.raises(TarifaInvalida):
        sin_ceros_de_mas(VALOR_MAXIMO_DE_UN_COBRO + 1, VALOR_MAXIMO_DE_UN_COBRO, TarifaInvalida)


@pytest.mark.parametrize(
    "abrir", [caja_lavadero.abrir_caja, parqueo.abrir_caja], ids=["lavadero", "parqueadero"]
)
def test_la_base_de_una_caja_tiene_tope(sesion: Session, abrir):
    with pytest.raises(ValorInvalido, match=r"el máximo es \$100\.000\.000"):
        abrir(sesion, base=VALOR_MAXIMO_EN_LA_CAJA + 1, ahora=LUNES_8AM)

    assert sesion.scalar(select(func.count()).select_from(Caja)) == 0
    assert abrir(sesion, base=VALOR_MAXIMO_EN_LA_CAJA, ahora=LUNES_8AM).base == 100_000_000


def test_un_cobro_del_lavadero_tiene_tope(sesion: Session, caja_del_lavadero):
    turno = turno_listo(sesion)

    with pytest.raises(PrecioInvalido, match=REVISA_LOS_CEROS):
        casos.cobrar(
            sesion,
            turno,
            medio_pago=MedioPago.EFECTIVO,
            precio_cobrado=VALOR_MAXIMO_DE_UN_COBRO + 1,
            motivo="Lavado de un bus",
        )


def test_una_tarifa_del_parqueadero_tiene_tope():
    for campo in ("valor_hora", "valor_fraccion", "valor_dia", "valor_dia_siguiente"):
        valores = {"valor_hora": 3_000, "gracia_minutos": 15, "valor_dia": 20_000}
        valores[campo] = VALOR_MAXIMO_DE_UN_COBRO + 1
        if campo == "valor_dia_siguiente":
            valores["valor_dia"] = 20_000
        with pytest.raises(TarifaInvalida, match=REVISA_LOS_CEROS):
            validar_tarifa(Tarifa(**valores))


def test_una_mensualidad_tiene_tope(sesion: Session):
    with pytest.raises(MensualidadInvalida, match=REVISA_LOS_CEROS):
        parqueo.crear_mensualidad(
            sesion,
            placa="MEN123",
            tipo=TipoParqueo.CARRO,
            titular="Ana Ruiz",
            valor_mensual=VALOR_MAXIMO_DE_UN_COBRO + 1,
            autorizo_datos=True,
            ahora=LUNES_8AM,
        )


# --- Ningún formulario se cae ---------------------------------------------------------------


@pytest.fixture
def los_dos_negocios(sesion: Session, reloj: dict, admin) -> dict:
    """En cada negocio, una caja cerrada y otra abierta, con cobros para corregir: el lavadero
    con un turno listo por cobrar; el parqueadero con un carro adentro y una mensualidad pagada."""
    reloj["ahora"] = LUNES_8AM + minutos(120)
    antes = LUNES_8AM - timedelta(hours=2)
    cerrada = caja_lavadero.abrir_caja(sesion, base=20_000, ahora=antes)
    primero = turno_listo(sesion, placa="AAA111", ahora=antes)
    casos.cobrar(sesion, primero, medio_pago=MedioPago.EFECTIVO, ahora=antes + minutos(55))
    caja_lavadero.cerrar_caja(
        sesion, cerrada.id, efectivo_contado=60_000, ahora=antes + minutos(60)
    )
    abierta = caja_lavadero.abrir_caja(sesion, base=10_000, ahora=antes + minutos(61))
    cobrado = turno_listo(sesion, placa="BBB222")
    casos.cobrar(sesion, cobrado, medio_pago=MedioPago.EFECTIVO, ahora=LUNES_8AM + minutos(55))
    listo = turno_listo(sesion, placa="CCC333", ahora=LUNES_8AM + minutos(1))

    cerrada_p = parqueo.abrir_caja(sesion, base=50_000, ahora=antes)
    salio = parqueo.registrar_entrada(sesion, placa="PAR111", tipo=TipoParqueo.CARRO, ahora=antes)
    parqueo.cobrar_salida(
        sesion, salio.id, medio_pago=MedioPago.EFECTIVO, ahora=antes + minutos(50)
    )
    parqueo.cerrar_caja(sesion, cerrada_p.id, efectivo_contado=53_000, ahora=antes + minutos(60))
    abierta_p = parqueo.abrir_caja(sesion, base=50_000, ahora=antes + minutos(61))
    cobrada = parqueo.registrar_entrada(
        sesion, placa="PAR222", tipo=TipoParqueo.CARRO, ahora=LUNES_8AM
    )
    parqueo.cobrar_salida(
        sesion, cobrada.id, medio_pago=MedioPago.EFECTIVO, ahora=LUNES_8AM + minutos(50)
    )
    adentro = parqueo.registrar_entrada(
        sesion, placa="PAR333", tipo=TipoParqueo.MOTO, ahora=LUNES_8AM
    )
    mensualidad = parqueo.crear_mensualidad(
        sesion,
        placa="MEN123",
        tipo=TipoParqueo.CARRO,
        titular="Ana Ruiz",
        valor_mensual=120_000,
        autorizo_datos=True,
        ahora=LUNES_8AM,
    )
    parqueo.pagar_mensualidad(
        sesion,
        mensualidad.id,
        desde=LUNES_8AM.date(),
        hasta=LUNES_8AM.date() + timedelta(days=29),
        valor=120_000,
        medio_pago=MedioPago.NEQUI,
        ahora=LUNES_8AM + minutos(5),
    )
    sesion.commit()
    return {
        "caja_lavadero_cerrada": cerrada.id,
        "caja_lavadero": abierta.id,
        "cobrado": cobrado,
        "listo": listo,
        "basico": servicio(sesion, "Básico").id,
        "automovil": tipo_vehiculo(sesion, "Automóvil").id,
        "caja_parqueadero_cerrada": cerrada_p.id,
        "caja_parqueadero": abierta_p.id,
        "salida_cobrada": cobrada.id,
        "adentro": adentro.id,
        "mensualidad": mensualidad.id,
        "pago": sesion.scalars(select(PagoMensualidad)).one().id,
        "fila": sesion.scalars(select(Fila)).first().id,
        "tipo": sesion.scalars(select(TipoVehiculo)).first().id,
    }


V = "<valor>"  # donde va el valor absurdo
# (qué es, la dirección, lo que se envía, si es plata: entonces debe decir "revisa los ceros")
FORMULARIOS = [
    ("abrir la caja del lavadero", "/caja/abrir", {"base": V}, True),
    ("abrir la caja del parqueadero", "/parqueadero/caja/abrir", {"base": V}, True),
    (
        "cerrar la caja del lavadero",
        "/caja/cerrar",
        {"caja_id": "{caja_lavadero}", "efectivo_contado": V},
        True,
    ),
    (
        "cerrar la caja del parqueadero",
        "/parqueadero/caja/cerrar",
        {"caja_id": "{caja_parqueadero}", "efectivo_contado": V},
        True,
    ),
    (
        "corregir la base de una caja del lavadero",
        "/caja/{caja_lavadero_cerrada}/corregir-base",
        {"base": V, "motivo": "Se contó mal"},
        True,
    ),
    (
        "corregir el conteo de una caja del lavadero",
        "/caja/{caja_lavadero_cerrada}/corregir-conteo",
        {"efectivo_contado": V, "motivo": "Se contó mal"},
        True,
    ),
    (
        "corregir la base de una caja del parqueadero",
        "/parqueadero/caja/{caja_parqueadero}/corregir-base",
        {"base": V, "motivo": "Se contó mal"},
        True,
    ),
    (
        "corregir el conteo de una caja del parqueadero",
        "/parqueadero/caja/{caja_parqueadero_cerrada}/corregir-conteo",
        {"efectivo_contado": V, "motivo": "Se contó mal"},
        True,
    ),
    (
        "cobrar un turno",
        "/turnos/{listo}/cobrar",
        {"servicio_id": "{basico}", "medio_pago": "efectivo", "precio_cobrado": V, "motivo": "x"},
        True,
    ),
    (
        "corregir el cobro de un turno",
        "/turnos/{cobrado}/corregir-cobro",
        {"valor": V, "medio_pago": "efectivo", "motivo": "Era otro valor"},
        True,
    ),
    (
        "cobrar una salida",
        "/parqueadero/{adentro}/salida",
        {"medio_pago": "efectivo", "valor_cobrado": V, "motivo": "x"},
        True,
    ),
    (
        "lo que entregó el cliente",
        "/parqueadero/{adentro}/salida",
        {"medio_pago": "efectivo", "recibido": V},
        True,
    ),
    (
        "corregir el cobro de una salida",
        "/parqueadero/{salida_cobrada}/corregir",
        {"valor": V, "medio_pago": "efectivo", "motivo": "Era otro valor"},
        True,
    ),
    (
        "crear una mensualidad",
        "/parqueadero/mensualidades",
        {
            "placa": "NUE123",
            "tipo": "carro",
            "titular": "Ana",
            "valor_mensual": V,
            "autorizo": "si",
        },
        True,
    ),
    (
        "cambiar el valor de una mensualidad",
        "/parqueadero/mensualidades/{mensualidad}",
        {"placa": "MEN123", "tipo": "carro", "titular": "Ana Ruiz", "valor_mensual": V},
        True,
    ),
    (
        "cobrar una mensualidad",
        "/parqueadero/mensualidades/{mensualidad}/pagar",
        {"desde": "2026-11-25", "hasta": "2026-12-24", "valor": V, "medio_pago": "efectivo"},
        True,
    ),
    (
        "corregir el pago de una mensualidad",
        "/parqueadero/mensualidades/pagos/{pago}/corregir",
        {"valor": V, "medio_pago": "efectivo", "motivo": "Era otro valor"},
        True,
    ),
    *[
        (f"la tarifa: {campo}", "/parqueadero/tarifas", {**TARIFAS, campo: V}, True)
        for campo in (
            "hora_carro",
            "hora_moto",
            "fraccion_carro",
            "fraccion_moto",
            "dia_carro",
            "dia_moto",
            "dia_siguiente_carro",
            "dia_siguiente_moto",
        )
    ],
    ("la tarifa: gracia", "/parqueadero/tarifas", {**TARIFAS, "gracia_minutos": V}, False),
    ("la tarifa: ancho", "/parqueadero/tarifas", {**TARIFAS, "ancho_ticket": V}, False),
    (
        "cobrar una mensualidad desde una salida",
        "/parqueadero/mensualidades/{mensualidad}/pagar",
        {
            "desde": "2026-11-25",
            "hasta": "2026-12-24",
            "valor": "120.000",
            "medio_pago": "efectivo",
            "salida": V,
        },
        False,
    ),
    (
        "un servicio nuevo: precio",
        "/configuracion/servicios",
        {"fila_id": "{fila}", "nombre": "Nuevo", "precio-{tipo}": V, "duracion": "60"},
        False,
    ),
    (
        "un servicio nuevo: duración",
        "/configuracion/servicios",
        {"fila_id": "{fila}", "nombre": "Nuevo", "precio-{tipo}": "10.000", "duracion": V},
        False,
    ),
    ("un servicio nuevo: fila", "/configuracion/servicios", {"fila_id": V}, False),
    ("un puesto nuevo", "/configuracion/puestos", {"fila_id": V}, False),
    ("el recibo: ancho", "/configuracion/recibo", {"ancho_ticket": V}, False),
    (
        "un turno nuevo: cliente",
        "/turnos",
        {
            "cliente_id": V,
            "tipo_vehiculo_id": "{automovil}",
            "placa": "ZZZ999",
            "servicio_id": "{basico}",
            "acepta_datos": "si",
        },
        False,
    ),
    (
        "un turno nuevo: tipo y servicio",
        "/turnos",
        {"tipo_vehiculo_id": V, "placa": "ZZZ999", "servicio_id": V, "acepta_datos": "si"},
        False,
    ),
    (
        "corregir un turno",
        "/turnos/{listo}/corregir",
        {"tipo_vehiculo_id": V, "servicio_id": V, "placa": "CCC333"},
        False,
    ),
]
DIRECCIONES = [
    "/caja?quien=" + V,
    "/parqueadero/caja?quien=" + V,
    "/caja?pagina=" + V,
    "/parqueadero/salida?q=" + V,
    "/balance?periodo=rango&desde=" + V + "&hasta=" + V,
    "/parqueadero/balance?periodo=rango&desde=" + V + "&hasta=" + V,
    "/caja/" + V,
    "/parqueadero/caja/" + V,
    "/turnos/" + V + "/recibo",
]


def _con(plantilla, datos: dict, valor: str):
    """La plantilla con los números del escenario y el valor absurdo puestos."""
    if isinstance(plantilla, dict):
        return {_con(k, datos, valor): _con(v, datos, valor) for k, v in plantilla.items()}
    return plantilla.replace(V, valor).format(**datos)


def _dice(respuesta) -> str:
    return respuesta.headers.get("HX-Trigger", "") + respuesta.text


def _otro_pedido(sesion: Session) -> None:
    """En el servidor cada pedido tiene su sesión, que se cierra si hubo un error; en la prueba
    todos comparten una, y PostgreSQL no sigue hasta deshacer lo que quedó a medias."""
    sesion.rollback()


@pytest.mark.parametrize(("que", "ruta", "datos", "es_plata"), FORMULARIOS, ids=lambda x: x)
def test_ningun_formulario_se_cae(
    adentro: TestClient,
    sesion: Session,
    los_dos_negocios: dict,
    que: str,
    ruta: str,
    datos: dict,
    es_plata: bool,
):
    for valor in ABSURDOS:
        _otro_pedido(sesion)
        respuesta = adentro.post(
            _con(ruta, los_dos_negocios, valor),
            data=_con(datos, los_dos_negocios, valor),
            headers=HTMX,
        )

        assert respuesta.status_code < 500, (que, valor[:5])
        if es_plata and valor != SUPERINDICE:
            assert REVISA_LOS_CEROS in _dice(respuesta), (que, valor[:5])


@pytest.mark.parametrize("direccion", DIRECCIONES)
def test_ninguna_direccion_se_cae(
    adentro: TestClient, sesion: Session, los_dos_negocios: dict, direccion: str
):
    for valor in ABSURDOS:
        _otro_pedido(sesion)
        respuesta = adentro.get(_con(direccion, los_dos_negocios, valor), headers=HTMX)

        assert respuesta.status_code < 500, (direccion, valor[:5])


@pytest.mark.parametrize(
    "campo",
    ["gracia_minutos", "avisar_cuando_falten", "sesion_bot_minutos", "dias_atencion", "precio"],
)
def test_la_configuracion_no_se_cae(adentro: TestClient, sesion: Session, campo: str):
    basico, automovil = servicio(sesion, "Básico").id, tipo_vehiculo(sesion, "Automóvil").id
    nombre = f"precio-{basico}-{automovil}" if campo == "precio" else campo
    for valor in ABSURDOS:
        _otro_pedido(sesion)
        escrito = [valor] if campo == "dias_atencion" else valor
        formulario = formulario_de_configuracion(sesion, **{nombre: escrito})

        respuesta = adentro.post("/configuracion", data=formulario, headers=HTMX)

        assert respuesta.status_code < 500, (campo, valor[:5])


# --- Lo que no cabe en la base de datos -----------------------------------------------------


def test_un_id_que_no_cabe_se_explica(
    adentro: TestClient, sesion: Session, caplog: pytest.LogCaptureFixture
):
    caplog.set_level(logging.WARNING)

    en_la_pagina = adentro.get(f"/caja/{VEINTE_CIFRAS}")
    _otro_pedido(sesion)
    con_htmx = adentro.get(f"/caja/{VEINTE_CIFRAS}", headers=HTMX)

    assert en_la_pagina.status_code == 400
    assert "Ese valor no es posible: revisa lo que escribiste." in en_la_pagina.text
    assert error(con_htmx) == "Ese valor no es posible: revisa lo que escribiste."
    assert con_htmx.headers["HX-Reswap"] == "none"
    # Al registro va qué pasó y dónde, nunca la consulta con sus datos.
    assert "Un valor que no cabe en la base de datos: GET /caja/" in caplog.text
    assert "NumericValueOutOfRange" in caplog.text
    assert "SELECT" not in caplog.text and "parameters" not in caplog.text
