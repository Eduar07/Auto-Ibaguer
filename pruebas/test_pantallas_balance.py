"""El balance y el reporte del día en el dashboard, como los usa el navegador: la pantalla por
día, semana y mes, el ticket, el botón de WhatsApp, el correo (a mano y al cerrar la caja) y
los correos de los jefes en Tarifas.

Al final, que las pantallas del parqueadero no hagan más consultas a la base cuando hay más
datos: así un mes lleno carga igual de rápido que un día.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Connection, event, select
from sqlalchemy.orm import Session

from app.main import app
from app.parqueadero import casos_de_uso as casos
from app.parqueadero.estados import TipoParqueo
from app.parqueadero.tablas import CajaParqueadero, Parqueo
from app.parqueadero.tarifas import guardar_ajustes, leer_ajustes
from app.turnos.estados import MedioPago
from app.web import parqueadero as pantallas
from pruebas.fabricas import BOGOTA, minutos, misma_sesion
from pruebas.navegador import HTMX, aviso, error
from pruebas.test_balance import ajustes_con

LUNES = date(2026, 10, 26)  # el día del reloj de las pruebas


def a_las(dia: date, hora: int, minuto: int = 0) -> datetime:
    return datetime(dia.year, dia.month, dia.day, hora, minuto, tzinfo=BOGOTA)


def entrar(adentro: TestClient, placa: str, tipo: str = "carro"):
    return adentro.post("/parqueadero/entradas", data={"placa": placa, "tipo": tipo}, headers=HTMX)


def salir(adentro: TestClient, sesion: Session, placa: str, **datos):
    parqueo = sesion.scalars(select(Parqueo).where(Parqueo.placa == placa)).one()
    datos.setdefault("medio_pago", "efectivo")
    return adentro.post(f"/parqueadero/{parqueo.id}/salida", data=datos, headers=HTMX)


def caja_abierta(sesion: Session) -> CajaParqueadero:
    return sesion.scalars(select(CajaParqueadero).where(CajaParqueadero.abierta)).one()


@pytest.fixture
def lunes_con_cobros(adentro: TestClient, sesion: Session, reloj: dict) -> None:
    """Lunes: un carro 2 h ($6.000 en efectivo), una moto 1 h por Nequi ($1.500), un carro con
    descuento y una entrada anulada. La caja queda abierta."""
    adentro.post("/parqueadero/caja/abrir", data={"base": "50.000"}, headers=HTMX)
    entrar(adentro, "ABC123")
    entrar(adentro, "ABC12D", "moto")
    entrar(adentro, "XYZ789")
    entrar(adentro, "ERR111")
    error_ = sesion.scalars(select(Parqueo).where(Parqueo.placa == "ERR111")).one()
    adentro.post(
        f"/parqueadero/{error_.id}/anular", data={"motivo": "Placa <b>mal</b>"}, headers=HTMX
    )
    reloj["ahora"] += minutos(60)
    salir(adentro, sesion, "ABC12D", medio_pago="nequi")
    reloj["ahora"] += minutos(60)
    salir(adentro, sesion, "ABC123")
    salir(adentro, sesion, "XYZ789", valor_cobrado="5.000", motivo="Cliente frecuente")


# --- La pantalla --------------------------------------------------------------------------


def test_el_balance_de_hoy(adentro: TestClient, lunes_con_cobros):
    pagina = adentro.get("/parqueadero/balance").text

    assert "Lunes 26 de octubre de 2026" in pagina
    assert "Recogido del día" in pagina and "$12.500" in pagina  # 6.000 + 1.500 + 5.000
    assert "Ayer (todo el día): $0" in pagina
    assert "3 salidas" in pagina and "4 entradas" not in pagina  # la anulada no cuenta
    assert '>3</span><span class="cifra__nombre">entradas (1 moto, 2 carros)' in pagina
    assert "Quedan adentro: 0 (0 motos, 0 carros)" in pagina  # salieron justo a las 10:00
    assert "Descuentos" in pagina and "$1.000 en 1 salida" in pagina
    assert "Cliente frecuente" in pagina
    # Lo que escribió alguien se muestra como texto, nunca como HTML.
    assert "Placa &lt;b&gt;mal&lt;/b&gt;" in pagina and "<b>mal</b>" not in pagina
    assert "Sigue abierta" in pagina
    # Hoy no hay "siguiente": del futuro no hay nada que ver.
    assert "Día anterior" in pagina and "Día siguiente" not in pagina
    assert 'href="/parqueadero/balance?periodo=dia&amp;fecha=2026-10-25"' in pagina


def test_el_reporte_del_dia_en_la_pantalla(adentro: TestClient, lunes_con_cobros):
    pagina = adentro.get("/parqueadero/balance").text

    assert "Reporte del día para los jefes" in pagina
    assert 'href="https://wa.me/?text=' in pagina and 'target="_blank"' in pagina
    assert 'data-imprimir="/parqueadero/balance/ticket?fecha=2026-10-26"' in pagina
    assert "Reporte del lunes 26 de octubre de 2026, hasta las 10:00 a.m." in pagina
    # Sin correo configurado: no hay botón de correo, y dice qué hacer.
    assert "Enviar por correo ahora" not in pagina
    assert "escribe los correos de los jefes" in pagina


def test_la_semana_y_el_mes(adentro: TestClient, lunes_con_cobros):
    semana = adentro.get("/parqueadero/balance", params={"periodo": "semana"}).text
    mes = adentro.get("/parqueadero/balance", params={"periodo": "mes"}).text

    assert "Semana del 26 de octubre al 1 de noviembre de 2026" in semana
    assert "Recogido de la semana" in semana and "Día por día" in semana
    assert "La semana pasada (completa): $0" in semana
    assert "Octubre de 2026" in mes and "Recogido del mes" in mes
    # Día por día, hasta hoy: los días que no han llegado no salen.
    assert ">Hoy</a>" in mes and ">Dom 25</a>" in mes and ">Mar 27</a>" not in mes
    assert "--parte: 100.0%" in mes  # el mejor día llena la barra
    assert "Reporte del día para los jefes" not in mes  # el reporte es del día


def test_las_cajas_del_mes_muestran_sobrantes_y_faltantes_aparte(
    adentro: TestClient, sesion: Session
):
    for dia, contado in ((1, 10000), (2, 10500), (3, 8000), (4, 10000)):  # base 10.000
        caja = casos.abrir_caja(sesion, base=10000, ahora=a_las(date(2026, 10, dia), 7))
        casos.cerrar_caja(
            sesion, caja.id, efectivo_contado=contado, ahora=a_las(date(2026, 10, dia), 20)
        )
    sesion.commit()

    mes = adentro.get("/parqueadero/balance", params={"periodo": "mes"}).text
    dia_3 = adentro.get("/parqueadero/balance", params={"fecha": "2026-10-03"}).text

    assert "Cajas cerradas (4)" in mes
    assert (
        '<span class="diferencia--cuadra">2 cuadraron</span> · '
        '<span class="diferencia--sobra">sobraron $500</span> · '
        '<span class="diferencia--falta">faltaron $2.000</span>'
    ) in mes
    assert "Cajas cerradas (1)" in dia_3 and "Faltaron $2.000" in dia_3


def test_moverse_entre_dias_semanas_y_meses(adentro: TestClient, lunes_con_cobros):
    pasado = adentro.get("/parqueadero/balance", params={"fecha": "2026-10-20"}).text

    assert "Martes 20 de octubre de 2026" in pasado
    assert "Recogido del día" in pasado and "$0" in pasado
    assert "El día anterior: $0" in pasado
    assert "Día siguiente" in pasado and ">Ir a hoy</a>" in pasado
    # Cambiar a semana o mes se queda en esa fecha.
    assert 'href="/parqueadero/balance?periodo=semana&amp;fecha=2026-10-20"' in pasado
    assert 'href="/parqueadero/balance?periodo=mes&amp;fecha=2026-10-20"' in pasado


def test_una_fecha_que_no_ha_llegado_muestra_hoy(adentro: TestClient, lunes_con_cobros):
    pagina = adentro.get("/parqueadero/balance", params={"fecha": "2027-01-01"}).text

    assert "Lunes 26 de octubre de 2026" in pagina and "$12.500" in pagina


def test_un_periodo_raro_muestra_el_dia(adentro: TestClient, lunes_con_cobros):
    pagina = adentro.get("/parqueadero/balance", params={"periodo": "año"}).text

    assert "Lunes 26 de octubre de 2026" in pagina


def test_balance_esta_en_las_partes_del_parqueadero(adentro: TestClient):
    pagina = adentro.get("/parqueadero").text
    balance = adentro.get("/parqueadero/balance").text

    assert 'href="/parqueadero/balance"' in pagina
    assert 'href="/parqueadero/balance" aria-current="page"' in balance
    assert "No se abrió caja ese día." in balance


def test_sin_sesion_no_se_ve_el_balance(web: TestClient):
    for ruta in ("/parqueadero/balance", "/parqueadero/balance/ticket"):
        respuesta = web.get(ruta, follow_redirects=False)
        assert respuesta.status_code == 303 and respuesta.headers["location"] == "/entrar"
    respuesta = web.post("/parqueadero/balance/enviar", data={}, follow_redirects=False)
    assert respuesta.status_code in (303, 403)


# --- El ticket ----------------------------------------------------------------------------


def test_el_ticket_del_reporte(adentro: TestClient, sesion: Session, lunes_con_cobros):
    guardar_ajustes(sesion, ajustes_con(sesion, nombre="Parqueadero Central", nit="900123456"))
    sesion.commit()

    ticket = adentro.get("/parqueadero/balance/ticket", params={"fecha": "2026-10-26"})

    assert ticket.headers["X-Frame-Options"] == "SAMEORIGIN"
    texto = ticket.text
    assert "Parqueadero Central" in texto and "NIT 900123456" in texto
    assert "Reporte del día" in texto and "Lunes 26 de octubre de 2026" in texto
    assert "Hasta 10:00 a.m." in texto
    assert "Recogido" in texto and "$12.500" in texto
    assert "Nequi" in texto and "Daviplata" not in texto  # solo los medios que se usaron
    assert "Sigue abierta" in texto and "Revisó" in texto
    assert "Conserve este ticket" not in texto  # ese pie es del ticket de entrada


# --- El correo ----------------------------------------------------------------------------


def test_con_correo_y_destinos_se_puede_mandar_ya(
    adentro: TestClient, sesion: Session, lunes_con_cobros, correo_configurado: list
):
    guardar_ajustes(sesion, ajustes_con(sesion, correos_reporte="jefe@x.co, socio@x.co"))
    sesion.commit()
    pagina = adentro.get("/parqueadero/balance").text
    assert "Enviar por correo ahora" in pagina
    assert "Al cerrar la caja les llega solo por correo." in pagina

    respuesta = adentro.post(
        "/parqueadero/balance/enviar", data={"fecha": "2026-10-26"}, headers=HTMX
    )

    assert aviso(respuesta) == "Reporte enviado a 2 correos."
    (mensaje,) = correo_configurado
    assert mensaje["To"] == "jefe@x.co, socio@x.co"
    assert "$12.500" in mensaje["Subject"]


def test_si_el_correo_falla_lo_dice(adentro: TestClient, sesion: Session, correo_configurado: list):
    respuesta = adentro.post("/parqueadero/balance/enviar", data={}, headers=HTMX)

    assert "No hay correos" in error(respuesta)
    assert respuesta.headers["HX-Reswap"] == "none"


def test_al_cerrar_la_caja_le_llega_el_reporte_del_dia_de_la_caja(
    adentro: TestClient, sesion: Session, reloj: dict, lunes_con_cobros, correo_configurado: list
):
    """La caja del lunes se cierra a las 12:30 a.m. del martes: llega el reporte del lunes,
    no uno del martes casi vacío."""
    guardar_ajustes(sesion, ajustes_con(sesion, correos_reporte="jefe@x.co"))
    sesion.commit()
    app.dependency_overrides[pantallas.obtener_abrir_sesion] = lambda: misma_sesion(sesion)
    reloj["ahora"] = a_las(date(2026, 10, 27), 0, 30)
    caja = caja_abierta(sesion)

    respuesta = adentro.post(
        "/parqueadero/caja/cerrar",
        data={"caja_id": caja.id, "efectivo_contado": "61.000"},
        headers=HTMX,
    )

    assert respuesta.headers["HX-Redirect"] == f"/parqueadero/caja/{caja.id}?imprimir=1"
    (mensaje,) = correo_configurado
    assert mensaje["Subject"] == "Parqueadero · Reporte del 26 de octubre de 2026: $12.500"
    cuerpo = mensaje.get_content()
    assert "Cerró eduar el 27 de octubre a las 12:30 a.m." in cuerpo
    assert "Cuadró exacto." in cuerpo  # 50.000 + 6.000 + 5.000 en efectivo


def test_si_el_correo_falla_la_caja_igual_se_cierra(
    adentro: TestClient,
    sesion: Session,
    lunes_con_cobros,
    correo_configurado: list,
    monkeypatch: pytest.MonkeyPatch,
):
    import smtplib

    from pruebas.correo_falso import ServidorFalso

    guardar_ajustes(sesion, ajustes_con(sesion, correos_reporte="jefe@x.co"))
    sesion.commit()
    app.dependency_overrides[pantallas.obtener_abrir_sesion] = lambda: misma_sesion(sesion)
    monkeypatch.setattr(smtplib, "SMTP_SSL", ServidorFalso([], fallar=ConnectionRefusedError()))
    caja = caja_abierta(sesion)

    respuesta = adentro.post(
        "/parqueadero/caja/cerrar",
        data={"caja_id": caja.id, "efectivo_contado": "61.000"},
        headers=HTMX,
    )

    assert "HX-Redirect" in respuesta.headers
    sesion.refresh(caja)
    assert caja.cerrada_en is not None


def test_sin_correo_configurado_cerrar_la_caja_no_intenta_mandar(
    adentro: TestClient, sesion: Session, lunes_con_cobros
):
    guardar_ajustes(sesion, ajustes_con(sesion, correos_reporte="jefe@x.co"))
    sesion.commit()

    def no_deberia_abrir():
        raise AssertionError("Sin correo configurado no debía abrir otra sesión")

    app.dependency_overrides[pantallas.obtener_abrir_sesion] = lambda: no_deberia_abrir
    caja = caja_abierta(sesion)

    respuesta = adentro.post(
        "/parqueadero/caja/cerrar",
        data={"caja_id": caja.id, "efectivo_contado": "61.000"},
        headers=HTMX,
    )

    assert "HX-Redirect" in respuesta.headers


# --- Los correos de los jefes en Tarifas ----------------------------------------------------

TARIFAS = {
    "hora_carro": "3.000",
    "hora_moto": "1.500",
    "gracia_minutos": "15",
    "nombre": "Parqueadero",
    "ancho_ticket": "80",
}


def test_guardar_los_correos_de_los_jefes(adentro: TestClient, sesion: Session):
    pagina = adentro.get("/parqueadero/tarifas").text
    assert "Reporte diario a los jefes" in pagina
    assert "El correo del sistema todavía no está configurado" in pagina

    respuesta = adentro.post(
        "/parqueadero/tarifas",
        data={**TARIFAS, "correos_reporte": "Jefe@Gmail.com; socio@gmail.com"},
        headers=HTMX,
    )

    assert "Tarifas guardadas" in aviso(respuesta)
    assert leer_ajustes(sesion).correos_reporte == "Jefe@gmail.com, socio@gmail.com"
    assert 'value="Jefe@gmail.com, socio@gmail.com"' in adentro.get("/parqueadero/tarifas").text


def test_un_correo_mal_escrito_no_guarda_nada(adentro: TestClient, sesion: Session):
    respuesta = adentro.post(
        "/parqueadero/tarifas",
        data={**TARIFAS, "hora_carro": "4.000", "correos_reporte": "jefe@gmail"},
        headers=HTMX,
    )

    assert "No se guardó nada: Revisa el correo «jefe@gmail»" in respuesta.text
    assert 'value="jefe@gmail"' in respuesta.text  # lo escrito sigue ahí para corregirlo
    assert leer_ajustes(sesion).hora_carro == 3000


def test_con_correo_configurado_no_sale_el_aviso(adentro: TestClient, correo_configurado: list):
    pagina = adentro.get("/parqueadero/tarifas").text

    assert "Reporte diario a los jefes" in pagina
    assert "todavía no está configurado" not in pagina


# --- Las consultas no crecen con los datos --------------------------------------------------


@contextmanager
def contar_consultas(conexion: Connection) -> Iterator[list[str]]:
    consultas: list[str] = []

    def anotar(_conexion, _cursor, sentencia, *_):
        consultas.append(sentencia)

    event.listen(conexion, "before_cursor_execute", anotar)
    try:
        yield consultas
    finally:
        event.remove(conexion, "before_cursor_execute", anotar)


def dias_de_trabajo(sesion: Session, desde: int, hasta: int) -> None:
    """Del día `desde` al `hasta` de octubre: cada día una caja, dos salidas, una mensualidad
    nueva que se paga y un carro que se queda adentro."""
    for dia_del_mes in range(desde, hasta + 1):
        dia = date(2026, 10, dia_del_mes)
        caja = casos.abrir_caja(sesion, base=10000, ahora=a_las(dia, 6))
        for placa, tipo in (
            (f"SAL{dia_del_mes:02d}0", TipoParqueo.CARRO),
            (f"MOT{dia_del_mes:02d}A", TipoParqueo.MOTO),
        ):
            parqueo = casos.registrar_entrada(sesion, placa=placa, tipo=tipo, ahora=a_las(dia, 7))
            casos.cobrar_salida(
                sesion, parqueo.id, medio_pago=MedioPago.EFECTIVO, ahora=a_las(dia, 9)
            )
        mensualidad = casos.crear_mensualidad(
            sesion,
            placa=f"MEN{dia_del_mes:02d}0",
            tipo=TipoParqueo.CARRO,
            titular="Cliente de prueba",
            valor_mensual=100000,
            autorizo_datos=True,
            ahora=a_las(dia, 10),
        )
        casos.pagar_mensualidad(
            sesion,
            mensualidad.id,
            desde=dia,
            hasta=date.fromordinal(dia.toordinal() + 29),
            valor=100000,
            medio_pago=MedioPago.NEQUI,
            ahora=a_las(dia, 10),
        )
        casos.registrar_entrada(
            sesion, placa=f"MEN{dia_del_mes:02d}0", tipo=TipoParqueo.CARRO, ahora=a_las(dia, 11)
        )
        casos.cerrar_caja(sesion, caja.id, efectivo_contado=14500, ahora=a_las(dia, 20))
    sesion.commit()


@pytest.mark.parametrize(
    "ruta",
    [
        "/parqueadero",
        "/parqueadero/adentro",
        "/parqueadero/caja",
        "/parqueadero/mensualidades",
        "/parqueadero/balance?periodo=mes&fecha=2026-10-01",
        "/parqueadero/balance?periodo=semana&fecha=2026-10-05",
        "/parqueadero/balance?fecha=2026-10-05",
        "/parqueadero/balance/ticket?fecha=2026-10-05",
    ],
)
def test_mas_datos_no_son_mas_consultas(
    adentro: TestClient, sesion: Session, conexion: Connection, ruta: str
):
    dias_de_trabajo(sesion, 1, 5)
    with contar_consultas(conexion) as con_poco:
        assert adentro.get(ruta).status_code == 200

    dias_de_trabajo(sesion, 6, 20)
    with contar_consultas(conexion) as con_mucho:
        assert adentro.get(ruta).status_code == 200

    assert len(con_mucho) == len(con_poco), "\n\n".join(con_mucho)
