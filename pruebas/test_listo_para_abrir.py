"""Lista para abrir: dice qué falta configurar antes de trabajar en serio, y deja de decirlo
cuando ya está."""

from dataclasses import replace

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app import listo
from app.parqueadero.tarifas import guardar_ajustes, leer_ajustes
from app.turnos import recibo
from pruebas.test_recibo_del_lavadero import ajustes


def punto(grupos, que: str) -> listo.Punto:
    return next(p for g in grupos for p in g.puntos if p.que == que)


def test_recien_instalado_falta_lo_basico(sesion: Session):
    grupos = listo.revisar(sesion)

    assert punto(grupos, "Tarifas guardadas").listo is False
    assert punto(grupos, "Teléfono del negocio").listo is False  # el de ejemplo
    assert punto(grupos, "NIT y dirección en el recibo").listo is False
    assert punto(grupos, "Render en el plan de pago").listo is None  # recordatorio
    assert listo.pendientes(grupos) >= 5


def test_lo_que_se_configura_queda_listo(sesion: Session):

    guardar_ajustes(
        sesion,
        replace(
            leer_ajustes(sesion),
            nit="900.123.456-7",
            direccion="Calle 1",
            telefono="322 000 0000",
            correos_reporte="jefe@x.co",
        ),
    )
    recibo.guardar_ajustes(
        sesion, ajustes(nit="900.123.456-7", direccion="Calle 1", correos_reporte="jefe@x.co")
    )

    grupos = listo.revisar(sesion)

    assert punto(grupos, "Tarifas guardadas").listo is True
    assert punto(grupos, "NIT, dirección y teléfono en el ticket").listo is True
    assert punto(grupos, "NIT y dirección en el recibo").listo is True
    assert punto(grupos, "Correos de los jefes para el reporte del día").listo is True


def test_en_la_pantalla_y_desde_elegir(adentro: TestClient):
    pagina = adentro.get("/listo").text
    inicio = adentro.get("/inicio").text

    assert "<h1>Lista para abrir</h1>" in pagina and 'href="/parqueadero/tarifas"' in pagina
    assert 'href="/listo"' in inicio and "pendientes)" in inicio
