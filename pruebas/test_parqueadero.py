"""El parqueadero con la base de datos: entradas, salidas, caja y mensualidades.

Los diez casos que fallaron en el sistema de parqueadero que se revisó están aquí, con el
nombre de lo que debe pasar.
"""

import threading
from collections.abc import Callable, Iterator
from dataclasses import replace
from datetime import date, timedelta
from typing import Any

import pytest
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from app.errores import ReglaDelNegocio
from app.parqueadero import casos_de_uso as casos
from app.parqueadero import consultas
from app.parqueadero.errores import (
    CajaYaAbierta,
    MensualidadInvalida,
    NoEstaAdentro,
    PlacaInvalida,
    SinCajaAbierta,
    TarifaInvalida,
    ValorInvalido,
    YaEstaAdentro,
)
from app.parqueadero.estados import EstadoParqueo, TipoParqueo
from app.parqueadero.tablas import PagoMensualidad, Parqueo
from app.parqueadero.tarifas import guardar_ajustes, leer_ajustes
from app.turnos.estados import MedioPago
from pruebas.fabricas import LUNES_8AM, debe_rechazar, minutos

CARRO, MOTO = TipoParqueo.CARRO, TipoParqueo.MOTO
EFECTIVO, NEQUI = MedioPago.EFECTIVO, MedioPago.NEQUI
HOY = date(2026, 10, 26)  # el día de LUNES_8AM en Bogotá


def entra(sesion: Session, placa: str = "ABC123", tipo: TipoParqueo = CARRO, **datos) -> Parqueo:
    return casos.registrar_entrada(sesion, placa=placa, tipo=tipo, ahora=LUNES_8AM, **datos)


def sale(sesion: Session, parqueo: Parqueo, despues: int, **datos) -> Parqueo:
    datos.setdefault("medio_pago", EFECTIVO)
    return casos.cobrar_salida(sesion, parqueo.id, ahora=LUNES_8AM + minutos(despues), **datos)


def con_caja(sesion: Session, base: int = 50000):
    return casos.abrir_caja(sesion, base=base, ahora=LUNES_8AM)


def mensualista(sesion: Session, placa: str = "MEN123", valor: int = 120000):
    return casos.crear_mensualidad(
        sesion,
        placa=placa,
        tipo=CARRO,
        titular="ana ruiz",
        telefono="322 755 6850",
        valor_mensual=valor,
        autorizo_datos=True,
        ahora=LUNES_8AM,
    )


# --- Lo que cobra ------------------------------------------------------------------------


def test_un_carro_dos_horas_paga_dos_horas(sesion: Session):
    con_caja(sesion)
    parqueo = sale(sesion, entra(sesion, "abc-123"), despues=120)

    assert parqueo.placa == "ABC123"
    assert (parqueo.horas_cobradas, parqueo.valor_cobrado) == (2, 6000)
    assert parqueo.estado is EstadoParqueo.SALIO


@pytest.mark.parametrize(("despues", "valor"), [(75, 1500), (76, 3000), (24 * 60, 36000)])
def test_una_moto_paga_su_tarifa_con_gracia(sesion: Session, despues: int, valor: int):
    con_caja(sesion)
    parqueo = sale(sesion, entra(sesion, "ABC12D", MOTO), despues=despues)

    assert parqueo.valor_cobrado == valor


def test_si_suben_el_precio_paga_el_de_cuando_entro(sesion: Session):
    con_caja(sesion)
    parqueo = entra(sesion)
    guardar_ajustes(sesion, _con(leer_ajustes(sesion), hora_carro=9000))

    assert sale(sesion, parqueo, despues=60).valor_cobrado == 3000
    assert entra(sesion, "XYZ789").valor_hora == 9000  # el que entra ahora, con el nuevo


def test_el_billete_grande_no_infla_la_caja(sesion: Session):
    caja = con_caja(sesion, base=50000)
    parqueo = sale(sesion, entra(sesion), despues=120, recibido=50000)

    assert (parqueo.valor_cobrado, parqueo.recibido) == (6000, 50000)
    cuadre = casos.cuadre_de_caja(sesion, caja)
    assert cuadre.por_medio(EFECTIVO) == 6000
    assert cuadre.efectivo_esperado == 56000  # la base + lo cobrado, no + el billete


def test_por_nequi_no_hay_vuelto(sesion: Session):
    con_caja(sesion)
    parqueo = sale(sesion, entra(sesion), despues=60, medio_pago=NEQUI, recibido=50000)

    assert parqueo.recibido is None


def test_un_segundo_cobro_del_mismo_vehiculo_se_rechaza(sesion: Session):
    con_caja(sesion)
    parqueo = sale(sesion, entra(sesion), despues=60)

    with pytest.raises(NoEstaAdentro, match="ya salió"):
        sale(sesion, parqueo, despues=61)
    assert (
        sesion.scalars(select(Parqueo).where(Parqueo.placa == "ABC123")).one().valor_cobrado == 3000
    )


def test_sin_caja_abierta_no_se_cobra(sesion: Session):
    parqueo = entra(sesion)

    with pytest.raises(SinCajaAbierta):
        sale(sesion, parqueo, despues=60)


def test_cobrar_distinto_a_la_tarifa_queda_con_motivo(sesion: Session):
    con_caja(sesion)
    parqueo = entra(sesion)

    with pytest.raises(ValorInvalido, match="escribe el motivo"):
        sale(sesion, parqueo, despues=600, valor_cobrado=15000)
    cobrado = sale(sesion, parqueo, despues=600, valor_cobrado=15000, motivo="Noche completa")
    assert (cobrado.valor_calculado, cobrado.valor_cobrado) == (30000, 15000)
    assert cobrado.motivo_ajuste == "Noche completa"


def test_cobrar_pide_el_medio_de_pago(sesion: Session):
    con_caja(sesion)

    with pytest.raises(ValorInvalido, match="medio de pago"):
        sale(sesion, entra(sesion), despues=60, medio_pago=None)


# --- Entradas ---------------------------------------------------------------------------


def test_una_placa_no_entra_dos_veces(sesion: Session):
    entra(sesion)

    with pytest.raises(YaEstaAdentro, match="ticket"):
        entra(sesion, "abc 123")


def test_una_placa_que_no_es_placa_no_entra(sesion: Session):
    with pytest.raises(PlacaInvalida):
        entra(sesion, "123")


def test_anular_una_entrada_pide_motivo_y_libera_la_placa(sesion: Session):
    parqueo = entra(sesion, observaciones="  deja casco  ")
    assert parqueo.observaciones == "deja casco"

    with pytest.raises(ValorInvalido):
        casos.anular_entrada(sesion, parqueo.id, motivo=" ", ahora=LUNES_8AM)
    anulado = casos.anular_entrada(sesion, parqueo.id, motivo="Placa mal escrita", ahora=LUNES_8AM)
    assert anulado.estado is EstadoParqueo.ANULADO
    assert entra(sesion).estado is EstadoParqueo.ADENTRO  # ya puede entrar de nuevo


def test_buscar_por_placa_o_por_ticket(sesion: Session):
    parqueo = entra(sesion)

    assert casos.buscar_adentro(sesion, "abc-123").id == parqueo.id
    assert casos.buscar_adentro(sesion, str(parqueo.id)).id == parqueo.id
    assert casos.buscar_adentro(sesion, "ZZZ999") is None


# --- Mensualidades ----------------------------------------------------------------------


def test_el_pago_de_la_mensualidad_entra_a_la_caja(sesion: Session):
    caja = con_caja(sesion, base=0)
    mensualidad = mensualista(sesion)

    casos.pagar_mensualidad(
        sesion,
        mensualidad.id,
        desde=HOY,
        hasta=date(2026, 11, 24),
        valor=120000,
        medio_pago=EFECTIVO,
        ahora=LUNES_8AM,
    )

    assert casos.cuadre_de_caja(sesion, caja).efectivo_esperado == 120000


def test_el_mensualista_al_dia_sale_sin_pagar_y_sin_caja(sesion: Session):
    con_caja(sesion)
    mensualidad = mensualista(sesion)
    casos.pagar_mensualidad(
        sesion, mensualidad.id, desde=HOY, hasta=date(2026, 11, 24), valor=120000,
        medio_pago=EFECTIVO, ahora=LUNES_8AM,
    )  # fmt: skip
    caja = casos.caja_abierta(sesion)
    casos.cerrar_caja(sesion, caja.id, efectivo_contado=170000, ahora=LUNES_8AM)

    parqueo = sale(sesion, entra(sesion, "MEN123"), despues=300, medio_pago=None)

    assert (parqueo.valor_calculado, parqueo.valor_cobrado) == (0, 0)
    assert parqueo.mensualidad_id == mensualidad.id
    assert parqueo.medio_pago is None


def test_con_la_mensualidad_vencida_paga_por_horas(sesion: Session):
    con_caja(sesion)
    mensualidad = mensualista(sesion)
    casos.pagar_mensualidad(
        sesion, mensualidad.id, desde=date(2026, 9, 26), hasta=date(2026, 10, 25),
        valor=120000, medio_pago=EFECTIVO, ahora=LUNES_8AM,
    )  # fmt: skip

    parqueo = sale(sesion, entra(sesion, "MEN123"), despues=60)

    assert parqueo.valor_cobrado == 3000
    assert parqueo.mensualidad_id is None


def test_el_mismo_mes_no_se_paga_dos_veces(sesion: Session):
    con_caja(sesion)
    mensualidad = mensualista(sesion)

    def pagar(desde: date, hasta: date) -> PagoMensualidad:
        return casos.pagar_mensualidad(
            sesion, mensualidad.id, desde=desde, hasta=hasta, valor=120000,
            medio_pago=EFECTIVO, ahora=LUNES_8AM,
        )  # fmt: skip

    pagar(HOY, date(2026, 11, 24))
    with pytest.raises(MensualidadInvalida, match="ya están pagados"):
        pagar(date(2026, 11, 20), date(2026, 12, 19))
    pagar(date(2026, 11, 25), date(2026, 12, 24))  # el mes siguiente sí


def test_los_pagos_seguidos_alargan_lo_pagado(sesion: Session):
    con_caja(sesion)
    mensualidad = mensualista(sesion)
    for desde, hasta in ((HOY, date(2026, 11, 24)), (date(2026, 11, 25), date(2026, 12, 24))):
        casos.pagar_mensualidad(
            sesion, mensualidad.id, desde=desde, hasta=hasta, valor=120000,
            medio_pago=EFECTIVO, ahora=LUNES_8AM,
        )  # fmt: skip

    assert casos.cubierta_hasta(sesion, mensualidad.id, HOY) == date(2026, 12, 24)
    assert casos.periodo_sugerido(sesion, mensualidad, HOY) == (
        date(2026, 12, 25),
        date(2027, 1, 23),
    )


def test_si_paga_unos_dias_tarde_el_mes_sigue_desde_que_se_vencio(sesion: Session):
    con_caja(sesion)
    mensualidad = mensualista(sesion)
    casos.pagar_mensualidad(
        sesion, mensualidad.id, desde=date(2026, 9, 24), hasta=date(2026, 10, 23),
        valor=120000, medio_pago=EFECTIVO, ahora=LUNES_8AM,
    )  # fmt: skip

    # Hoy es el 26: debe desde el 24, y el mes nuevo empieza ahí, no hoy.
    assert casos.periodo_sugerido(sesion, mensualidad, HOY) == (
        date(2026, 10, 24),
        date(2026, 11, 22),
    )
    fila = consultas.mensualidades(sesion, LUNES_8AM)[0]
    assert (fila.estado, fila.debe_desde, fila.dias_restantes) == (
        "vencida",
        date(2026, 10, 24),
        -3,
    )
    assert fila.por_cobrar


def test_los_estados_de_las_mensualidades(sesion: Session):
    con_caja(sesion)

    def con_pago_hasta(placa: str, hasta: date | None):
        mensualidad = mensualista(sesion, placa)
        if hasta is not None:
            casos.pagar_mensualidad(
                sesion, mensualidad.id, desde=hasta - timedelta(days=29), hasta=hasta,
                valor=120000, medio_pago=EFECTIVO, ahora=LUNES_8AM,
            )  # fmt: skip

    con_pago_hasta("AAA111", date(2026, 11, 20))  # al día
    con_pago_hasta("BBB222", date(2026, 10, 28))  # vence en 2 días
    con_pago_hasta("CCC333", date(2026, 10, 25))  # se le venció ayer
    con_pago_hasta("DDD444", None)  # nunca ha pagado

    filas = consultas.mensualidades(sesion, LUNES_8AM)

    # Primero lo que hay que cobrar.
    assert [(f.mensualidad.placa, f.estado) for f in filas] == [
        ("CCC333", "vencida"),
        ("DDD444", "sin_pagos"),
        ("BBB222", "vence_pronto"),
        ("AAA111", "al_dia"),
    ]
    assert [f.por_cobrar for f in filas] == [True, True, False, False]


def test_si_la_mensualidad_vencio_el_siguiente_periodo_empieza_hoy(sesion: Session):
    con_caja(sesion)
    mensualidad = mensualista(sesion)
    casos.pagar_mensualidad(
        sesion, mensualidad.id, desde=date(2026, 8, 1), hasta=date(2026, 8, 30), valor=120000,
        medio_pago=EFECTIVO, ahora=LUNES_8AM,
    )  # fmt: skip

    assert casos.periodo_sugerido(sesion, mensualidad, HOY) == (HOY, date(2026, 11, 24))


def test_una_mensualidad_pide_autorizacion_y_una_por_placa(sesion: Session):
    with pytest.raises(MensualidadInvalida, match="Ley 1581"):
        casos.crear_mensualidad(
            sesion, placa="MEN123", tipo=CARRO, titular="Ana", valor_mensual=100000,
            autorizo_datos=False,
        )  # fmt: skip
    mensualidad = mensualista(sesion)
    assert (mensualidad.titular, mensualidad.telefono) == ("Ana Ruiz", "573227556850")
    with pytest.raises(MensualidadInvalida, match="ya tiene una mensualidad activa"):
        mensualista(sesion)

    casos.dar_de_baja(sesion, mensualidad.id)
    assert mensualista(sesion, valor=90000).valor_mensual == 90000


def test_el_mensualista_que_ya_traia_dias_pagados(sesion: Session):
    # Pagó con el dueño anterior hasta el 3 de noviembre: sale sin pagar hasta ese día y esa
    # plata no aparece en ninguna caja.
    mensualidad = casos.crear_mensualidad(
        sesion, placa="MEN123", tipo=CARRO, titular="ana ruiz", valor_mensual=120000,
        autorizo_datos=True, pagada_hasta=date(2026, 11, 3), ahora=LUNES_8AM,
    )  # fmt: skip

    parqueo = sale(sesion, entra(sesion, "MEN123"), despues=300, medio_pago=None)
    assert (parqueo.valor_cobrado, parqueo.mensualidad_id) == (0, mensualidad.id)

    # El primer cobro empieza el día siguiente, y lo que ya traía no se cobra otra vez.
    assert casos.periodo_sugerido(sesion, mensualidad, HOY) == (
        date(2026, 11, 4),
        date(2026, 12, 3),
    )
    caja = con_caja(sesion, base=0)
    with pytest.raises(MensualidadInvalida, match="ya están pagados"):
        casos.pagar_mensualidad(
            sesion, mensualidad.id, desde=HOY, hasta=date(2026, 11, 24), valor=120000,
            medio_pago=EFECTIVO, ahora=LUNES_8AM,
        )  # fmt: skip
    assert casos.cuadre_de_caja(sesion, caja).total == 0


@pytest.mark.parametrize(
    ("hasta", "error"),
    [(date(2026, 10, 25), "ya pasó"), (date(2027, 12, 1), "máximo un año")],
)
def test_los_dias_que_ya_traia_se_revisan(sesion: Session, hasta: date, error: str):
    with pytest.raises(ReglaDelNegocio, match=error):
        casos.crear_mensualidad(
            sesion, placa="MEN123", tipo=CARRO, titular="Ana", valor_mensual=120000,
            autorizo_datos=True, pagada_hasta=hasta, ahora=LUNES_8AM,
        )  # fmt: skip


def test_dar_de_baja_borrando_los_datos_del_titular(sesion: Session):
    con_caja(sesion)
    mensualidad = mensualista(sesion)
    casos.pagar_mensualidad(
        sesion, mensualidad.id, desde=HOY, hasta=date(2026, 11, 24), valor=120000,
        medio_pago=EFECTIVO, ahora=LUNES_8AM,
    )  # fmt: skip

    casos.dar_de_baja(sesion, mensualidad.id, borrar_datos=True)

    assert (mensualidad.titular, mensualidad.telefono, mensualidad.notas) == (
        casos.TITULAR_BORRADO,
        None,
        None,
    )
    # Las cuentas no cambian: el pago y la placa siguen ahí.
    assert casos.cuadre_de_caja(sesion, casos.caja_abierta(sesion)).total == 120000
    assert mensualidad.placa == "MEN123"


def test_una_mensualidad_dada_de_baja_no_recibe_pagos(sesion: Session):
    con_caja(sesion)
    mensualidad = mensualista(sesion)
    casos.dar_de_baja(sesion, mensualidad.id)

    with pytest.raises(MensualidadInvalida, match="dada de baja"):
        casos.pagar_mensualidad(
            sesion, mensualidad.id, desde=HOY, hasta=date(2026, 11, 24), valor=1,
            medio_pago=EFECTIVO,
        )  # fmt: skip


# --- Caja -------------------------------------------------------------------------------


def test_una_sola_caja_abierta(sesion: Session):
    con_caja(sesion)

    with pytest.raises(CajaYaAbierta):
        con_caja(sesion)


def test_cerrar_la_caja_guarda_lo_esperado_y_la_diferencia(sesion: Session):
    caja = con_caja(sesion, base=50000)
    sale(sesion, entra(sesion), despues=120)  # $6.000 en efectivo
    sale(sesion, entra(sesion, "XYZ789"), despues=60, medio_pago=NEQUI)  # $3.000 por Nequi
    mensualidad = mensualista(sesion)
    casos.pagar_mensualidad(
        sesion, mensualidad.id, desde=HOY, hasta=date(2026, 11, 24), valor=120000,
        medio_pago=NEQUI, ahora=LUNES_8AM,
    )  # fmt: skip

    cerrada = casos.cerrar_caja(
        sesion, caja.id, efectivo_contado=55000, nota="faltó un billete", ahora=LUNES_8AM
    )

    assert cerrada.efectivo_esperado == 56000  # 50.000 de base + 6.000 en efectivo
    assert cerrada.diferencia == -1000
    assert not cerrada.abierta
    assert casos.caja_abierta(sesion) is None
    with pytest.raises(SinCajaAbierta):
        casos.cerrar_caja(sesion, caja.id, efectivo_contado=0)


# --- Ajustes ----------------------------------------------------------------------------


def _con(ajustes, **cambios):
    return replace(ajustes, **cambios)


def test_los_ajustes_iniciales_son_los_de_eduar(sesion: Session):
    ajustes = leer_ajustes(sesion)

    assert (ajustes.hora_carro, ajustes.hora_moto, ajustes.gracia_minutos) == (3000, 1500, 15)
    assert ajustes.fraccion_carro is None


def test_guardar_ajustes_revisa_los_valores(sesion: Session):
    ajustes = leer_ajustes(sesion)
    guardar_ajustes(sesion, _con(ajustes, hora_moto=2000, fraccion_moto=800, nombre=" Parq "))

    guardado = leer_ajustes(sesion)
    assert (guardado.hora_moto, guardado.fraccion_moto, guardado.nombre) == (2000, 800, "Parq")
    with pytest.raises(TarifaInvalida):
        guardar_ajustes(sesion, _con(ajustes, ancho_ticket=70))
    with pytest.raises(TarifaInvalida):
        guardar_ajustes(sesion, _con(ajustes, hora_carro=0))


# --- La base de datos también cuida la plata --------------------------------------------


def test_la_base_no_deja_dos_entradas_abiertas_de_la_misma_placa(sesion: Session):
    entra(sesion)
    with debe_rechazar(sesion, "uq_parqueo_placa_adentro"):
        sesion.add(Parqueo(placa="ABC123", tipo=CARRO, valor_hora=3000, gracia_minutos=15))


def test_la_base_no_deja_un_cobro_sin_caja(sesion: Session):
    parqueo = entra(sesion)
    with debe_rechazar(sesion, "ck_parqueo_cobro_en_caja"):
        parqueo.estado = EstadoParqueo.SALIO
        parqueo.salida_en = LUNES_8AM
        parqueo.valor_calculado = parqueo.valor_cobrado = 3000
        parqueo.medio_pago = EFECTIVO


@pytest.mark.parametrize(
    ("valor", "medio", "en_caja"),
    [(120000, EFECTIVO, False), (120000, None, False), (0, EFECTIVO, True)],
)
def test_la_base_no_deja_un_pago_de_mensualidad_sin_caja_con_plata(
    sesion: Session, valor: int, medio: MedioPago | None, en_caja: bool
):
    # Solo hay dos formas: plata cobrada (con caja y medio) o días que ya traía (sin nada, en 0).
    caja = con_caja(sesion)
    mensualidad = mensualista(sesion)
    with debe_rechazar(sesion, "ck_pago_mensualidad_cobro_en_caja"):
        sesion.add(
            PagoMensualidad(
                mensualidad_id=mensualidad.id, desde=HOY, hasta=HOY, valor=valor,
                medio_pago=medio, caja_id=caja.id if en_caja else None,
            )
        )  # fmt: skip


# --- Al mismo tiempo, como un doble clic ------------------------------------------------


@pytest.fixture
def base(motor: Engine) -> Iterator[Engine]:
    yield motor
    with motor.begin() as conexion:
        conexion.execute(
            text("TRUNCATE parqueo, pago_mensualidad, mensualidad, caja_parqueadero CASCADE")
        )


def al_mismo_tiempo(motor: Engine, *acciones: Callable[[Session], Any]) -> list[Any]:
    barrera = threading.Barrier(len(acciones))
    resultados: list[Any] = [None] * len(acciones)

    def correr(indice: int, accion: Callable[[Session], Any]) -> None:
        try:
            with Session(motor) as sesion, sesion.begin():
                barrera.wait(timeout=10)
                resultados[indice] = accion(sesion)
        except Exception as error:
            resultados[indice] = error

    hilos = [threading.Thread(target=correr, args=par) for par in enumerate(acciones)]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join(timeout=30)
    return resultados


def test_doble_clic_en_cobrar_cobra_una_sola_vez(base: Engine):
    with Session(base) as sesion, sesion.begin():
        con_caja(sesion)
        parqueo_id = entra(sesion).id

    def cobrar(sesion: Session) -> int:
        return casos.cobrar_salida(
            sesion, parqueo_id, medio_pago=EFECTIVO, ahora=LUNES_8AM + timedelta(minutes=60)
        ).valor_cobrado

    resultados = al_mismo_tiempo(base, cobrar, cobrar)

    assert sorted(map(type, resultados), key=str) == sorted([int, NoEstaAdentro], key=str)
    with Session(base) as sesion:
        caja = casos.caja_abierta(sesion)
        assert casos.cuadre_de_caja(sesion, caja).por_medio(EFECTIVO) == 3000


def test_dos_entradas_de_la_misma_placa_a_la_vez_quedan_en_una(base: Engine):
    resultados = al_mismo_tiempo(base, lambda s: entra(s).id, lambda s: entra(s).id)

    assert sorted(map(type, resultados), key=str) == sorted([int, YaEstaAdentro], key=str)


def test_dos_cajas_abiertas_a_la_vez_quedan_en_una(base: Engine):
    resultados = al_mismo_tiempo(base, lambda s: con_caja(s).id, lambda s: con_caja(s).id)

    assert sorted(map(type, resultados), key=str) == sorted([int, CajaYaAbierta], key=str)
