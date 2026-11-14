"""Corregir un cobro desde la pantalla de Caja, como lo usa el navegador: el botón en cada
cobro, el diálogo, lo que pasa al guardar o al equivocarse, y dónde queda anotado (la caja, su
cierre, su ticket y el Balance)."""

import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.parqueadero import casos_de_uso as casos
from app.parqueadero.estados import EstadoParqueo
from app.parqueadero.tablas import Caja, Mensualidad, PagoMensualidad, Parqueo
from pruebas.fabricas import minutos
from pruebas.navegador import HTMX, aviso, error, eventos

CON_EFECTIVO = {"medio_pago": "efectivo"}


def parqueo(sesion: Session, placa: str) -> Parqueo:
    return sesion.scalars(select(Parqueo).where(Parqueo.placa == placa)).one()


def pago(sesion: Session, placa: str = "MEN123") -> PagoMensualidad:
    return sesion.scalars(
        select(PagoMensualidad)
        .join(Mensualidad, Mensualidad.id == PagoMensualidad.mensualidad_id)
        .where(Mensualidad.placa == placa, PagoMensualidad.caja_id.is_not(None))
    ).one()


@pytest.fixture
def caja_con_cobros(adentro: TestClient, sesion: Session, reloj: dict) -> None:
    """A las 9:00: ABC123 pagó $3.000 en efectivo (1 h) y MEN123 pagó el mes por Nequi."""
    adentro.post("/parqueadero/caja/abrir", data={"base": "50.000"}, headers=HTMX)
    adentro.post("/parqueadero/entradas", data={"placa": "ABC123", "tipo": "carro"}, headers=HTMX)
    adentro.post(
        "/parqueadero/mensualidades",
        data={
            "placa": "MEN123",
            "tipo": "carro",
            "titular": "Ana Ruiz",
            "valor_mensual": "120.000",
            "autorizo": "si",
        },
        headers=HTMX,
    )
    mensualidad = sesion.scalars(select(Mensualidad).where(Mensualidad.placa == "MEN123")).one()
    adentro.post(
        f"/parqueadero/mensualidades/{mensualidad.id}/pagar",
        data={
            "desde": "2026-10-26",
            "hasta": "2026-11-24",
            "valor": "120.000",
            "medio_pago": "nequi",
        },
        headers=HTMX,
    )
    reloj["ahora"] += minutos(60)
    adentro.post(
        f"/parqueadero/{parqueo(sesion, 'ABC123').id}/salida", data=CON_EFECTIVO, headers=HTMX
    )
    assert pago(sesion).valor == 120000 and parqueo(sesion, "ABC123").valor_cobrado == 3000


def corregir(adentro: TestClient, ruta: str, **datos):
    return adentro.post(ruta, data=datos, headers=HTMX)


# --- La caja y el diálogo -----------------------------------------------------------------


def test_cada_cobro_de_la_caja_abierta_se_puede_corregir(
    adentro: TestClient, sesion: Session, caja_con_cobros
):
    pagina = adentro.get("/parqueadero/caja").text

    salida, mes = parqueo(sesion, "ABC123").id, pago(sesion).id
    assert f'hx-get="/parqueadero/{salida}/corregir"' in pagina
    assert f'hx-get="/parqueadero/mensualidades/pagos/{mes}/corregir"' in pagina
    assert "Después del cierre también se puede, hasta 30 días" in pagina
    assert 'id="caja-abierta"' in pagina
    caja = sesion.scalars(select(Caja)).one()
    assert f'hx-get="/parqueadero/caja/{caja.id}/corregir"' in pagina  # la base


def test_el_dialogo_de_una_salida(adentro: TestClient, sesion: Session, caja_con_cobros):
    ticket = parqueo(sesion, "ABC123").id

    dialogo = adentro.get(f"/parqueadero/{ticket}/corregir", headers=HTMX).text

    assert "Corregir el cobro de" in dialogo and "ABC123" in dialogo
    assert f"Salió a las 9:00 a.m. · ticket {ticket}" in dialogo
    assert "se cobró <strong>$3.000 en efectivo</strong>" in dialogo
    assert re.search(r'value="efectivo"\s+checked', dialogo)
    assert 'value="3.000"' in dialogo and "La tarifa daba $3.000." in dialogo
    assert f'hx-post="/parqueadero/{ticket}/deshacer-salida"' in dialogo
    assert "vuelve a quedar adentro con su ticket y su hora de entrada" in dialogo
    assert "los $3.000 en efectivo salen de la caja" in dialogo


def test_el_dialogo_de_un_mes(adentro: TestClient, sesion: Session, caja_con_cobros):
    mes = pago(sesion).id

    dialogo = adentro.get(f"/parqueadero/mensualidades/pagos/{mes}/corregir", headers=HTMX).text

    assert (
        "Mensualidad de Ana Ruiz, del 26 de octubre de 2026 al 24 de noviembre de 2026" in dialogo
    )
    assert "se cobró <strong>$120.000 por Nequi</strong>" in dialogo
    assert "La tarifa daba" not in dialogo
    assert f'hx-post="/parqueadero/mensualidades/pagos/{mes}/anular"' in dialogo
    assert "esos días vuelven a deberse y los $120.000 por Nequi salen de la caja" in dialogo


# --- Guardar --------------------------------------------------------------------------------


def test_cambiar_el_medio_de_pago(adentro: TestClient, sesion: Session, caja_con_cobros):
    ticket = parqueo(sesion, "ABC123").id

    respuesta = corregir(
        adentro,
        f"/parqueadero/{ticket}/corregir",
        valor="3.000",
        medio_pago="nequi",
        motivo="Pagó por Nequi",
    )

    assert aviso(respuesta) == "Listo: el cobro de ABC123 quedó en $3.000 por Nequi."
    assert "cerrar-dialogo" in eventos(respuesta)
    assert respuesta.headers["HX-Retarget"] == "#caja-abierta"
    # La caja vuelve pintada, con la corrección a la vista.
    assert "Correcciones" in respuesta.text and "Pagó por Nequi" in respuesta.text
    assert "Se corrigió el cobro" in respuesta.text and "Efectivo −$3.000" in respuesta.text
    sesion.expire_all()
    assert parqueo(sesion, "ABC123").medio_pago.value == "nequi"


def test_sin_motivo_el_dialogo_vuelve_con_lo_escrito(
    adentro: TestClient, sesion: Session, caja_con_cobros
):
    ticket = parqueo(sesion, "ABC123").id

    respuesta = corregir(
        adentro, f"/parqueadero/{ticket}/corregir", valor="2.000", medio_pago="nequi", motivo=" "
    )

    assert "HX-Retarget" not in respuesta.headers
    assert "Escribe por qué se corrige" in respuesta.text
    assert 'value="2.000"' in respuesta.text
    assert re.search(r'value="nequi"\s+checked', respuesta.text)
    sesion.expire_all()
    assert parqueo(sesion, "ABC123").valor_cobrado == 3000


def test_deshacer_la_salida(adentro: TestClient, sesion: Session, caja_con_cobros):
    ticket = parqueo(sesion, "ABC123").id

    respuesta = corregir(adentro, f"/parqueadero/{ticket}/deshacer-salida", motivo="Era otro carro")

    assert aviso(respuesta) == (
        f"ABC123 volvió a quedar adentro (ticket {ticket}). Salen $3.000 en efectivo de la caja."
    )
    assert "Se deshizo la salida" in respuesta.text
    assert "ABC123" in adentro.get("/parqueadero").text  # otra vez en la lista de adentro
    sesion.expire_all()
    assert parqueo(sesion, "ABC123").estado is EstadoParqueo.ADENTRO


def test_deshacer_si_la_placa_ya_entro_otra_vez_explica_y_guarda_el_motivo(
    adentro: TestClient, sesion: Session, caja_con_cobros
):
    ticket = parqueo(sesion, "ABC123").id
    adentro.post("/parqueadero/entradas", data={"placa": "ABC123", "tipo": "carro"}, headers=HTMX)

    respuesta = corregir(adentro, f"/parqueadero/{ticket}/deshacer-salida", motivo="Era otro carro")

    assert "ABC123 ya volvió a entrar" in respuesta.text
    assert re.search(r'id="motivo-deshacer"[^>]*value="Era otro carro"', respuesta.text)


def test_anular_el_pago_de_un_mes(adentro: TestClient, sesion: Session, caja_con_cobros):
    mes = pago(sesion).id

    respuesta = corregir(
        adentro, f"/parqueadero/mensualidades/pagos/{mes}/anular", motivo="Se cobró dos veces"
    )

    assert aviso(respuesta) == (
        "Se anuló el pago de la mensualidad de MEN123: esos días vuelven a deberse. "
        "Salen $120.000 por Nequi de la caja."
    )
    assert sesion.get(PagoMensualidad, mes) is None
    mensualidades = adentro.get("/parqueadero/mensualidades").text
    assert "MEN123" in mensualidades and "pendiente de pago" in mensualidades.lower()


def test_corregir_el_valor_de_un_mes(adentro: TestClient, sesion: Session, caja_con_cobros):
    mes = pago(sesion).id

    respuesta = corregir(
        adentro,
        f"/parqueadero/mensualidades/pagos/{mes}/corregir",
        valor="100.000",
        medio_pago="nequi",
        motivo="Se le hizo descuento",
    )

    assert aviso(respuesta) == (
        "Listo: el pago de la mensualidad de MEN123 quedó en $100.000 por Nequi."
    )


# --- Cuando ya no se puede ----------------------------------------------------------------


def test_si_la_caja_se_cerro_mientras_tanto_se_corrige_despues_del_cierre(
    adentro: TestClient, sesion: Session, caja_con_cobros
):
    ticket = parqueo(sesion, "ABC123").id
    caja = sesion.scalars(select(Caja)).one()
    casos.cerrar_caja(sesion, caja.id, efectivo_contado=53000)  # desde otro celular
    sesion.commit()

    respuesta = corregir(adentro, f"/parqueadero/{ticket}/deshacer-salida", motivo="Era otro carro")

    assert "volvió a quedar adentro" in aviso(respuesta)
    assert respuesta.headers["HX-Retarget"] == "#caja-abierta"
    assert "Abrir caja" in respuesta.text  # la caja del día se pinta como está: cerrada
    sesion.expire_all()
    assert sesion.get(Caja, caja.id).diferencia == 3000  # contaron un cobro que no era


def test_pasados_30_dias_del_cierre_el_dialogo_se_cierra_y_avisa(
    adentro: TestClient, sesion: Session, caja_con_cobros, reloj: dict
):
    ticket = parqueo(sesion, "ABC123").id
    caja = sesion.scalars(select(Caja)).one()
    casos.cerrar_caja(sesion, caja.id, efectivo_contado=53000, ahora=reloj["ahora"])
    sesion.commit()
    reloj["ahora"] += minutos(31 * 24 * 60)

    respuesta = corregir(adentro, f"/parqueadero/{ticket}/deshacer-salida", motivo="Era otro carro")

    assert "se cerró hace más de 30 días" in error(respuesta)
    assert "cerrar-dialogo" in eventos(respuesta)


def test_un_pago_que_ya_se_anulo(adentro: TestClient, sesion: Session, caja_con_cobros):
    mes = pago(sesion).id
    corregir(adentro, f"/parqueadero/mensualidades/pagos/{mes}/anular", motivo="Dos veces")

    dialogo = adentro.get(f"/parqueadero/mensualidades/pagos/{mes}/corregir", headers=HTMX)
    otra_vez = corregir(adentro, f"/parqueadero/mensualidades/pagos/{mes}/anular", motivo="Otra")

    assert "Ese pago ya no existe" in error(dialogo)
    assert "Ese pago ya no existe" in error(otra_vez)
    assert "cerrar-dialogo" in eventos(otra_vez)


def test_una_salida_que_ya_se_deshizo(adentro: TestClient, sesion: Session, caja_con_cobros):
    ticket = parqueo(sesion, "ABC123").id
    corregir(adentro, f"/parqueadero/{ticket}/deshacer-salida", motivo="Era otro carro")

    dialogo = adentro.get(f"/parqueadero/{ticket}/corregir", headers=HTMX)

    assert "ya no está cobrada" in error(dialogo)


def test_lo_que_no_existe(adentro: TestClient, caja_con_cobros):
    assert adentro.get("/parqueadero/999999/corregir").status_code == 404
    assert adentro.post("/parqueadero/999999/corregir", data={"motivo": "x"}).status_code == 404
    assert adentro.get("/parqueadero/mensualidades/pagos/999999/corregir").status_code == 200


def test_sin_sesion_no_se_corrige_nada(web: TestClient):
    for ruta in ("/parqueadero/1/corregir", "/parqueadero/mensualidades/pagos/1/corregir"):
        respuesta = web.get(ruta, follow_redirects=False)
        assert respuesta.status_code == 303 and respuesta.headers["location"] == "/entrar"


# --- Dónde queda anotado ------------------------------------------------------------------


def test_el_cierre_su_ticket_y_el_balance_muestran_las_correcciones(
    adentro: TestClient, sesion: Session, caja_con_cobros
):
    ticket = parqueo(sesion, "ABC123").id
    corregir(
        adentro,
        f"/parqueadero/{ticket}/corregir",
        valor="3.000",
        medio_pago="nequi",
        motivo="Pagó <b>por</b> Nequi",
    )
    caja = sesion.scalars(select(Caja)).one()
    adentro.post(
        "/parqueadero/caja/cerrar",
        data={"caja_id": caja.id, "efectivo_contado": "50.000"},
        headers=HTMX,
    )

    cierre = adentro.get(f"/parqueadero/caja/{caja.id}").text
    papel = adentro.get(f"/parqueadero/caja/{caja.id}/ticket").text
    balance = adentro.get("/parqueadero/balance").text

    # Recién cerrada, se puede seguir corrigiendo (hasta 30 días después del cierre).
    assert "Correcciones" in cierre and "Corregir</button>" in cierre
    # Lo que escribió alguien se muestra como texto, nunca como HTML.
    assert "Pagó &lt;b&gt;por&lt;/b&gt; Nequi" in cierre and "<b>por</b>" not in cierre
    assert "Correcciones" in papel
    assert "Se corrigió el cobro: $3.000 en efectivo → $3.000 por Nequi" in papel
    assert "Correcciones en las cajas del día (1)" in balance
    assert "- ABC123: se corrigió el cobro ($3.000 en efectivo → $3.000 por Nequi)" in balance
