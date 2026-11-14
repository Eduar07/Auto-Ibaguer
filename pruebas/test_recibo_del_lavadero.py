"""El recibo del lavadero y su reporte del día por correo.

El recibo de un turno cobrado lleva los datos del negocio (nombre y teléfono de
Configuración, NIT, dirección y texto del final de Configuración > Recibo y reporte), y se
imprime al cobrar si así se pidió, o después desde la caja. El reporte del día les llega a los
jefes por correo al cerrar la caja del lavadero.

Lo que tiene que cumplirse siempre:
- el recibo no lleva el nombre ni el celular del cliente: solo la placa;
- un NIT o un correo mal escrito no se guarda, y se dice cuál;
- el reporte que llega al cerrar es el del día en que se abrió la caja (si se cierra pasada
  la medianoche, sigue siendo el de ese día);
- si el correo falla, la caja igual queda cerrada.
"""

import smtplib
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app import formato
from app.catalogo.parametros import leer_parametros
from app.integraciones import correo
from app.main import app
from app.turnos import caja as caja_lavadero
from app.turnos import casos_de_uso as casos
from app.turnos import recibo, reporte
from app.turnos.estados import MedioPago
from app.turnos.recibo import AjustesDelRecibo, AjustesInvalidos
from app.web import caja_del_lavadero as pantallas
from pruebas.correo_falso import ServidorFalso
from pruebas.fabricas import BOGOTA, LUNES_8AM, minutos, misma_sesion, servicio, turno_listo
from pruebas.navegador import HTMX, aviso, error, eventos

HOY = LUNES_8AM.date()
MARTES_1230AM = datetime(2026, 10, 27, 0, 30, tzinfo=BOGOTA)


def ajustes(**cambios) -> AjustesDelRecibo:
    valores = {
        "nit": "",
        "direccion": "",
        "pie_recibo": "Gracias por preferirnos.",
        "ancho_ticket": 80,
        "imprimir_al_cobrar": False,
        "correos_reporte": "",
    }
    return AjustesDelRecibo(**(valores | cambios))


def cobrado(sesion: Session, admin=None, **datos) -> int:
    """Un turno lavado y cobrado (Básico: $40.000 carro, $50.000 camioneta)."""
    placa = datos.pop("placa", "ABC123")
    tipo = datos.pop("tipo", "Automóvil")
    turno = turno_listo(sesion, placa=placa, tipo=tipo)
    casos.cobrar(
        sesion,
        turno,
        medio_pago=datos.pop("medio_pago", MedioPago.EFECTIVO),
        administrador_id=admin.id if admin else None,
        ahora=LUNES_8AM + minutos(60),
        **datos,
    )
    return turno


# --- Los datos del recibo -------------------------------------------------------------------


def test_sin_guardar_nada_el_recibo_trae_lo_basico(sesion: Session):
    leidos = recibo.leer_ajustes(sesion)

    assert leidos == ajustes()
    assert leidos.destinos_del_reporte == []
    datos = recibo.datos_del_ticket(sesion)
    assert (datos.nombre, datos.pie_ticket, datos.ancho_ticket) == (
        "Auto Ibaguer",
        "Gracias por preferirnos.",
        80,
    )
    # El teléfono del negocio, como se dice en Colombia (el mismo que da el bot).
    assert datos.telefono == formato.telefono(leer_parametros(sesion).telefono_contacto)
    assert len(datos.telefono.split()) == 3


def test_los_datos_del_recibo_se_guardan_limpios(sesion: Session):
    recibo.guardar_ajustes(
        sesion,
        ajustes(
            nit="NIT: 900.123.456-7",
            direccion="  Calle 45 # 12-30  ",
            pie_recibo="Vuelva pronto",
            ancho_ticket=58,
            imprimir_al_cobrar=True,
            correos_reporte="jefe@x.co; socio@x.co jefe@x.co",
        ),
    )

    leidos = recibo.leer_ajustes(sesion)
    assert leidos == ajustes(
        nit="900.123.456-7",
        direccion="Calle 45 # 12-30",
        pie_recibo="Vuelva pronto",
        ancho_ticket=58,
        imprimir_al_cobrar=True,
        correos_reporte="jefe@x.co, socio@x.co",
    )
    assert leidos.destinos_del_reporte == ["jefe@x.co", "socio@x.co"]


@pytest.mark.parametrize(
    ("cambios", "dice"),
    [
        ({"nit": "Auto Ibaguer"}, "El NIT es solo el número"),
        ({"correos_reporte": "jefe@"}, "Revisa el correo «jefe@»"),
        ({"correos_reporte": ", ".join(f"j{n}@x.co" for n in range(6))}, "máximo 5 correos"),
        ({"ancho_ticket": 70}, "58 o de 80 milímetros"),
        ({"direccion": "x" * 81}, "La dirección es muy larga: máximo 80 letras."),
        ({"pie_recibo": "x" * 241}, "El texto del final es muy largo: máximo 240 letras."),
    ],
)
def test_lo_mal_escrito_no_se_guarda(sesion: Session, cambios: dict, dice: str):
    with pytest.raises(AjustesInvalidos, match=dice):
        recibo.guardar_ajustes(sesion, ajustes(**cambios))

    assert recibo.leer_ajustes(sesion) == ajustes()


# --- En Configuración ----------------------------------------------------------------------


def test_la_seccion_en_configuracion(adentro: TestClient):
    pagina = adentro.get("/configuracion").text

    assert '<a href="#recibo">Recibo y reporte</a>' in pagina
    assert '<section id="recibo"' in pagina
    assert 'hx-post="/configuracion/recibo"' in pagina
    assert 'value="Gracias por preferirnos."' in pagina
    # Sin el correo del sistema configurado, se dice que por ahora el reporte va por WhatsApp.
    assert "El correo del sistema todavía no está configurado" in pagina


def test_guardar_el_recibo_desde_la_pantalla(adentro: TestClient, sesion: Session):
    respuesta = adentro.post(
        "/configuracion/recibo",
        data={
            "nit": "900123456-7",
            "direccion": "Calle 45 # 12-30",
            "pie_recibo": "Vuelva pronto",
            "ancho_ticket": "58",
            "imprimir_al_cobrar": "si",
            "correos_reporte": "jefe@x.co",
        },
        headers=HTMX,
    )

    assert aviso(respuesta) == "Recibo y reporte guardados."
    assert 'value="900123456-7"' in respuesta.text
    assert recibo.leer_ajustes(sesion) == ajustes(
        nit="900123456-7",
        direccion="Calle 45 # 12-30",
        pie_recibo="Vuelva pronto",
        ancho_ticket=58,
        imprimir_al_cobrar=True,
        correos_reporte="jefe@x.co",
    )


def test_un_error_se_muestra_con_lo_que_se_escribio(adentro: TestClient, sesion: Session):
    respuesta = adentro.post(
        "/configuracion/recibo",
        data={"nit": "900123456-7", "ancho_ticket": "80", "correos_reporte": "jefe@gmail"},
        headers=HTMX,
    )

    assert respuesta.status_code == 200 and aviso(respuesta) == ""
    assert "Revisa el correo «jefe@gmail»" in respuesta.text
    assert 'value="900123456-7"' in respuesta.text  # no hay que escribir todo de nuevo
    assert recibo.leer_ajustes(sesion) == ajustes()


@pytest.mark.parametrize("ancho", ["", "²", "abc"])
def test_un_ancho_raro_se_explica(adentro: TestClient, ancho: str):
    respuesta = adentro.post("/configuracion/recibo", data={"ancho_ticket": ancho}, headers=HTMX)

    assert respuesta.status_code == 200
    assert "58 o de 80 milímetros" in respuesta.text


# --- El recibo -------------------------------------------------------------------------------


def test_el_recibo_de_un_cobro(adentro: TestClient, sesion: Session, admin, caja_del_lavadero):
    recibo.guardar_ajustes(
        sesion, ajustes(nit="900.123.456-7", direccion="Calle 45 # 12-30", ancho_ticket=58)
    )
    turno = cobrado(
        sesion,
        admin,
        placa="XYZ789",
        tipo="Camioneta",
        medio_pago=MedioPago.NEQUI,
        precio_cobrado=45_000,
        motivo="Cliente frecuente",
    )
    sesion.commit()

    respuesta = adentro.get(f"/turnos/{turno}/recibo")

    assert respuesta.status_code == 200
    assert respuesta.headers["X-Frame-Options"] == "SAMEORIGIN"
    texto = respuesta.text
    assert '<body class="ticket ticket--58">' in texto
    for esperado in (
        "Auto Ibaguer",
        "NIT 900.123.456-7",
        "Calle 45 # 12-30",
        "Recibo de pago",
        "XYZ789",
        "Camioneta",
        "<dt>Servicio</dt><dd>Básico</dd>",
        "<dt>Precio</dt><dd>$50.000</dd>",
        "<dt>Descuento</dt><dd>−$5.000</dd>",
        "<strong>$45.000</strong>",
        "<dt>Pago</dt><dd>Nequi</dd>",
        "<dt>Atendió</dt><dd>eduar</dd>",
        "Gracias por preferirnos.",
    ):
        assert esperado in texto, esperado
    # El motivo del descuento es para los jefes, no para el cliente.
    assert "Cliente frecuente" not in texto


def test_un_cobro_de_mas_sale_como_recargo(adentro: TestClient, sesion: Session, caja_del_lavadero):
    turno = cobrado(sesion, precio_cobrado=45_000, motivo="Venía muy sucio")
    sesion.commit()

    texto = adentro.get(f"/turnos/{turno}/recibo").text

    assert "<dt>Recargo</dt><dd>+$5.000</dd>" in texto
    assert "<strong>$45.000</strong>" in texto


def test_el_cobro_al_precio_del_menu_no_muestra_ajuste(
    adentro: TestClient, sesion: Session, caja_del_lavadero
):
    turno = cobrado(sesion)
    sesion.commit()

    texto = adentro.get(f"/turnos/{turno}/recibo").text

    assert "Descuento" not in texto and "Recargo" not in texto and "<dt>Precio</dt>" not in texto


def test_el_recibo_no_lleva_datos_del_cliente(
    adentro: TestClient, sesion: Session, caja_del_lavadero
):
    turno = cobrado(sesion)
    sesion.commit()

    texto = adentro.get(f"/turnos/{turno}/recibo").text

    assert "ABC123" in texto
    assert "Cliente" not in texto and "+57" not in texto and "310" not in texto


def test_sin_cobro_no_hay_recibo(adentro: TestClient, sesion: Session):
    turno = turno_listo(sesion)
    sesion.commit()

    assert adentro.get(f"/turnos/{turno}/recibo").status_code == 404
    assert adentro.get("/turnos/999999/recibo").status_code == 404


def test_al_cobrar_se_imprime_el_recibo_si_asi_se_pidio(
    adentro: TestClient, sesion: Session, caja_del_lavadero
):
    recibo.guardar_ajustes(sesion, ajustes(imprimir_al_cobrar=True))
    turno = turno_listo(sesion)
    sesion.commit()

    respuesta = adentro.post(
        f"/turnos/{turno}/cobrar",
        data={"servicio_id": servicio(sesion, "Básico").id, "medio_pago": "efectivo"},
        headers=HTMX,
    )

    assert aviso(respuesta) == "C-01 cobrado: $40.000 en efectivo."
    assert eventos(respuesta)["imprimir"] == f"/turnos/{turno}/recibo"


def test_si_no_se_pidio_no_se_imprime_al_cobrar(
    adentro: TestClient, sesion: Session, caja_del_lavadero
):
    turno = turno_listo(sesion)
    sesion.commit()

    respuesta = adentro.post(
        f"/turnos/{turno}/cobrar",
        data={"servicio_id": servicio(sesion, "Básico").id, "medio_pago": "efectivo"},
        headers=HTMX,
    )

    assert aviso(respuesta) == "C-01 cobrado: $40.000 en efectivo."
    assert "imprimir" not in eventos(respuesta)


def test_desde_la_caja_se_vuelve_a_imprimir(
    adentro: TestClient, sesion: Session, caja_del_lavadero
):
    turno = cobrado(sesion)
    sesion.commit()

    caja = adentro.get("/caja").text

    assert f'data-imprimir="/turnos/{turno}/recibo"' in caja


# --- El reporte del día ---------------------------------------------------------------------


@pytest.fixture
def lunes(sesion: Session, admin):
    """La caja del lunes se abre a las 7:00 a.m. con $20.000 y se cobra un carro en efectivo
    ($40.000) y una camioneta por Nequi con descuento ($45.000)."""
    caja = caja_lavadero.abrir_caja(sesion, base=20_000, ahora=LUNES_8AM - minutos(60))
    cobrado(sesion, admin, placa="ABC123")
    cobrado(
        sesion,
        admin,
        placa="XYZ789",
        tipo="Camioneta",
        medio_pago=MedioPago.NEQUI,
        precio_cobrado=45_000,
        motivo="Cliente frecuente",
    )
    return caja


def test_el_reporte_del_dia(sesion: Session, lunes):
    informe = reporte.armar(sesion, HOY, LUNES_8AM + minutos(600))

    texto = reporte.texto(informe)

    assert texto.splitlines()[:3] == [
        "*Auto Ibaguer*",
        "Reporte del lavadero del lunes 26 de octubre de 2026, hasta las 6:00 p.m.",
        "",
    ]
    for esperado in (
        "*Cobrado: $85.000*\nEfectivo: $40.000\nNequi: $45.000",
        "Turnos cobrados: 2 · promedio $42.500",
        "Servicios: Básico 2",
        "Turnos pedidos: 2 (WhatsApp 0, en el local 2)",
        "*Cobros distintos al menú*\nDescuentos: $5.000 en 1 turno",
        "- XYZ789: $50.000 → $45.000 (Cliente frecuente)",
        "*Caja*\nSigue abierta: debería haber $60.000 en efectivo (con la base).",
    ):
        assert esperado in texto, esperado
    assert "Cancelados" not in texto  # nada que decir: no sale


def test_el_reporte_de_un_dia_sin_nada(sesion: Session):
    texto = reporte.texto(reporte.armar(sesion, HOY, LUNES_8AM + minutos(600)), negrilla=False)

    assert "No se cobró nada." in texto
    assert "No se abrió caja este día." in texto
    assert "Cobros distintos" not in texto and "*" not in texto


def test_el_reporte_de_un_dia_que_ya_paso_no_dice_hasta_que_hora(sesion: Session):
    texto = reporte.texto(reporte.armar(sesion, HOY, MARTES_1230AM))

    assert "Reporte del lavadero del lunes 26 de octubre de 2026\n" in texto


def test_el_asunto_dice_de_que_negocio_es(sesion: Session, lunes):
    informe = reporte.armar(sesion, HOY, MARTES_1230AM)

    assert reporte.asunto(informe) == (
        "Auto Ibaguer · Reporte del lavadero del 26 de octubre de 2026: $85.000"
    )


def test_el_reporte_por_correo(sesion: Session, lunes, correo_configurado: list):
    recibo.guardar_ajustes(sesion, ajustes(correos_reporte="jefe@x.co, socio@x.co"))

    cuantos = reporte.enviar_por_correo(sesion, HOY, MARTES_1230AM)

    assert cuantos == 2
    (mensaje,) = correo_configurado
    assert mensaje["To"] == "jefe@x.co, socio@x.co"
    assert mensaje["From"] == "Auto Ibaguer <parqueadero@gmail.com>"
    assert mensaje["Subject"].endswith("$85.000")
    cuerpo = mensaje.get_content()
    assert "Cobrado: $85.000" in cuerpo and "*" not in cuerpo


def test_sin_correos_dice_donde_escribirlos(sesion: Session, correo_configurado: list):
    with pytest.raises(correo.CorreoNoEnviado, match="Configuración > Recibo y reporte"):
        reporte.enviar_por_correo(sesion, HOY)

    assert correo_configurado == []


def test_mandar_el_reporte_ya_desde_el_balance(
    adentro: TestClient, sesion: Session, lunes, correo_configurado: list
):
    recibo.guardar_ajustes(sesion, ajustes(correos_reporte="jefe@x.co, socio@x.co"))
    sesion.commit()
    pagina = adentro.get("/balance", params={"fecha": "2026-10-26"}).text
    assert "Enviar por correo ahora" in pagina

    respuesta = adentro.post("/balance/enviar", data={"fecha": "2026-10-26"}, headers=HTMX)

    assert aviso(respuesta) == "Reporte enviado a 2 correos."
    assert len(correo_configurado) == 1


def test_sin_correos_el_balance_lo_dice(adentro: TestClient, correo_configurado: list):
    respuesta = adentro.post("/balance/enviar", data={}, headers=HTMX)

    assert "Configuración > Recibo y reporte" in error(respuesta)
    assert respuesta.headers["HX-Reswap"] == "none"


def test_al_cerrar_la_caja_les_llega_el_reporte_del_dia_de_la_caja(
    adentro: TestClient, sesion: Session, reloj: dict, lunes, correo_configurado: list
):
    """La caja del lunes se cierra a las 12:30 a.m. del martes: llega el reporte del lunes,
    no uno del martes casi vacío."""
    recibo.guardar_ajustes(sesion, ajustes(correos_reporte="jefe@x.co"))
    sesion.commit()
    app.dependency_overrides[pantallas.obtener_abrir_sesion] = lambda: misma_sesion(sesion)
    reloj["ahora"] = MARTES_1230AM

    respuesta = adentro.post(
        "/caja/cerrar",
        data={"caja_id": lunes.id, "efectivo_contado": "60.000"},
        headers=HTMX,
    )

    assert respuesta.headers["HX-Redirect"] == f"/caja/{lunes.id}?imprimir=1"
    (mensaje,) = correo_configurado
    assert mensaje["Subject"] == (
        "Auto Ibaguer · Reporte del lavadero del 26 de octubre de 2026: $85.000"
    )
    cuerpo = mensaje.get_content()
    assert "Cerró eduar el 27 de octubre a las 12:30 a.m." in cuerpo
    assert "Base $20.000 · Debía haber $60.000 · Contaron $60.000" in cuerpo
    assert "Cuadró exacto." in cuerpo


def test_si_el_correo_falla_la_caja_igual_se_cierra(
    adentro: TestClient,
    sesion: Session,
    lunes,
    correo_configurado: list,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
):
    recibo.guardar_ajustes(sesion, ajustes(correos_reporte="jefe@x.co"))
    sesion.commit()
    app.dependency_overrides[pantallas.obtener_abrir_sesion] = lambda: misma_sesion(sesion)
    monkeypatch.setattr(
        smtplib, "SMTP_SSL", ServidorFalso(correo_configurado, fallar=ConnectionRefusedError())
    )

    respuesta = adentro.post(
        "/caja/cerrar", data={"caja_id": lunes.id, "efectivo_contado": "60.000"}, headers=HTMX
    )

    assert respuesta.headers["HX-Redirect"] == f"/caja/{lunes.id}?imprimir=1"
    assert not caja_lavadero.caja_del_lavadero(sesion, lunes.id).abierta
    assert correo_configurado == []
    assert "El reporte del lavadero no salió por correo" in caplog.text
    assert "jefe@x.co" not in caplog.text  # el registro no guarda direcciones


def test_al_cerrar_sin_correos_no_manda_nada(sesion: Session, correo_configurado: list):
    reporte.enviar_al_cerrar(misma_sesion(sesion), HOY)

    assert correo_configurado == []
