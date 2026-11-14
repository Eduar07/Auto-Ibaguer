"""La caja del lavadero: se abre con una base, cada turno cobrado entra a ella y se cierra contando
el efectivo; un cobro equivocado se corrige (o se deshace) con su motivo, también con la caja
ya cerrada (hasta 30 días). Las pantallas son las mismas de la caja del parqueadero.

Lo que tiene que cumplirse siempre:
- sin la caja del lavadero abierta no se cobra un turno, y cada cobro queda en la caja abierta;
- lo que debe haber en el cajón es la base más lo cobrado en efectivo;
- una corrección nunca borra el rastro: queda la corrección con cómo estaba, el motivo y quién;
- la caja del lavadero y la del parqueadero nunca se mezclan.
"""

import csv
import io
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.caja.errores import CobroNoCorregible, SinCajaAbierta, ValorInvalido
from app.caja.estados import Negocio, TipoCorreccion
from app.caja.tablas import CorreccionCobro
from app.parqueadero import casos_de_uso as parqueadero
from app.turnos import caja as caja_lavadero
from app.turnos import casos_de_uso as casos
from app.turnos import consultas_de_caja as consultas
from app.turnos.consultas_de_caja import FiltroDeCierres
from app.turnos.estados import EstadoTurno, MedioPago
from app.turnos.tablas import Pago, Turno
from pruebas.fabricas import LUNES_8AM, historial, minutos, turno_listo
from pruebas.navegador import HTMX, aviso

EFECTIVO, NEQUI = MedioPago.EFECTIVO, MedioPago.NEQUI
A_LAS_9 = LUNES_8AM + minutos(60)


def abierta(sesion: Session, base: int = 20_000):
    return caja_lavadero.abrir_caja(sesion, base=base, ahora=LUNES_8AM - minutos(60))


def cobrado(sesion: Session, placa: str, medio: MedioPago = EFECTIVO, tipo: str = "Automóvil"):
    """Un turno lavado y cobrado al precio del menú (Básico: $40.000 carro, $50.000 camioneta)."""
    turno = turno_listo(sesion, placa=placa, tipo=tipo)
    casos.cobrar(sesion, turno, medio_pago=medio, ahora=A_LAS_9)
    return turno


# --- Cobrar con la caja abierta ---------------------------------------------------------------


def test_sin_la_caja_abierta_no_se_cobra(sesion: Session):
    turno = turno_listo(sesion)

    with pytest.raises(SinCajaAbierta, match="La caja del lavadero está cerrada"):
        casos.cobrar(sesion, turno, medio_pago=EFECTIVO, ahora=A_LAS_9)
    assert sesion.get(Turno, turno).estado == EstadoTurno.LISTO

    caja = abierta(sesion)
    pago = casos.cobrar(sesion, turno, medio_pago=EFECTIVO, ahora=A_LAS_9)
    assert pago.caja_id == caja.id


def test_la_caja_del_parqueadero_no_sirve_para_cobrar_un_turno(sesion: Session):
    parqueadero.abrir_caja(sesion, base=0, ahora=LUNES_8AM)

    with pytest.raises(SinCajaAbierta):
        casos.cobrar(sesion, turno_listo(sesion), medio_pago=EFECTIVO, ahora=A_LAS_9)


def test_el_cuadre_y_el_cierre(sesion: Session):
    caja = abierta(sesion, base=20_000)
    cobrado(sesion, "ABC123", EFECTIVO)
    cobrado(sesion, "XYZ789", NEQUI, tipo="Camioneta")

    cuadre = caja_lavadero.cuadre_de_caja(sesion, caja)
    assert (cuadre.total, cuadre.por_medio(NEQUI), cuadre.efectivo_esperado) == (
        90_000,
        50_000,
        60_000,
    )

    caja_lavadero.cerrar_caja(
        sesion, caja.id, efectivo_contado=55_000, ahora=LUNES_8AM.replace(hour=19)
    )
    assert (caja.efectivo_esperado, caja.diferencia) == (60_000, -5_000)
    assert caja_lavadero.caja_abierta(sesion) is None


def test_el_resumen_de_la_caja(sesion: Session):
    caja = abierta(sesion)
    cobrado(sesion, "ABC123", EFECTIVO)
    cobrado(sesion, "XYZ789", NEQUI, tipo="Camioneta")

    resumen = consultas.resumen_de_caja(sesion, caja)

    assert resumen.cobros == 2
    assert resumen.cifras == [(2, "turnos cobrados")]
    assert resumen.lineas == [(EFECTIVO, (40_000,), 40_000), (NEQUI, (50_000,), 50_000)]
    assert [c.concepto for c in consultas.cobros_de_caja(sesion, caja.id)] == [
        "C-02 · XYZ789",
        "C-01 · ABC123",
    ]


# --- Corregir un cobro ------------------------------------------------------------------------


def test_corregir_el_medio_de_pago(sesion: Session, admin):
    caja = abierta(sesion)
    turno = cobrado(sesion, "ABC123", EFECTIVO)

    correccion = casos.corregir_cobro(
        sesion,
        turno,
        precio_cobrado=40_000,
        medio_pago=NEQUI,
        motivo="Pagó por Nequi",
        administrador_id=admin.id,
        ahora=A_LAS_9 + minutos(5),
    )

    pago = sesion.scalars(select(Pago).where(Pago.turno_id == turno)).one()
    assert (pago.precio_cobrado, pago.medio_pago) == (40_000, NEQUI)
    assert (correccion.tipo, correccion.turno_id, correccion.caja_id) == (
        TipoCorreccion.CORREGIDO,
        turno,
        caja.id,
    )
    assert not correccion.despues_del_cierre
    assert caja_lavadero.cuadre_de_caja(sesion, caja).efectivo_esperado == 20_000
    assert historial(sesion, turno)[-1].detalle == (
        "Cobro corregido: $40.000 en efectivo → $40.000 por Nequi. Motivo: Pagó por Nequi"
    )


def test_corregir_un_cobro_de_una_caja_cerrada_cambia_su_diferencia(sesion: Session):
    caja = abierta(sesion)
    turno = cobrado(sesion, "ABC123", EFECTIVO)
    caja_lavadero.cerrar_caja(sesion, caja.id, efectivo_contado=55_000, ahora=A_LAS_9 + minutos(60))
    assert caja.diferencia == -5_000  # debía haber $60.000

    correccion = casos.corregir_cobro(
        sesion,
        turno,
        precio_cobrado=35_000,
        medio_pago=EFECTIVO,
        motivo="Descuento de cliente frecuente",
        ahora=A_LAS_9 + timedelta(days=2),
    )

    assert correccion.despues_del_cierre
    assert (caja.efectivo_esperado, caja.diferencia) == (55_000, 0)
    pago = sesion.scalars(select(Pago).where(Pago.turno_id == turno)).one()
    assert pago.motivo_ajuste == "Descuento de cliente frecuente"  # ya no es el del menú
    resumen = consultas.resumen_de_caja(sesion, caja)
    assert resumen.corregida_despues_del_cierre and resumen.diferencia_al_cerrar == -5_000


def test_deshacer_un_cobro_deja_el_turno_por_cobrar(sesion: Session):
    caja = abierta(sesion)
    turno = cobrado(sesion, "ABC123", EFECTIVO)

    correccion = casos.deshacer_cobro(
        sesion, turno, motivo="Era el turno de al lado", ahora=A_LAS_9 + minutos(2)
    )

    assert sesion.get(Turno, turno).estado == EstadoTurno.LISTO
    assert sesion.scalar(select(func.count()).select_from(Pago).where(Pago.turno_id == turno)) == 0
    assert (correccion.tipo, correccion.valor_antes, correccion.medio_despues) == (
        TipoCorreccion.COBRO_DESHECHO,
        40_000,
        None,
    )
    assert caja_lavadero.cuadre_de_caja(sesion, caja).total == 0
    # Y se puede volver a cobrar, en la misma caja.
    assert (
        casos.cobrar(sesion, turno, medio_pago=NEQUI, ahora=A_LAS_9 + minutos(3)).caja_id == caja.id
    )


def test_lo_que_no_se_corrige(sesion: Session):
    abierta(sesion)
    turno = turno_listo(sesion)

    with pytest.raises(CobroNoCorregible, match="no tiene un cobro que corregir"):
        casos.deshacer_cobro(sesion, turno, motivo="Prueba", ahora=A_LAS_9)
    casos.cobrar(sesion, turno, medio_pago=EFECTIVO, ahora=A_LAS_9)
    with pytest.raises(ValorInvalido, match="Escribe por qué"):
        casos.deshacer_cobro(sesion, turno, motivo="  ", ahora=A_LAS_9)
    with pytest.raises(ValorInvalido, match="No hay nada que cambiar"):
        casos.corregir_cobro(
            sesion, turno, precio_cobrado=40_000, medio_pago=EFECTIVO, motivo="x", ahora=A_LAS_9
        )


def test_una_caja_cerrada_hace_mas_de_30_dias_ya_no_se_corrige(sesion: Session):
    caja = abierta(sesion)
    turno = cobrado(sesion, "ABC123")
    caja_lavadero.cerrar_caja(sesion, caja.id, efectivo_contado=60_000, ahora=A_LAS_9)

    with pytest.raises(CobroNoCorregible, match="hace más de 30 días"):
        casos.deshacer_cobro(sesion, turno, motivo="Tarde", ahora=A_LAS_9 + timedelta(days=31))


# --- Cierres y Excel ----------------------------------------------------------------------------


def test_los_cierres_del_lavadero_son_solo_los_suyos(sesion: Session):
    caja = abierta(sesion, base=0)
    cobrado(sesion, "ABC123")
    caja_lavadero.cerrar_caja(sesion, caja.id, efectivo_contado=40_000, ahora=A_LAS_9)
    del_parqueadero = parqueadero.abrir_caja(sesion, base=0, ahora=A_LAS_9)
    parqueadero.cerrar_caja(sesion, del_parqueadero.id, efectivo_contado=0, ahora=A_LAS_9)

    cierres = consultas.cierres(sesion, FiltroDeCierres())

    assert [fila.caja.id for fila in cierres.filas] == [caja.id]
    assert (cierres.cuantos, cierres.entro, cierres.cuadraron) == (1, 40_000, 1)
    assert caja.negocio == Negocio.LAVADERO


def test_el_excel_de_los_cobros(sesion: Session):
    abierta(sesion)
    turno = cobrado(sesion, "ABC123")
    casos.corregir_cobro(
        sesion, turno, precio_cobrado=35_000, medio_pago=EFECTIVO, motivo="Cliente frecuente",
        ahora=A_LAS_9,
    )  # fmt: skip

    encabezado, linea = list(
        consultas.lineas_para_excel(sesion, LUNES_8AM.date(), LUNES_8AM.date())
    )

    fila = dict(zip(encabezado, linea, strict=True))
    assert (fila["Turno"], fila["Placa"], fila["Servicio"]) == ("C-01", "ABC123", "Básico")
    assert (fila["Precio del menú"], fila["Cobrado"], fila["Descuento"]) == (
        "40000",
        "35000",
        "5000",
    )
    assert fila["Motivo del ajuste"] == "Cliente frecuente"


# --- En la pantalla -------------------------------------------------------------------------------


def test_abrir_la_caja_desde_la_pantalla(adentro: TestClient, sesion: Session):
    pagina = adentro.get("/caja").text
    assert "<h1>Caja del lavadero</h1>" in pagina
    assert 'hx-post="/caja/abrir"' in pagina
    assert pagina.count('<a href="/caja" aria-current="page">') == 2

    respuesta = adentro.post("/caja/abrir", data={"base": "$50.000"}, headers=HTMX)

    assert respuesta.headers["HX-Refresh"] == "true"
    caja = caja_lavadero.caja_abierta(sesion)
    assert caja is not None and caja.base == 50_000
    assert "Caja abierta" in adentro.get("/caja").text


def test_con_la_caja_cerrada_la_fila_lo_dice_y_no_deja_cobrar(adentro: TestClient, sesion: Session):
    turno = turno_listo(sesion)
    sesion.commit()

    fila = adentro.get("/fila").text
    dialogo = adentro.get(f"/turnos/{turno}/cobrar", headers=HTMX).text

    assert "La caja está cerrada: para cobrar, primero" in fila
    assert ">Abrir la caja<" in fila or "Abrir la caja" in fila
    assert "La caja del lavadero está cerrada." in dialogo
    assert 'href="/caja"' in dialogo


def test_cerrar_la_caja_y_ver_el_cierre(adentro: TestClient, sesion: Session):
    caja = abierta(sesion, base=10_000)
    cobrado(sesion, "ABC123")
    sesion.commit()

    dialogo = adentro.get("/caja/cerrar", headers=HTMX).text
    respuesta = adentro.post(
        "/caja/cerrar", data={"caja_id": caja.id, "efectivo_contado": "50.000"}, headers=HTMX
    )

    assert "Debe haber en el cajón" in dialogo and "$50.000" in dialogo
    assert respuesta.headers["HX-Redirect"] == f"/caja/{caja.id}?imprimir=1"
    cierre = adentro.get(f"/caja/{caja.id}").text
    assert f"<h1>Caja {caja.id} del lavadero</h1>" in cierre
    assert "Cuadró exacto" in cierre
    assert f'data-imprimir="/caja/{caja.id}/ticket"' in cierre
    ticket = adentro.get(f"/caja/{caja.id}/ticket").text
    assert "Turnos cobrados" in ticket or "Turno cobrado" in ticket


def test_corregir_y_deshacer_desde_la_caja(adentro: TestClient, sesion: Session):
    abierta(sesion)
    turno = cobrado(sesion, "ABC123")
    sesion.commit()

    caja = adentro.get("/caja").text
    dialogo = adentro.get(f"/turnos/{turno}/corregir-cobro", headers=HTMX).text
    corregido = adentro.post(
        f"/turnos/{turno}/corregir-cobro",
        data={"valor": "40000", "medio_pago": "nequi", "motivo": "Pagó por Nequi"},
        headers=HTMX | {"HX-Current-URL": "http://testserver/caja"},
    )
    deshecho = adentro.post(
        f"/turnos/{turno}/deshacer-cobro",
        data={"motivo": "Era otro turno"},
        headers=HTMX | {"HX-Current-URL": "http://testserver/caja"},
    )

    assert f'hx-get="/turnos/{turno}/corregir-cobro"' in caja
    assert "El precio del menú daba $40.000." in dialogo
    assert aviso(corregido) == "Listo: el cobro de ABC123 quedó en $40.000 por Nequi."
    assert aviso(deshecho).startswith("Listo: el cobro de ABC123 se deshizo")
    assert sesion.get(Turno, turno).estado == EstadoTurno.LISTO
    assert sesion.scalar(select(func.count()).select_from(CorreccionCobro)) == 2


def test_las_cajas_de_un_negocio_no_se_ven_desde_el_otro(adentro: TestClient, sesion: Session):
    del_lavadero = abierta(sesion)
    del_parqueadero = parqueadero.abrir_caja(sesion, base=0, ahora=LUNES_8AM)
    sesion.commit()

    assert adentro.get(f"/caja/{del_parqueadero.id}").status_code == 404
    assert adentro.get(f"/parqueadero/caja/{del_lavadero.id}").status_code == 404
    assert adentro.get(f"/caja/{del_lavadero.id}").status_code == 200


def test_los_archivos_para_excel(adentro: TestClient, sesion: Session):
    caja = abierta(sesion)
    cobrado(sesion, "ABC123")
    caja_lavadero.cerrar_caja(sesion, caja.id, efectivo_contado=60_000, ahora=A_LAS_9)
    sesion.commit()

    cobros = adentro.get("/caja/exportar", params={"desde": "2026-10-26", "hasta": "2026-10-26"})
    cierres = adentro.get("/caja/exportar-cierres")

    assert (
        'filename="lavadero-2026-10-26-a-2026-10-26.csv"' in cobros.headers["content-disposition"]
    )
    assert "ABC123" in cobros.text
    filas = list(csv.reader(io.StringIO(cierres.text.lstrip("﻿")), delimiter=";"))
    assert len(filas) == 2 and filas[1][0] == str(caja.id)
