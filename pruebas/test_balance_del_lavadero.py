"""El Balance del lavadero: lo que se cobró (por medio, servicio y tipo de vehículo), los turnos
pedidos (por WhatsApp o en el local, cancelados y los que no llegaron), cuánto dura un lavado,
los descuentos, las cajas y sus correcciones, en un día, una semana, un mes o un rango.

Lo que tiene que cumplirse siempre:
- cada cobro cuenta el día en que se hizo, en Bogotá: lo de ayer no entra en el día de hoy;
- las cuentas cuadran por cualquier camino: el total es la suma por medio, por servicio y por
  tipo de vehículo, y la de los días;
- el reporte del día lleva plata y placas, nunca nombres ni celulares de los clientes.
"""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.caja.periodos import periodo
from app.turnos import balance
from app.turnos import caja as caja_lavadero
from app.turnos import casos_de_uso as casos
from app.turnos.estados import Actor, MedioPago
from app.web.balance_del_lavadero import texto_del_reporte
from pruebas.fabricas import LUNES_8AM, minutos, por_whatsapp, turno_listo

HOY = LUNES_8AM.date()
AYER = timedelta(days=1)


@pytest.fixture
def un_dia(sesion: Session):
    """El lunes: dos cobros (un carro en efectivo; una camioneta por Nequi, con $5.000 de
    descuento), uno por WhatsApp que se canceló, y un cobro de ayer que hoy no cuenta."""
    caja = caja_lavadero.abrir_caja(sesion, base=20_000, ahora=LUNES_8AM - AYER)
    ayer = turno_listo(sesion, placa="AAA111", ahora=LUNES_8AM - AYER)
    casos.cobrar(sesion, ayer, medio_pago=MedioPago.EFECTIVO, ahora=LUNES_8AM - AYER + minutos(60))
    carro = turno_listo(sesion, placa="ABC123")
    casos.cobrar(sesion, carro, medio_pago=MedioPago.EFECTIVO, ahora=LUNES_8AM + minutos(60))
    camioneta = turno_listo(sesion, placa="XYZ789", tipo="Camioneta", ahora=LUNES_8AM + minutos(5))
    casos.cobrar(
        sesion,
        camioneta,
        medio_pago=MedioPago.NEQUI,
        precio_cobrado=45_000,
        motivo="Cliente frecuente",
        ahora=LUNES_8AM + minutos(70),
    )
    cancelado = por_whatsapp(sesion).turno.id
    casos.cancelar(sesion, cancelado, actor=Actor.ADMINISTRADOR, ahora=LUNES_8AM + minutos(80))
    return caja


def test_el_balance_del_dia(sesion: Session, un_dia):
    b = balance.balance(sesion, periodo("dia", HOY))

    assert (b.cobrado, b.cobros, b.ticket_promedio) == (85_000, 2, 42_500)
    assert b.por_medio == {MedioPago.EFECTIVO: 40_000, MedioPago.NEQUI: 45_000}
    assert [(r.nombre, r.cobros, r.cobrado) for r in b.por_servicio] == [("Básico", 2, 85_000)]
    assert [(r.nombre, r.cobrado) for r in b.por_tipo] == [
        ("Camioneta", 45_000),
        ("Automóvil", 40_000),
    ]
    assert (b.descuentos, b.con_descuento, b.recargos, b.cuantos_ajustes) == (5_000, 1, 0, 1)
    assert [(a.placa, a.menu, a.cobrado, a.motivo) for a in b.ajustes] == [
        ("XYZ789", 50_000, 45_000, "Cliente frecuente")
    ]
    # Tres turnos pedidos para hoy: dos en el local (los cobrados) y uno por WhatsApp, cancelado.
    assert (b.turnos, b.por_whatsapp, b.en_el_local, b.cancelados) == (3, 1, 2, 1)
    assert b.lavado_promedio_min == 50  # de "pasó a puesto" a "listo"
    assert len(b.cajas) == 0  # la caja se abrió ayer: es del día de ayer


def test_las_cuentas_cuadran_por_cualquier_camino(sesion: Session, un_dia):
    mes = balance.balance(sesion, periodo("mes", HOY))
    semana = balance.balance(sesion, periodo("semana", HOY))

    assert mes.cobrado == 125_000  # el domingo y el lunes
    assert semana.cobrado == 85_000  # la semana va de lunes a domingo: el domingo es de la anterior
    assert sum(mes.por_medio.values()) == mes.cobrado
    assert sum(r.cobrado for r in mes.por_servicio) == mes.cobrado
    assert sum(r.cobrado for r in mes.por_tipo) == mes.cobrado
    assert sum(d.cobrado for d in mes.dias) == mes.cobrado
    assert balance.total_cobrado(sesion, periodo("dia", HOY - AYER)) == 40_000


def test_cada_caja_es_del_dia_en_que_se_abrio(sesion: Session, un_dia):
    caja = un_dia
    caja_lavadero.cerrar_caja(
        sesion, caja.id, efectivo_contado=95_000, ahora=LUNES_8AM + minutos(600)
    )

    ayer = balance.balance(sesion, periodo("dia", HOY - AYER))

    assert [r.caja.id for r in ayer.cajas] == [caja.id]
    assert ayer.faltantes == [5_000]  # debía haber 20.000 + 40.000 + 40.000 = 100.000


def test_el_reporte_del_dia(sesion: Session, un_dia):
    texto = texto_del_reporte(balance.balance(sesion, periodo("dia", HOY)), "Lavadero")

    assert texto.startswith("*Lavadero · Lunes 26 de octubre de 2026*")
    assert "Cobrado: *$85.000* en 2 turnos (promedio $42.500)" in texto
    assert "Efectivo $40.000 · Nequi $45.000" in texto
    assert "Turnos pedidos: 3 (WhatsApp 1, en el local 2) · cancelados 1 · no llegaron 0" in texto
    assert "Descuentos: $5.000 en 1 turno" in texto
    assert "Cliente" not in texto.replace("Cliente frecuente", "")  # sin nombres de clientes


# --- En la pantalla -------------------------------------------------------------------------------


def test_el_balance_del_dia_en_la_pantalla(adentro: TestClient, sesion: Session, un_dia):
    sesion.commit()

    pagina = adentro.get("/balance").text

    assert "<h1>Balance del lavadero</h1>" in pagina
    assert pagina.count('<a href="/balance" aria-current="page">') == 2
    assert "<strong>$85.000</strong>" in pagina
    assert "https://wa.me/?text=" in pagina
    assert 'data-imprimir="/balance/ticket?fecha=2026-10-26"' in pagina
    assert "menú $50.000, cobrado <strong>$45.000</strong> · Cliente frecuente" in pagina


def test_las_graficas_del_mes(adentro: TestClient, sesion: Session, un_dia):
    sesion.commit()

    en_plata = adentro.get("/balance", params={"periodo": "mes", "fecha": "2026-10-26"}).text
    en_turnos = adentro.get(
        "/balance", params={"periodo": "mes", "fecha": "2026-10-26", "ver": "turnos"}
    ).text

    assert "Día por día: plata cobrada" in en_plata
    assert "Día por día: turnos cobrados" in en_turnos
    assert '<h3 id="t-dias">Día por día</h3>' in en_plata


def test_el_reporte_impreso(adentro: TestClient, sesion: Session, un_dia):
    sesion.commit()

    ticket = adentro.get("/balance/ticket", params={"fecha": "2026-10-26"})

    assert ticket.status_code == 200
    assert "Reporte del día · Lavadero" in ticket.text
    assert ticket.headers["X-Frame-Options"] == "SAMEORIGIN"


def test_lo_raro_en_la_direccion_se_ignora(adentro: TestClient):
    respuesta = adentro.get(
        "/balance", params={"periodo": "x", "fecha": "ayer", "ver": "nada", "desde": "no"}
    )

    assert respuesta.status_code == 200
