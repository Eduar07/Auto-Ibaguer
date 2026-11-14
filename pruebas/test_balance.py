"""El balance del parqueadero (día, semana y mes), el reporte del día para los jefes y el
correo que lo lleva.

Casi todas las pruebas usan la misma semana armada a mano, con de todo: cobros en efectivo y
por Nequi, un descuento, una entrada anulada, mensualistas al día, uno que ya traía días
pagados, uno que no ha pagado, una moto que sale a las 11:30 p.m. (en UTC ya es el día
siguiente) y una caja que se cierra pasada la medianoche. Las cuentas de cada día están
escritas junto a cada movimiento: así se puede revisar a mano.
"""

import smtplib
from dataclasses import replace
from datetime import date, datetime, timedelta
from urllib.parse import unquote

import pytest
from sqlalchemy.orm import Session

from app.config import obtener_entorno
from app.integraciones import correo
from app.parqueadero import balance as modulo_balance
from app.parqueadero import casos_de_uso as casos
from app.parqueadero import reporte
from app.parqueadero.balance import balance, periodo, total_recogido
from app.parqueadero.errores import TarifaInvalida
from app.parqueadero.estados import TipoParqueo
from app.parqueadero.tablas import Caja, Parqueo
from app.parqueadero.tarifas import (
    AjustesParqueadero,
    guardar_ajustes,
    leer_ajustes,
    limpiar_correos,
)
from app.turnos.estados import MedioPago
from pruebas.correo_falso import ServidorFalso
from pruebas.fabricas import BOGOTA, misma_sesion

CARRO, MOTO = TipoParqueo.CARRO, TipoParqueo.MOTO
EFECTIVO, NEQUI = MedioPago.EFECTIVO, MedioPago.NEQUI

DOMINGO = date(2026, 10, 25)  # la semana anterior
LUNES = date(2026, 10, 26)
MARTES = date(2026, 10, 27)
MIERCOLES = date(2026, 10, 28)
DOMINGO_1 = date(2026, 11, 1)  # la misma semana, pero otro mes


def a_las(dia: date, hora: int, minuto: int = 0) -> datetime:
    return datetime(dia.year, dia.month, dia.day, hora, minuto, tzinfo=BOGOTA)


def estadia(
    sesion: Session, placa: str, tipo: TipoParqueo, entra: datetime, sale: datetime, **cobro
) -> Parqueo:
    parqueo = casos.registrar_entrada(sesion, placa=placa, tipo=tipo, ahora=entra)
    cobro.setdefault("medio_pago", EFECTIVO)
    return casos.cobrar_salida(sesion, parqueo.id, ahora=sale, **cobro)


def mensualista(sesion: Session, placa: str, tipo: TipoParqueo, ahora: datetime, **datos):
    return casos.crear_mensualidad(
        sesion,
        placa=placa,
        tipo=tipo,
        titular="Ana Ruiz",
        telefono="322 755 6850",
        valor_mensual=datos.pop("valor", 120000),
        autorizo_datos=True,
        ahora=ahora,
        **datos,
    )


def ajustes_con(sesion: Session, **cambios) -> AjustesParqueadero:
    return replace(leer_ajustes(sesion), **cambios)


@pytest.fixture
def semana(sesion: Session) -> dict[str, Caja]:
    # Domingo 25 (semana anterior): $3.000. Su caja abre y cierra ese día y cuadra.
    z = casos.abrir_caja(sesion, base=20000, ahora=a_las(DOMINGO, 8))
    estadia(sesion, "SUN111", CARRO, a_las(DOMINGO, 9), a_las(DOMINGO, 10))
    casos.cerrar_caja(sesion, z.id, efectivo_contado=23000, ahora=a_las(DOMINGO, 18))

    # Lunes 26: $137.500 = efectivo $13.000 + Nequi $124.500.
    a = casos.abrir_caja(sesion, base=50000, ahora=a_las(LUNES, 7))
    estadia(sesion, "ABC123", CARRO, a_las(LUNES, 8), a_las(LUNES, 10))  # 2 h: $6.000
    estadia(sesion, "ABC12D", MOTO, a_las(LUNES, 8), a_las(LUNES, 9), medio_pago=NEQUI)  # $1.500
    estadia(  # tarifa 3 h = $9.000, se cobraron $7.000: un descuento de $2.000
        sesion,
        "XYZ789",
        CARRO,
        a_las(LUNES, 9),
        a_las(LUNES, 12),
        valor_cobrado=7000,
        motivo="Cliente frecuente",
    )
    error = casos.registrar_entrada(sesion, placa="ERR111", tipo=CARRO, ahora=a_las(LUNES, 10))
    casos.anular_entrada(sesion, error.id, motivo="Placa mal escrita", ahora=a_las(LUNES, 10, 5))
    men = mensualista(sesion, "MEN123", CARRO, a_las(LUNES, 10, 30))
    casos.pagar_mensualidad(  # $120.000 por Nequi
        sesion,
        men.id,
        desde=LUNES,
        hasta=LUNES + timedelta(days=29),
        valor=120000,
        medio_pago=NEQUI,
        ahora=a_las(LUNES, 11),
    )
    estadia(sesion, "MEN123", CARRO, a_las(LUNES, 13), a_las(LUNES, 15))  # al día: $0
    # Ya traía pagado con el dueño anterior: un pago en $0 sin caja, que no es plata de hoy.
    mensualista(sesion, "TRA456", CARRO, a_las(LUNES, 13, 30), pagada_hasta=date(2026, 11, 10))
    estadia(sesion, "TRA456", CARRO, a_las(LUNES, 14), a_las(LUNES, 16))  # al día: $0
    mensualista(sesion, "PEN789", MOTO, a_las(LUNES, 17), valor=60000)  # no ha pagado
    noche = casos.registrar_entrada(sesion, placa="NOC001", tipo=CARRO, ahora=a_las(LUNES, 20))
    # 11:30 p.m. en Bogotá ya es martes en UTC: igual cuenta el lunes. 1 h 30 min: $3.000.
    estadia(sesion, "LAT99E", MOTO, a_las(LUNES, 22), a_las(LUNES, 23, 30), medio_pago=NEQUI)

    # Martes 27: $13.500. La caja del lunes cobra a NOC001 y se cierra pasada la medianoche.
    casos.cobrar_salida(sesion, noche.id, medio_pago=EFECTIVO, ahora=a_las(MARTES, 0, 15))  # 4 h
    casos.cerrar_caja(  # esperaba 50.000 + 6.000 + 7.000 + 12.000 = 75.000: faltaron 1.000
        sesion, a.id, efectivo_contado=74000, nota="Faltó uno de mil", ahora=a_las(MARTES, 0, 30)
    )
    b = casos.abrir_caja(sesion, base=50000, ahora=a_las(MARTES, 7))
    estadia(sesion, "MOT22E", MOTO, a_las(MARTES, 8), a_las(MARTES, 8, 30))  # $1.500

    # Domingo 1 de noviembre: $6.000, con la caja del martes todavía abierta.
    estadia(sesion, "NOV001", CARRO, a_las(DOMINGO_1, 10), a_las(DOMINGO_1, 12))
    return {"z": z, "a": a, "b": b}


# --- Los periodos -------------------------------------------------------------------------


def test_la_semana_va_de_lunes_a_domingo():
    for dia in (LUNES, MIERCOLES, DOMINGO_1):
        semana = periodo("semana", dia)
        assert (semana.desde, semana.hasta) == (LUNES, DOMINGO_1)
    assert periodo("semana", DOMINGO).desde == date(2026, 10, 19)
    assert len(periodo("semana", LUNES).dias) == 7


@pytest.mark.parametrize(
    ("dia", "primero", "ultimo"),
    [
        (date(2026, 10, 15), date(2026, 10, 1), date(2026, 10, 31)),
        (date(2026, 11, 30), date(2026, 11, 1), date(2026, 11, 30)),
        (date(2026, 12, 31), date(2026, 12, 1), date(2026, 12, 31)),
        (date(2028, 2, 10), date(2028, 2, 1), date(2028, 2, 29)),  # bisiesto
    ],
)
def test_el_mes_va_del_primero_al_ultimo_dia(dia: date, primero: date, ultimo: date):
    mes = periodo("mes", dia)

    assert (mes.desde, mes.hasta) == (primero, ultimo)
    assert len(mes.dias) == ultimo.day


def test_anterior_y_siguiente():
    assert periodo("dia", LUNES).anterior() == periodo("dia", DOMINGO)
    assert periodo("semana", LUNES).siguiente().desde == date(2026, 11, 2)
    assert periodo("mes", date(2026, 1, 20)).anterior() == periodo("mes", date(2025, 12, 1))
    assert periodo("mes", date(2026, 12, 5)).siguiente().desde == date(2027, 1, 1)


def test_los_titulos():
    assert periodo("dia", MARTES).titulo == "Martes 27 de octubre de 2026"
    assert periodo("semana", LUNES).titulo == "Semana del 26 de octubre al 1 de noviembre de 2026"
    assert periodo("semana", date(2026, 11, 10)).titulo == "Semana del 9 al 15 de noviembre de 2026"
    assert (
        periodo("semana", date(2026, 12, 30)).titulo
        == "Semana del 28 de diciembre de 2026 al 3 de enero de 2027"
    )
    assert periodo("mes", LUNES).titulo == "Octubre de 2026"


def test_con_que_se_compara():
    hoy = MARTES
    assert periodo("dia", hoy).nombre_del_anterior(hoy) == "Ayer (todo el día)"
    assert periodo("semana", hoy).nombre_del_anterior(hoy) == "La semana pasada (completa)"
    assert periodo("mes", hoy).nombre_del_anterior(hoy) == "El mes pasado (completo)"
    assert periodo("dia", DOMINGO).nombre_del_anterior(hoy) == "El día anterior"


# --- El balance ---------------------------------------------------------------------------


def test_el_balance_del_dia_con_todo(sesion: Session, semana):
    b = balance(sesion, periodo("dia", LUNES))

    assert b.total == 137500
    assert b.por_medio == {
        MedioPago.EFECTIVO: 13000,
        MedioPago.NEQUI: 124500,
        MedioPago.DAVIPLATA: 0,
        MedioPago.TARJETA: 0,
    }
    # Entradas: la anulada no cuenta. Salidas: también las de los mensualistas en $0.
    assert b.entradas == {CARRO: 5, MOTO: 2}
    assert b.salidas == {CARRO: 4, MOTO: 2}
    assert b.cobrado_salidas == {CARRO: 13000, MOTO: 4500}
    assert b.mensualistas_sin_pagar == 2
    # El que ya traía días pagados no suma: esa plata no la recibió este sistema.
    assert (b.pagos_mensualidad, b.cobrado_mensualidades) == (1, 120000)


def test_los_descuentos_y_las_anuladas_quedan_con_su_motivo(sesion: Session, semana):
    b = balance(sesion, periodo("dia", LUNES))

    assert [(a.placa, a.calculado, a.cobrado, a.motivo) for a in b.ajustes] == [
        ("XYZ789", 9000, 7000, "Cliente frecuente")
    ]
    assert (b.descuentos, b.salidas_con_descuento, b.recargos) == (2000, 1, 0)
    assert [(a.placa, a.motivo) for a in b.anuladas] == [("ERR111", "Placa mal escrita")]
    assert b.total_anuladas == 1


def test_cobrar_de_mas_sale_aparte_de_los_descuentos(sesion: Session, semana):
    estadia(  # tarifa $3.000; se cobró la noche completa
        sesion,
        "RCG001",
        CARRO,
        a_las(MIERCOLES, 8),
        a_las(MIERCOLES, 9),
        valor_cobrado=10000,
        motivo="Noche completa",
    )

    b = balance(sesion, periodo("dia", MIERCOLES))

    assert (b.descuentos, b.recargos, b.total) == (0, 7000, 10000)


def test_lo_cobrado_cuenta_el_dia_de_bogota(sesion: Session, semana):
    """La moto que salió a las 11:30 p.m. del lunes (4:30 a.m. del martes en UTC) es del
    lunes; el carro que salió a las 12:15 a.m. del martes es del martes."""
    lunes = balance(sesion, periodo("dia", LUNES))
    martes = balance(sesion, periodo("dia", MARTES))

    assert lunes.cobrado_salidas[MOTO] == 1500 + 3000
    assert martes.cobrado_salidas == {CARRO: 12000, MOTO: 1500}
    assert martes.total == 13500


def test_cada_caja_es_del_dia_en_que_se_abrio(sesion: Session, semana):
    """La del lunes se cerró a las 12:30 a.m. del martes: sale el lunes (y no el martes)."""
    lunes = balance(sesion, periodo("dia", LUNES))
    martes = balance(sesion, periodo("dia", MARTES))

    assert [r.caja.id for r in lunes.cajas] == [semana["a"].id]
    assert lunes.diferencia_de_cajas == -1000
    assert lunes.cajas[0].cuadre.total == 149500  # incluye lo que cobró pasada la medianoche
    assert [r.caja.id for r in martes.cajas] == [semana["b"].id]
    assert martes.cajas_cerradas == []  # sigue abierta
    assert balance(sesion, periodo("dia", MIERCOLES)).cajas == []


def test_los_sobrantes_y_los_faltantes_van_aparte(sesion: Session, semana):
    """Un sobrante de un día no tapa un faltante de otro: se ven los dos."""
    # La del martes esperaba 50.000 + 1.500 + 6.000 (lo del 1 de noviembre): sobran 500.
    casos.cerrar_caja(sesion, semana["b"].id, efectivo_contado=58000, ahora=a_las(DOMINGO_1, 21))

    octubre = balance(sesion, periodo("mes", LUNES))

    assert len(octubre.cajas_cerradas) == 3
    assert octubre.cajas_que_cuadraron == 1  # la del domingo 25
    assert (octubre.sobrantes, octubre.faltantes) == ([500], [1000])
    assert octubre.diferencia_de_cajas == -500


def test_la_semana_suma_sus_siete_dias_aunque_cambie_el_mes(sesion: Session, semana):
    b = balance(sesion, periodo("semana", LUNES))

    assert b.total == 137500 + 13500 + 6000
    assert [(d.dia, d.total) for d in b.dias if d.total] == [
        (LUNES, 137500),
        (MARTES, 13500),
        (DOMINGO_1, 6000),
    ]
    assert len(b.dias) == 7 and b.mayor_dia == 137500
    assert sum(d.entradas for d in b.dias) == b.cuantas_entradas == 7 + 1 + 1
    assert [r.caja.id for r in b.cajas] == [semana["a"].id, semana["b"].id]
    assert total_recogido(sesion, periodo("semana", LUNES).anterior()) == 3000


def test_el_mes(sesion: Session, semana):
    octubre = balance(sesion, periodo("mes", LUNES))
    noviembre = balance(sesion, periodo("mes", DOMINGO_1))

    assert octubre.total == 3000 + 137500 + 13500
    assert len(octubre.dias) == 31
    assert [r.caja.id for r in octubre.cajas] == [semana["z"].id, semana["a"].id, semana["b"].id]
    # Lo del 1 de noviembre es de noviembre, aunque la caja sea una que se abrió en octubre.
    assert (noviembre.total, noviembre.cajas) == (6000, [])
    assert total_recogido(sesion, periodo("mes", DOMINGO_1).anterior()) == octubre.total


def test_los_totales_coinciden_por_cualquier_camino(sesion: Session, semana):
    """El total del mes = la suma de sus días = la suma de sus medios de pago."""
    mes = balance(sesion, periodo("mes", LUNES))

    assert mes.total == sum(d.total for d in mes.dias) == sum(mes.por_medio.values())
    assert mes.total == total_recogido(sesion, periodo("mes", LUNES))
    por_dia = sum(balance(sesion, periodo("dia", d)).total for d in periodo("mes", LUNES).dias)
    assert por_dia == mes.total


def test_un_dia_sin_movimiento(sesion: Session):
    b = balance(sesion, periodo("dia", MIERCOLES))

    assert (b.total, b.cuantas_salidas, b.cuantas_entradas, b.cajas) == (0, 0, 0, [])
    assert b.mayor_dia == 0


def test_el_detalle_se_corta_pero_los_totales_cuentan_todo(
    sesion: Session, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(modulo_balance, "DETALLE_MAXIMO", 2)
    casos.abrir_caja(sesion, base=0, ahora=a_las(MIERCOLES, 7))
    for numero in range(3):
        estadia(
            sesion,
            f"DES00{numero}",
            CARRO,
            a_las(MIERCOLES, 8),
            a_las(MIERCOLES, 9),
            valor_cobrado=1000,
            motivo="Promoción",
        )
        anulada = casos.registrar_entrada(
            sesion, placa=f"ANU00{numero}", tipo=CARRO, ahora=a_las(MIERCOLES, 10)
        )
        casos.anular_entrada(sesion, anulada.id, motivo="Repetida", ahora=a_las(MIERCOLES, 11))

    b = balance(sesion, periodo("dia", MIERCOLES))

    assert (len(b.ajustes), b.cuantos_ajustes, b.descuentos) == (2, 3, 3 * 2000)
    assert (len(b.anuladas), b.total_anuladas) == (2, 3)


# --- El reporte del día -------------------------------------------------------------------


def test_el_reporte_del_lunes_mandado_al_cerrar_la_caja(sesion: Session, semana):
    """Se arma a las 12:30 a.m. del martes, al cerrar la caja del lunes."""
    informe = reporte.armar(sesion, LUNES, a_las(MARTES, 0, 30))

    assert not informe.es_de_hoy
    # A la medianoche seguía adentro NOC001 (salió a las 12:15 a.m.); la anulada no cuenta.
    assert informe.adentro == {CARRO: 1, MOTO: 0}
    assert informe.pendientes == ["PEN789"]
    texto = reporte.texto(informe, negrilla=False)
    assert texto.splitlines()[:8] == [
        "Parqueadero",
        "Reporte del lunes 26 de octubre de 2026",
        "",
        "Recogido: $137.500",
        "Efectivo: $13.000",
        "Nequi: $124.500",
        "",
        "Salidas: 6 · $17.500 (2 motos, 4 carros)",
    ]
    for linea in (
        "Mensualidades: 1 pago · $120.000",
        "Entradas: 7 (2 motos, 5 carros)",
        "Mensualistas al día que salieron: 2",
        "Descuentos: $2.000 en 1 salida",
        "- XYZ789: $9.000 → $7.000 (Cliente frecuente)",
        "Entradas anuladas: 1",
        "Cerró el 27 de octubre a las 12:30 a.m.",
        "Base $50.000 · Debía haber $75.000 · Contaron $74.000",
        "Faltaron $1.000.",
        "Nota: Faltó uno de mil",
        "Adentro al terminar el día: 1 (0 motos, 1 carro)",
        "Mensualidades pendientes de pago hoy (27 de octubre): 1 (PEN789)",
    ):
        assert linea in texto.splitlines(), linea
    assert "ERR111 (ticket" in texto and "Placa mal escrita" in texto
    assert "*" not in texto  # en el correo, sin asteriscos


def test_el_reporte_de_una_caja_que_cerro_el_mismo_dia(sesion: Session, semana):
    texto = reporte.texto(reporte.armar(sesion, DOMINGO, a_las(LUNES, 9)))

    assert "Cerró a las 6:00 p.m." in texto.splitlines()
    assert "Base $20.000 · Debía haber $23.000 · Contaron $23.000" in texto
    assert "Cuadró exacto." in texto


def test_el_reporte_de_hoy_dice_hasta_que_hora_va(sesion: Session):
    casos.abrir_caja(sesion, base=50000, ahora=a_las(MIERCOLES, 7))
    estadia(sesion, "MOT22E", MOTO, a_las(MIERCOLES, 8), a_las(MIERCOLES, 8, 30))  # $1.500
    casos.registrar_entrada(sesion, placa="ADE001", tipo=CARRO, ahora=a_las(MIERCOLES, 17))
    mensualista(sesion, "PEN789", MOTO, a_las(MIERCOLES, 9), valor=60000)

    informe = reporte.armar(sesion, MIERCOLES, a_las(MIERCOLES, 18, 5))

    assert informe.es_de_hoy
    texto = reporte.texto(informe)
    assert "Reporte del miércoles 28 de octubre de 2026, hasta las 6:05 p.m." in texto
    assert "*Recogido: $1.500*" in texto  # en WhatsApp, en negrilla
    assert "Sigue abierta: debería haber $51.500 en efectivo (con la base)." in texto
    assert "Quedan adentro: 1 (0 motos, 1 carro)" in texto
    assert "Mensualidades pendientes de pago: 1 (PEN789)" in texto
    assert "Mensualistas al día" not in texto  # nada que decir: no sale


def test_el_reporte_de_un_dia_sin_nada(sesion: Session):
    texto = reporte.texto(reporte.armar(sesion, MIERCOLES, a_las(MIERCOLES, 21)))

    assert "No se recogió plata." in texto
    assert "No se abrió caja este día." in texto
    assert "Cobros distintos" not in texto and "anuladas" not in texto
    assert "pendientes" not in texto


def test_el_reporte_no_lleva_nombres_ni_celulares(sesion: Session, semana):
    texto = reporte.texto(reporte.armar(sesion, LUNES, a_las(MARTES, 0, 30)))

    assert "Ana" not in texto and "Ruiz" not in texto
    assert "322" not in texto and "6850" not in texto


def test_el_reporte_corta_las_listas_largas(sesion: Session, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(reporte, "DETALLE_EN_EL_TEXTO", 2)
    for numero in range(4):
        mensualista(sesion, f"PEN00{numero}", MOTO, a_las(MIERCOLES, 8))

    texto = reporte.texto(reporte.armar(sesion, MIERCOLES, a_las(MIERCOLES, 9)))

    assert "Mensualidades pendientes de pago: 4 (PEN000, PEN001 y 2 más)" in texto


def test_el_enlace_de_whatsapp_lleva_el_reporte_escrito(sesion: Session, semana):
    informe = reporte.armar(sesion, LUNES, a_las(MARTES, 0, 30))

    enlace = reporte.enlace_de_whatsapp(informe)

    assert enlace.startswith("https://wa.me/?text=")
    assert " " not in enlace and "\n" not in enlace
    assert unquote(enlace.removeprefix("https://wa.me/?text=")) == reporte.texto(informe)


def test_el_asunto_del_correo(sesion: Session, semana):
    informe = reporte.armar(sesion, LUNES, a_las(MARTES, 0, 30))

    assert reporte.asunto(informe) == "Parqueadero · Reporte del 26 de octubre de 2026: $137.500"


# --- Los correos de los jefes ---------------------------------------------------------------


def test_los_correos_se_revisan_y_se_guardan_limpios():
    assert limpiar_correos("") == ""
    assert limpiar_correos(" Jefe@Gmail.com ;socio@hotmail.com\njefe@gmail.com ") == (
        "Jefe@gmail.com, socio@hotmail.com"
    )


@pytest.mark.parametrize(
    ("texto", "error"),
    [
        ("jefe@gmail", "Revisa el correo «jefe@gmail»"),
        ("jefe arroba gmail.com", "Revisa el correo «jefe»"),
        ("a@x.co, b@x.co, c@x.co, d@x.co, e@x.co, f@x.co", "máximo 5 correos"),
    ],
)
def test_un_correo_mal_escrito_no_se_guarda(texto: str, error: str):
    with pytest.raises(TarifaInvalida, match=error):
        limpiar_correos(texto)


def test_los_correos_quedan_en_los_ajustes(sesion: Session):
    guardar_ajustes(sesion, ajustes_con(sesion, correos_reporte="a@x.co; b@x.co"))

    ajustes = leer_ajustes(sesion)
    assert ajustes.correos_reporte == "a@x.co, b@x.co"
    assert ajustes.destinos_del_reporte == ["a@x.co", "b@x.co"]


# --- El correo ----------------------------------------------------------------------------


def test_el_correo_sale_con_el_reporte(sesion: Session, semana, correo_configurado: list):
    guardar_ajustes(
        sesion, ajustes_con(sesion, nombre="Parqueadero Central", correos_reporte="a@x.co, b@x.co")
    )

    cuantos = reporte.enviar_por_correo(sesion, LUNES, a_las(MARTES, 0, 30))

    assert cuantos == 2
    (mensaje,) = correo_configurado
    assert mensaje["To"] == "a@x.co, b@x.co"
    assert mensaje["From"] == "Parqueadero Central <parqueadero@gmail.com>"
    assert mensaje["Subject"] == "Parqueadero Central · Reporte del 26 de octubre de 2026: $137.500"
    cuerpo = mensaje.get_content()
    assert "Recogido: $137.500" in cuerpo and "*" not in cuerpo


def test_sin_configurar_no_sale_y_dice_por_que(sesion: Session):
    with pytest.raises(correo.CorreoNoEnviado, match="no está configurado"):
        correo.enviar("Asunto", "Texto", ["a@x.co"])


def test_sin_destinos_no_sale(correo_configurado: list):
    with pytest.raises(correo.CorreoNoEnviado, match="No hay correos"):
        correo.enviar("Asunto", "Texto", [])
    assert correo_configurado == []


def test_con_la_clave_equivocada_explica_la_de_aplicacion(
    correo_configurado: list, monkeypatch: pytest.MonkeyPatch
):
    falla = smtplib.SMTPAuthenticationError(535, b"Username and Password not accepted")
    monkeypatch.setattr(smtplib, "SMTP_SSL", ServidorFalso(correo_configurado, fallar=falla))

    with pytest.raises(correo.CorreoNoEnviado, match="contraseña de aplicación"):
        correo.enviar("Asunto", "Texto", ["a@x.co"])


def test_sin_conexion_avisa_lo_del_plan_gratis(
    correo_configurado: list, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    falla = TimeoutError("timed out")
    monkeypatch.setattr(smtplib, "SMTP_SSL", ServidorFalso(correo_configurado, fallar=falla))

    with pytest.raises(correo.CorreoNoEnviado, match="plan gratis de Render"):
        correo.enviar("Asunto", "Texto", ["jefe@x.co"])
    assert "jefe@x.co" not in caplog.text  # el registro no guarda direcciones


def test_en_el_587_se_cifra_antes_de_mandar_la_clave(
    correo_configurado: list, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(obtener_entorno(), "correo_puerto", 587)
    servidor = ServidorFalso(correo_configurado)
    monkeypatch.setattr(smtplib, "SMTP", servidor)

    correo.enviar("Asunto", "Texto", ["a@x.co"])

    assert servidor.conexion["tls"] is True and servidor.conexion["usuario"]
    assert len(correo_configurado) == 1


def test_la_clave_no_viaja_sin_cifrar(correo_configurado: list, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(obtener_entorno(), "correo_puerto", 25)
    servidor = ServidorFalso(correo_configurado, cifrado=False)
    monkeypatch.setattr(smtplib, "SMTP", servidor)

    with pytest.raises(correo.CorreoNoEnviado, match="no ofrece conexión cifrada"):
        correo.enviar("Asunto", "Texto", ["a@x.co"])
    assert correo_configurado == [] and servidor.cerrado


def test_un_servidor_de_prueba_sin_clave_si_recibe(
    correo_configurado: list, monkeypatch: pytest.MonkeyPatch
):
    """Para probar en tu computador: un servidor local sin cifrado y sin contraseña."""
    entorno = obtener_entorno()
    monkeypatch.setattr(entorno, "correo_servidor", "localhost")
    monkeypatch.setattr(entorno, "correo_puerto", 1025)
    monkeypatch.setattr(entorno, "correo_usuario", "")
    monkeypatch.setattr(entorno, "correo_clave", "")
    monkeypatch.setattr(smtplib, "SMTP", ServidorFalso(correo_configurado, cifrado=False))

    correo.enviar("Asunto", "Texto", ["a@x.co"])

    assert correo_configurado[0]["From"] == "Auto Ibaguer <parqueadero@localhost>"


# --- Al cerrar la caja ----------------------------------------------------------------------


def test_al_cerrar_sin_correo_configurado_no_hace_nada(sesion: Session):
    def no_deberia_abrir() -> Session:
        raise AssertionError("No debía abrir una sesión")

    reporte.enviar_al_cerrar(no_deberia_abrir, LUNES)


def test_al_cerrar_sin_destinos_no_manda(sesion: Session, correo_configurado: list):
    reporte.enviar_al_cerrar(misma_sesion(sesion), LUNES)

    assert correo_configurado == []


def test_al_cerrar_manda_y_si_falla_queda_en_el_registro(
    sesion: Session,
    semana,
    correo_configurado: list,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
):
    guardar_ajustes(sesion, ajustes_con(sesion, correos_reporte="jefe@x.co"))

    reporte.enviar_al_cerrar(misma_sesion(sesion), LUNES)
    assert len(correo_configurado) == 1

    monkeypatch.setattr(
        smtplib, "SMTP_SSL", ServidorFalso(correo_configurado, fallar=ConnectionRefusedError())
    )
    reporte.enviar_al_cerrar(misma_sesion(sesion), LUNES)  # no lanza el error
    assert len(correo_configurado) == 1
    assert "El reporte del día no salió por correo" in caplog.text
    assert "jefe@x.co" not in caplog.text
