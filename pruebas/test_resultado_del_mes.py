"""El resultado del mes y su cierre para el contador, con los dos negocios juntos.

Lo que tiene que cumplirse siempre:
- resultado = lo cobrado − los gastos (los de la caja y los pagados por fuera de ella); los
  retiros y las consignaciones no son gasto;
- un mes se cierra solo cuando terminó y no le queda ninguna caja abierta, y una sola vez;
- un mes cerrado ya no cambia: ni sus cajas, ni sus cobros, ni sus salidas, ni sus gastos.
"""

from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import resultados
from app.caja import casos_de_uso as cajas
from app.caja.errores import CajaNoCorregible, CobroNoCorregible, ValorInvalido
from app.caja.estados import Negocio
from app.caja.tablas import CierreDeMes, GastoFueraDeCaja
from app.turnos import caja as caja_lavadero
from app.turnos import casos_de_uso as casos
from app.turnos.estados import MedioPago
from app.web.negocios import de_la_seccion
from pruebas.fabricas import BOGOTA, LUNES_8AM, minutos, turno_listo
from pruebas.navegador import HTMX, aviso

OCTUBRE = date(2026, 10, 1)
EN_NOVIEMBRE = datetime(2026, 11, 2, 9, 0, tzinfo=BOGOTA)


def esperado(sesion: Session, caja) -> int:
    return caja_lavadero.cuadre_de_caja(sesion, caja).efectivo_esperado


@pytest.fixture
def octubre(sesion: Session):
    """En el lavadero: un lavado de $40.000 en efectivo, $10.000 de aseo y un retiro de
    $20.000 del cajón, y el arriendo de $500.000 por transferencia. La caja sigue abierta."""
    caja = caja_lavadero.abrir_caja(sesion, base=50_000, ahora=LUNES_8AM - minutos(60))
    turno = turno_listo(sesion)
    casos.cobrar(sesion, turno, medio_pago=MedioPago.EFECTIVO, ahora=LUNES_8AM + minutos(60))
    for tipo, categoria, valor in (("gasto", "aseo", 10_000), ("retiro", None, 20_000)):
        cajas.registrar_salida(
            sesion,
            Negocio.LAVADERO,
            tipo=tipo,
            categoria=categoria,
            descripcion="Algo",
            valor=valor,
            efectivo_esperado=esperado,
            ahora=LUNES_8AM + minutos(70),
        )
    cajas.registrar_gasto_fuera_de_caja(
        sesion,
        Negocio.LAVADERO,
        fecha=date(2026, 10, 5),
        categoria="arriendo",
        descripcion="Arriendo de octubre",
        valor=500_000,
        ahora=LUNES_8AM,
    )
    return caja, turno


def cerrar_la_caja(sesion: Session, caja) -> None:
    caja_lavadero.cerrar_caja(
        sesion, caja.id, efectivo_contado=60_000, ahora=LUNES_8AM + minutos(600)
    )


def test_el_resultado_del_mes(sesion: Session, octubre):
    r = resultados.resultado_del_mes(sesion, Negocio.LAVADERO, date(2026, 10, 26))

    assert (r.mes, r.ingresos, r.por_medio) == (OCTUBRE, 40_000, {MedioPago.EFECTIVO: 40_000})
    assert r.gastos == [("Arriendo", 500_000), ("Aseo", 10_000)]
    assert (r.gastos_de_caja, r.gastos_fuera_de_caja, r.total_gastos) == (10_000, 500_000, 510_000)
    assert (r.resultado, r.retiros, r.consignaciones) == (-470_000, 20_000, 0)
    assert r.cajas_abiertas == 1 and r.cierre is None
    parqueadero = resultados.resultado_del_mes(sesion, Negocio.PARQUEADERO, OCTUBRE)
    assert (parqueadero.ingresos, parqueadero.total_gastos) == (0, 0)


def test_un_mes_se_cierra_cuando_termino_y_sin_cajas_abiertas(sesion: Session, octubre):
    caja, _ = octubre

    with pytest.raises(ValorInvalido, match="todavía no ha terminado"):
        resultados.cerrar_mes(sesion, Negocio.LAVADERO, OCTUBRE, ahora=LUNES_8AM)
    with pytest.raises(ValorInvalido, match="caja\\(s\\) de ese mes todavía abierta"):
        resultados.cerrar_mes(sesion, Negocio.LAVADERO, OCTUBRE, ahora=EN_NOVIEMBRE)
    cerrar_la_caja(sesion, caja)

    cierre = resultados.cerrar_mes(sesion, Negocio.LAVADERO, OCTUBRE, ahora=EN_NOVIEMBRE)

    assert cierre.cifras["resultado"] == -470_000
    assert cierre.cifras["gastos"] == [["Arriendo", 500_000], ["Aseo", 10_000]]
    with pytest.raises(ValorInvalido, match="ya se cerró"):
        resultados.cerrar_mes(sesion, Negocio.LAVADERO, OCTUBRE, ahora=EN_NOVIEMBRE)
    # El del parqueadero es aparte: sigue abierto.
    assert resultados.resultado_del_mes(sesion, Negocio.PARQUEADERO, OCTUBRE).cierre is None


def test_un_mes_cerrado_ya_no_cambia(sesion: Session, octubre):
    caja, turno = octubre
    cerrar_la_caja(sesion, caja)
    resultados.cerrar_mes(sesion, Negocio.LAVADERO, OCTUBRE, ahora=EN_NOVIEMBRE)
    gasto = sesion.scalars(select(GastoFueraDeCaja)).one()
    dice = "Octubre de 2026 ya se cerró para el contador"

    with pytest.raises(CajaNoCorregible, match=dice):
        cajas.corregir_base(
            sesion, Negocio.LAVADERO, caja.id, base=1_000, motivo="x", ahora=EN_NOVIEMBRE
        )
    with pytest.raises(CobroNoCorregible, match=dice):
        casos.corregir_cobro(
            sesion,
            turno,
            precio_cobrado=30_000,
            medio_pago="efectivo",
            motivo="x",
            administrador_id=None,
            ahora=EN_NOVIEMBRE,
        )
    with pytest.raises(ValorInvalido, match=dice):
        cajas.registrar_gasto_fuera_de_caja(
            sesion,
            Negocio.LAVADERO,
            fecha=date(2026, 10, 31),
            categoria="aseo",
            descripcion="Tarde",
            valor=1_000,
            ahora=EN_NOVIEMBRE,
        )
    with pytest.raises(ValorInvalido, match=dice):
        cajas.anular_gasto_fuera_de_caja(sesion, gasto.id, motivo="x", ahora=EN_NOVIEMBRE)
    # El parqueadero sí puede anotar gastos de octubre: su mes sigue abierto.
    cajas.registrar_gasto_fuera_de_caja(
        sesion,
        Negocio.PARQUEADERO,
        fecha=date(2026, 10, 31),
        categoria="vigilancia",
        descripcion="Celador",
        valor=1_000,
        ahora=EN_NOVIEMBRE,
    )


@pytest.mark.parametrize(
    ("cambios", "dice"),
    [
        ({"fecha": None}, "Escribe el día"),
        ({"fecha": date(2026, 10, 27)}, "no puede ser después de hoy"),
        ({"categoria": None}, "Elige en qué se gastó"),
        ({"descripcion": " "}, "Escribe qué se pagó"),
        ({"valor": 0}, "Escribe cuánto se pagó"),
    ],
)
def test_lo_que_se_revisa_en_un_gasto(sesion: Session, cambios: dict, dice: str):
    datos = {
        "fecha": date(2026, 10, 26),
        "categoria": "aseo",
        "descripcion": "Escobas",
        "valor": 9_000,
    } | cambios
    with pytest.raises(ValorInvalido, match=dice):
        cajas.registrar_gasto_fuera_de_caja(sesion, Negocio.LAVADERO, ahora=LUNES_8AM, **datos)


# --- En la pantalla -------------------------------------------------------------------------


def test_la_seccion_es_de_los_dos_negocios():
    assert de_la_seccion("resultados") == (None, "resultados")


def test_desde_elegir_se_llega_a_los_dos_juntos(adentro: TestClient):
    assert 'href="/resultados"' in adentro.get("/inicio").text


def test_cerrar_octubre_desde_la_pantalla(
    adentro: TestClient, sesion: Session, reloj: dict, octubre
):
    caja, _ = octubre
    cerrar_la_caja(sesion, caja)
    sesion.commit()
    reloj["ahora"] = EN_NOVIEMBRE

    pagina = adentro.get("/resultados", params={"mes": "2026-10"}).text
    dialogo = adentro.get(
        "/resultados/cerrar", params={"negocio": "lavadero", "mes": "2026-10"}, headers=HTMX
    ).text
    respuesta = adentro.post(
        "/resultados/cerrar", data={"negocio": "lavadero", "mes": "2026-10"}, headers=HTMX
    )

    assert "<h1>Resultado del mes</h1>" in pagina and "Octubre de 2026" in pagina
    assert "Cerrar octubre de 2026 para el contador" in pagina
    assert "Gasto · Arriendo" in pagina and "−$500.000" in pagina
    assert "se perdió" in dialogo.lower()
    assert aviso(respuesta) == "Lavadero: octubre de 2026 quedó cerrado para el contador."
    assert "Se cerró el 2 de noviembre de 2026, eduar." in respuesta.text
    assert sesion.scalars(select(CierreDeMes)).one().negocio == Negocio.LAVADERO
    archivo = adentro.get("/resultados/exportar", params={"mes": "2026-10"})
    assert "Lavadero;octubre de 2026;Resultado;;-470000" in archivo.text
    assert (
        "Lavadero;octubre de 2026;Estado del mes;Cerrado el 2 de noviembre de 2026;" in archivo.text
    )


def test_anotar_y_anular_un_gasto_desde_la_pantalla(
    adentro: TestClient, sesion: Session, reloj: dict
):
    reloj["ahora"] = LUNES_8AM
    dialogo = adentro.get("/resultados/gastos/nuevo", headers=HTMX).text
    assert 'value="2026-10-26"' in dialogo  # hoy, por defecto

    respuesta = adentro.post(
        "/resultados/gastos",
        data={
            "negocio": "parqueadero",
            "fecha": "2026-10-20",
            "categoria": "servicios",
            "descripcion": "Luz",
            "valor": "180.000",
            "mes": "2026-10",
        },
        headers=HTMX,
    )

    assert aviso(respuesta) == "Gasto anotado: $180.000 en servicios públicos."
    assert "Parqueadero · Servicios públicos" in respuesta.text
    gasto = sesion.scalars(select(GastoFueraDeCaja)).one()
    anulado = adentro.post(
        f"/resultados/gastos/{gasto.id}/anular", data={"motivo": "Era del mes pasado"}, headers=HTMX
    )
    assert aviso(anulado) == "Gasto anulado: $180.000."
    assert "Anulado por eduar: Era del mes pasado" in anulado.text


def test_un_gasto_mal_escrito_se_explica(adentro: TestClient, reloj: dict):
    reloj["ahora"] = LUNES_8AM

    respuesta = adentro.post(
        "/resultados/gastos",
        data={"negocio": "lavadero", "fecha": "2026-10-20", "descripcion": "Luz", "valor": "1"},
        headers=HTMX,
    )

    assert "Elige en qué se gastó" in respuesta.text and 'value="Luz"' in respuesta.text


def test_un_mes_que_no_ha_llegado_muestra_el_de_hoy(adentro: TestClient, reloj: dict):
    reloj["ahora"] = LUNES_8AM

    pagina = adentro.get("/resultados", params={"mes": "2030-01"}).text

    assert "Octubre de 2026" in pagina and "Mes siguiente" not in pagina
    assert adentro.get("/resultados", params={"mes": "nada"}).status_code == 200
    assert (
        adentro.get("/resultados/cerrar", params={"negocio": "x", "mes": "2026-10"}).status_code
        == 404
    )


def test_la_fecha_de_un_gasto_es_la_de_bogota(sesion: Session):
    """A las 11:30 p.m. del 31 en Bogotá ya es 1 en UTC: el gasto del 31 sigue valiendo."""
    tarde = datetime(2026, 10, 31, 23, 30, tzinfo=BOGOTA)

    gasto = cajas.registrar_gasto_fuera_de_caja(
        sesion,
        Negocio.LAVADERO,
        fecha=date(2026, 10, 31),
        categoria="aseo",
        descripcion="Escobas",
        valor=9_000,
        ahora=tarde,
    )

    assert gasto.fecha == tarde.date()
