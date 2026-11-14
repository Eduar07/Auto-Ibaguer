"""Los cierres anteriores con filtros: desde y hasta qué día, cómo cerró la caja (cuadró, sobró,
faltó o se corrigió después) y quién la cerró, con las cuentas de todo lo filtrado, de a 30
por página, y el archivo para Excel.

Lo que tiene que cumplirse siempre:
- el día de un cierre es el día en que se abrió la caja, como en el Balance;
- las cuentas (cuántos, lo que entró, lo que sobró y lo que faltó) son de todo lo filtrado,
  no solo de la página que se ve;
- lo que llega raro en la dirección se ignora: nunca un error.
"""

import csv
import io
from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.parqueadero import casos_de_uso as casos
from app.parqueadero import consultas
from app.parqueadero.consultas import FiltroDeCierres
from app.parqueadero.estados import TipoParqueo
from app.parqueadero.tablas import CajaParqueadero
from app.turnos.estados import MedioPago
from app.web.administradores import guardar_administrador
from app.web.tablas import Administrador
from pruebas.fabricas import BOGOTA

OCTUBRE = 10


def a_las(dia: int, hora: int) -> datetime:
    return datetime(2026, OCTUBRE, dia, hora, tzinfo=BOGOTA)


def cerrada_el(
    sesion: Session, dia: int, contado: int, por: Administrador | None = None
) -> CajaParqueadero:
    """La caja de ese día de octubre: un carro pagó $3.000 en efectivo (sin base) y al cerrar
    se contó `contado`. Cuadra con $3.000."""
    caja = casos.abrir_caja(sesion, base=0, ahora=a_las(dia, 6))
    parqueo = casos.registrar_entrada(
        sesion, placa=f"ABC{dia:03d}", tipo=TipoParqueo.CARRO, ahora=a_las(dia, 7)
    )
    casos.cobrar_salida(sesion, parqueo.id, medio_pago=MedioPago.EFECTIVO, ahora=a_las(dia, 8))
    casos.cerrar_caja(
        sesion,
        caja.id,
        efectivo_contado=contado,
        administrador_id=por.id if por else None,
        ahora=a_las(dia, 20),
    )
    return caja


@pytest.fixture
def dos(sesion: Session) -> tuple[Administrador, Administrador]:
    eduar, _ = guardar_administrador(sesion, "eduar.prueba", "clave-larga-de-eduar")
    ana, _ = guardar_administrador(sesion, "ana", "clave-larga-de-ana")
    return eduar, ana


@pytest.fixture
def octubre(sesion: Session, dos) -> dict[str, CajaParqueadero]:
    """Cinco cierres: el 1 y el 2 cuadraron, el 3 sobraron $1.000, el 4 faltaron $500 y el 5
    faltaron $2.000. Ana cerró el 4 y el 5."""
    eduar, ana = dos
    return {
        "1": cerrada_el(sesion, 1, 3000, eduar),
        "2": cerrada_el(sesion, 2, 3000, eduar),
        "3": cerrada_el(sesion, 3, 4000, eduar),
        "4": cerrada_el(sesion, 4, 2500, ana),
        "5": cerrada_el(sesion, 5, 1000, ana),
    }


def dias(resultado: consultas.Cierres) -> list[int]:
    return [r.caja.abierta_en.astimezone(BOGOTA).day for r in resultado.filas]


# --- Los filtros ----------------------------------------------------------------------------


def test_sin_filtro_todos_del_mas_nuevo_al_mas_viejo(sesion: Session, octubre):
    todos = consultas.cierres(sesion, FiltroDeCierres())

    assert dias(todos) == [5, 4, 3, 2, 1]
    assert (todos.cuantos, todos.entro, todos.cuadraron) == (5, 15000, 2)
    assert (todos.sobrantes, todos.cuantos_sobrantes) == (1000, 1)
    assert (todos.faltantes, todos.cuantos_faltantes) == (2500, 2)


def test_desde_y_hasta_que_dia(sesion: Session, octubre):
    del_2_al_4 = consultas.cierres(
        sesion, FiltroDeCierres(desde=date(2026, 10, 2), hasta=date(2026, 10, 4))
    )

    assert dias(del_2_al_4) == [4, 3, 2]
    assert del_2_al_4.entro == 9000


def test_el_dia_es_el_de_apertura(sesion: Session):
    """La que se abre el 6 y se cierra pasada la medianoche es del 6, como en el Balance."""
    caja = casos.abrir_caja(sesion, base=0, ahora=a_las(6, 18))
    casos.cerrar_caja(sesion, caja.id, efectivo_contado=0, ahora=a_las(7, 1))

    del_6 = consultas.cierres(
        sesion, FiltroDeCierres(desde=date(2026, 10, 6), hasta=date(2026, 10, 6))
    )

    assert [r.caja.id for r in del_6.filas] == [caja.id]


@pytest.mark.parametrize(
    ("resultado", "esperados"),
    [("cuadro", [2, 1]), ("sobro", [3]), ("falto", [5, 4])],
)
def test_como_cerro(sesion: Session, octubre, resultado: str, esperados: list[int]):
    assert dias(consultas.cierres(sesion, FiltroDeCierres(resultado=resultado))) == esperados


def test_las_que_se_corrigieron_despues_del_cierre(sesion: Session, octubre):
    casos.corregir_conteo(
        sesion, octubre["5"].id, efectivo_contado=3000, motivo="Se contó mal", ahora=a_las(6, 9)
    )
    casos.corregir_base(sesion, octubre["2"].id, base=1000, motivo="Había base", ahora=a_las(6, 9))

    corregidas = consultas.cierres(sesion, FiltroDeCierres(resultado="corregida"))

    assert dias(corregidas) == [5, 2]
    # Con la corrección, el 5 ya cuadra y el 2 ya no.
    assert dias(consultas.cierres(sesion, FiltroDeCierres(resultado="cuadro"))) == [5, 1]


def test_quien_cerro(sesion: Session, octubre, dos):
    _, ana = dos

    de_ana = consultas.cierres(sesion, FiltroDeCierres(quien=ana.id))

    assert dias(de_ana) == [5, 4]
    assert [usuario for _, usuario in consultas.quienes_cerraron(sesion)] == ["ana", "eduar.prueba"]


def test_las_cuentas_son_de_todo_lo_filtrado_no_solo_de_la_pagina(sesion: Session):
    for dia in range(1, 31):
        cerrada_el(sesion, dia, 3000)
    for dia in range(1, 6):  # otras cinco el mismo mes: 35 en total
        cerrada_el(sesion, dia, 2000)

    primera = consultas.cierres(sesion, FiltroDeCierres(), pagina=1)
    segunda = consultas.cierres(sesion, FiltroDeCierres(), pagina=2)
    muy_lejos = consultas.cierres(sesion, FiltroDeCierres(), pagina=99)

    assert (len(primera.filas), len(segunda.filas)) == (30, 5)
    assert (primera.cuantos, primera.paginas, primera.entro) == (35, 2, 105000)
    assert (primera.faltantes, primera.cuantos_faltantes) == (5000, 5)
    assert muy_lejos.pagina == 2  # la última que hay
    assert consultas.cierres(sesion, FiltroDeCierres(), pagina=0).pagina == 1


# --- En la pantalla -------------------------------------------------------------------------


def test_la_caja_con_filtros(adentro: TestClient, sesion: Session, octubre):
    sesion.commit()

    pagina = adentro.get(
        "/parqueadero/caja", params={"desde": "2026-10-03", "resultado": "falto"}
    ).text

    assert 'name="desde" type="date" value="2026-10-03"' in pagina
    assert '<option value="falto" selected>Faltó</option>' in pagina
    assert "<strong>2 cierres</strong> · entraron $6.000" in pagina
    assert "faltaron $2.500 en 2" in pagina
    assert "Quitar filtros" in pagina
    assert 'href="/parqueadero/exportar-cierres?desde=2026-10-03&amp;resultado=falto"' in pagina
    assert "Descargar estos cierres para Excel" in pagina


def test_quien_cerro_sale_cuando_hay_mas_de_uno(adentro: TestClient, sesion: Session, octubre):
    sesion.commit()

    pagina = adentro.get("/parqueadero/caja").text

    assert '<label for="cierres-quien">Quién cerró</label>' in pagina
    assert ">ana</option>" in pagina


def test_si_ninguno_cumple_el_filtro(adentro: TestClient, sesion: Session, octubre):
    sesion.commit()

    pagina = adentro.get("/parqueadero/caja", params={"desde": "2026-11-01"}).text

    assert "Ningún cierre cumple ese filtro" in pagina


def test_lo_raro_en_la_direccion_se_ignora(adentro: TestClient, sesion: Session, octubre):
    sesion.commit()

    pagina = adentro.get(
        "/parqueadero/caja",
        params={
            "desde": "2026-10-04",
            "hasta": "2026-10-02",  # al revés: se voltea
            "resultado": "x",
            "quien": "nadie",
            "pagina": "-3",
        },
    )

    assert pagina.status_code == 200
    assert "<strong>3 cierres</strong>" in pagina.text


def test_de_a_30_por_pagina_con_los_filtros_en_los_enlaces(adentro: TestClient, sesion: Session):
    for dia in range(1, 31):
        cerrada_el(sesion, dia, 3000)
    for dia in range(1, 6):
        cerrada_el(sesion, dia, 2000)
    sesion.commit()

    primera = adentro.get("/parqueadero/caja", params={"hasta": "2026-10-31"}).text
    segunda = adentro.get("/parqueadero/caja", params={"hasta": "2026-10-31", "pagina": 2}).text

    assert "Página 1 de 2" in primera
    assert 'href="/parqueadero/caja?hasta=2026-10-31&amp;pagina=2#t-cerradas"' in primera
    assert "Página 2 de 2" in segunda and "Más nuevos" in segunda


def test_los_cierres_para_excel(adentro: TestClient, sesion: Session, octubre):
    casos.corregir_conteo(
        sesion, octubre["5"].id, efectivo_contado=3000, motivo="Se contó mal", ahora=a_las(6, 9)
    )
    sesion.commit()

    respuesta = adentro.get(
        "/parqueadero/exportar-cierres", params={"desde": "2026-10-04", "hasta": "2026-10-05"}
    )

    assert respuesta.headers["content-type"].startswith("text/csv")
    assert (
        'filename="cierres-2026-10-04-a-2026-10-05.csv"' in respuesta.headers["content-disposition"]
    )
    assert respuesta.text.startswith("﻿Caja;Abrió;")
    filas = list(csv.reader(io.StringIO(respuesta.text.lstrip("﻿")), delimiter=";"))
    encabezado, *datos = filas
    assert len(datos) == 2  # del 4 y del 5, del más viejo al más nuevo
    del_5 = dict(zip(encabezado, datos[1], strict=True))
    assert (del_5["Efectivo"], del_5["Entró en total"]) == ("3000", "3000")
    assert (del_5["Contado"], del_5["Diferencia"]) == ("3000", "0")
    assert del_5["Corregida después del cierre"] == "Sí"
    assert del_5["Cerró por"] == "ana"


def test_sin_sesion_no_se_descargan(web: TestClient):
    respuesta = web.get("/parqueadero/exportar-cierres", follow_redirects=False)

    assert respuesta.status_code == 303 and respuesta.headers["location"] == "/entrar"
