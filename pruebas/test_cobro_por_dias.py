"""El cobro por días: el que deja el vehículo varios días no paga 96 horas, sino días.

Con carro a $3.000 la hora, día completo a $15.000 y desde el segundo día a $6.600:
2 horas son $6.000; 6 horas ya pasan el día y son $15.000; 4 días son
$15.000 + 3 × $6.600 = $34.800.
"""

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.parqueadero import casos_de_uso as casos
from app.parqueadero.errores import TarifaInvalida
from app.parqueadero.estados import TipoParqueo
from app.parqueadero.reglas import Tarifa, calcular_cobro, validar_tarifa
from app.parqueadero.tablas import Parqueo
from app.parqueadero.tarifas import guardar_ajustes, leer_ajustes
from app.turnos.estados import MedioPago
from pruebas.fabricas import LUNES_8AM, debe_rechazar, minutos
from pruebas.navegador import HTMX, aviso

CARRO = Tarifa(valor_hora=3000, gracia_minutos=15, valor_dia=15000, valor_dia_siguiente=6600)
MOTO = Tarifa(valor_hora=1500, gracia_minutos=15, valor_dia=10000)  # todos los días igual
HORA = 60
DIA = 24 * HORA


# --- La regla ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tiempo", "valor", "detalle"),
    [
        (1 * HORA, 3000, "1 hora × $3.000"),
        (2 * HORA, 6000, "2 horas × $3.000"),
        (4 * HORA + 15, 12000, "4 horas × $3.000"),  # la gracia sigue igual
        (5 * HORA, 15000, "1 día: $15.000"),  # 5 horas ($15.000) ya valen el día
        (6 * HORA, 15000, "1 día: $15.000"),
        (DIA, 15000, "1 día: $15.000"),
        (DIA + 10, 15000, "1 día: $15.000"),  # dentro de la gracia de la hora 24
        (DIA + 1 * HORA, 18000, "1 día: $15.000 + 1 hora × $3.000"),
        (DIA + 2 * HORA, 21000, "1 día: $15.000 + 2 horas × $3.000"),
        (DIA + 3 * HORA, 21600, "2 días: $15.000 + $6.600"),  # $9.000 ya pasan el día
        (4 * DIA, 34800, "4 días: $15.000 + 3 × $6.600"),
        (4 * DIA + 2 * HORA, 40800, "4 días: $15.000 + 3 × $6.600 + 2 horas × $3.000"),
        (30 * DIA, 15000 + 29 * 6600, "30 días: $15.000 + 29 × $6.600"),
    ],
)
def test_lo_que_paga_un_carro_por_dias(tiempo: int, valor: int, detalle: str):
    cobro = calcular_cobro(tiempo, CARRO)

    assert (cobro.valor, cobro.detalle) == (valor, detalle)


def test_si_todos_los_dias_valen_lo_mismo():
    assert calcular_cobro(7 * HORA, MOTO).valor == 10000  # 7 h × $1.500 = $10.500: el día
    assert calcular_cobro(4 * DIA, MOTO).detalle == "4 días × $10.000"
    assert calcular_cobro(4 * DIA, MOTO).valor == 40000


def test_sin_valor_del_dia_todo_sigue_por_horas():
    solo_horas = Tarifa(valor_hora=3000, gracia_minutos=15)

    assert calcular_cobro(4 * DIA, solo_horas).valor == 96 * 3000
    assert calcular_cobro(2 * HORA, solo_horas).detalle == "2 horas × $3.000"


def test_los_primeros_15_minutos_no_cambian():
    con_fraccion = replace(CARRO, valor_fraccion=1500)

    cobro = calcular_cobro(10, con_fraccion)

    assert (cobro.valor, cobro.detalle) == (1500, "Primeros 15 minutos: $1.500")


def test_el_cobro_nunca_baja_con_mas_tiempo():
    """Quedarse un minuto más nunca sale más barato."""
    anterior = 0
    for tiempo in range(0, 6 * DIA, 7):
        valor = calcular_cobro(tiempo, CARRO).valor
        assert valor >= anterior, tiempo
        anterior = valor


@pytest.mark.parametrize(
    ("tarifa", "error"),
    [
        (Tarifa(3000, 15, valor_dia=2000), "no puede valer menos que una hora"),
        (Tarifa(3000, 15, valor_dia_siguiente=6600), "escribe primero el día completo"),
        (Tarifa(3000, 15, valor_dia=15000, valor_dia_siguiente=0), "mayor que cero"),
    ],
)
def test_valores_del_dia_que_no_tienen_sentido(tarifa: Tarifa, error: str):
    with pytest.raises(TarifaInvalida, match=error):
        validar_tarifa(tarifa)


# --- En las entradas y salidas -------------------------------------------------------------


def con_cobro_por_dias(sesion: Session, **valores) -> None:
    guardar_ajustes(sesion, replace(leer_ajustes(sesion), **valores))


def test_cada_entrada_guarda_el_cobro_por_dias_con_que_entro(sesion: Session):
    con_cobro_por_dias(sesion, dia_carro=15000, dia_siguiente_carro=6600, dia_moto=10000)
    carro = casos.registrar_entrada(sesion, placa="ABC123", tipo=TipoParqueo.CARRO, ahora=LUNES_8AM)
    moto = casos.registrar_entrada(sesion, placa="ABC12D", tipo=TipoParqueo.MOTO, ahora=LUNES_8AM)
    # Si después cambian las tarifas, los que ya están adentro pagan las de cuando entraron.
    con_cobro_por_dias(sesion, dia_carro=50000, dia_siguiente_carro=None, dia_moto=None)

    assert (carro.valor_dia, carro.valor_dia_siguiente) == (15000, 6600)
    assert (moto.valor_dia, moto.valor_dia_siguiente) == (10000, None)
    assert casos.liquidar(sesion, carro, LUNES_8AM + minutos(4 * DIA)).cobro.valor == 34800
    assert casos.liquidar(sesion, moto, LUNES_8AM + minutos(4 * DIA)).cobro.valor == 40000


def test_cuatro_dias_de_carro_se_cobran_por_dias(sesion: Session):
    con_cobro_por_dias(sesion, dia_carro=15000, dia_siguiente_carro=6600)
    casos.abrir_caja(sesion, base=0, ahora=LUNES_8AM)
    carro = casos.registrar_entrada(sesion, placa="ABC123", tipo=TipoParqueo.CARRO, ahora=LUNES_8AM)

    casos.cobrar_salida(
        sesion, carro.id, medio_pago=MedioPago.EFECTIVO, ahora=LUNES_8AM + minutos(4 * DIA)
    )

    assert (carro.horas_cobradas, carro.valor_calculado, carro.valor_cobrado) == (96, 34800, 34800)
    assert carro.motivo_ajuste is None  # es la tarifa, no un descuento


def test_las_entradas_de_antes_siguen_por_horas(sesion: Session):
    """Las que entraron antes de este cambio no tienen valor del día: todo por horas."""
    carro = casos.registrar_entrada(sesion, placa="ABC123", tipo=TipoParqueo.CARRO, ahora=LUNES_8AM)

    assert carro.valor_dia is None
    assert casos.liquidar(sesion, carro, LUNES_8AM + minutos(DIA)).cobro.valor == 24 * 3000


def test_la_base_no_guarda_un_dia_mas_barato_que_una_hora(sesion: Session):
    carro = casos.registrar_entrada(sesion, placa="ABC123", tipo=TipoParqueo.CARRO, ahora=LUNES_8AM)

    with debe_rechazar(sesion, "ck_parqueo_dia_valido"):
        carro.valor_dia = 1000
    sesion.refresh(carro)
    with debe_rechazar(sesion, "ck_parqueo_dia_siguiente_valido"):
        carro.valor_dia_siguiente = 6600  # sin valor del día


# --- En las pantallas ----------------------------------------------------------------------

TARIFAS = {
    "hora_carro": "3.000",
    "hora_moto": "1.500",
    "gracia_minutos": "15",
    "nombre": "Parqueadero",
    "ancho_ticket": "80",
}


def test_guardar_el_cobro_por_dias_y_cotizar(adentro: TestClient, sesion: Session):
    pagina = adentro.get("/parqueadero/tarifas").text
    assert "Cobro por días" in pagina and "Así se cobra con lo guardado" not in pagina
    assert "mensualidad máxima de tu categoría dividida entre 30" in pagina

    respuesta = adentro.post(
        "/parqueadero/tarifas",
        data={
            **TARIFAS,
            "dia_carro": "15.000",
            "dia_siguiente_carro": "6.600",
            "dia_moto": "10.000",
        },
        headers=HTMX,
    )

    assert "Tarifas guardadas" in aviso(respuesta)
    ajustes = leer_ajustes(sesion)
    assert (ajustes.dia_carro, ajustes.dia_siguiente_carro) == (15000, 6600)
    assert (ajustes.dia_moto, ajustes.dia_siguiente_moto) == (10000, None)
    # Para responder en el mostrador "¿cuánto me cobra por 4 días?".
    assert "Así se cobra con lo guardado" in respuesta.text
    # Una fila por cantidad de días: carro y moto, 4 días.
    assert '<th scope="row">4 días</th><td>$34.800</td><td>$40.000</td>' in respuesta.text
    assert '<th scope="row">15 días</th><td>$107.400</td><td>$150.000</td>' in respuesta.text
    assert 'value="6.600"' in respuesta.text


def test_la_cotizacion_solo_trae_los_vehiculos_con_cobro_por_dias(adentro: TestClient):
    respuesta = adentro.post(
        "/parqueadero/tarifas", data={**TARIFAS, "dia_carro": "15.000"}, headers=HTMX
    )

    assert '<th scope="col">Días seguidos</th><th scope="col">Carro</th></tr>' in respuesta.text
    assert '<th scope="row">4 días</th><td>$60.000</td></tr>' in respuesta.text  # 4 × $15.000


def test_un_dia_mas_barato_que_una_hora_no_se_guarda(adentro: TestClient, sesion: Session):
    respuesta = adentro.post(
        "/parqueadero/tarifas", data={**TARIFAS, "dia_moto": "1.000"}, headers=HTMX
    )

    assert "No se guardó nada: Moto: El día completo no puede valer menos que una hora" in (
        respuesta.text
    )
    assert 'value="1.000"' in respuesta.text  # lo escrito sigue ahí para corregirlo
    assert leer_ajustes(sesion).dia_moto is None


def test_la_salida_y_los_tickets_dicen_como_se_cobro(
    adentro: TestClient, sesion: Session, reloj: dict
):
    adentro.post(
        "/parqueadero/tarifas",
        data={**TARIFAS, "dia_carro": "15.000", "dia_siguiente_carro": "6.600"},
        headers=HTMX,
    )
    adentro.post("/parqueadero/caja/abrir", data={"base": "0"}, headers=HTMX)
    adentro.post("/parqueadero/entradas", data={"placa": "abc123", "tipo": "carro"}, headers=HTMX)
    carro = sesion.scalars(select(Parqueo).where(Parqueo.placa == "ABC123")).one()
    entrada = adentro.get(f"/parqueadero/tickets/{carro.id}").text
    reloj["ahora"] += minutos(4 * DIA + 2 * HORA)

    dialogo = adentro.get(f"/parqueadero/{carro.id}/salida", headers=HTMX).text
    adentro.post(f"/parqueadero/{carro.id}/salida", data={"medio_pago": "efectivo"}, headers=HTMX)
    recibo = adentro.get(f"/parqueadero/tickets/{carro.id}/salida").text

    assert "Día completo" in entrada and "$15.000; desde el 2.º, $6.600" in entrada
    detalle = "4 días: $15.000 + 3 × $6.600 + 2 horas × $3.000"
    assert detalle in dialogo and "$40.800" in dialogo
    assert "El día completo, máximo $15.000, y desde el segundo día $6.600 cada uno." in dialogo
    assert detalle in recibo and "$40.800" in recibo
