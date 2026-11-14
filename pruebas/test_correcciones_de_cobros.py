"""Corregir un cobro equivocado del parqueadero: otro medio de pago u otro valor, deshacer la
salida del carro que no era y anular un mes cobrado por error.

Lo que tiene que cumplirse siempre:
- se corrige en la caja del cobro: la abierta, o la cerrada hasta 30 días después (eso, con
  la caja ya cerrada, está en test_correcciones_de_cajas_cerradas.py);
- cada corrección queda anotada (cómo estaba, cómo quedó, el motivo y quién);
- la caja, el balance y el reporte cuentan lo que quedó, y muestran la corrección;
- un doble clic no corrige dos veces, y una caja que se cierra al mismo tiempo que se cobra
  queda con el cobro adentro de su cuadre (o el cobro no entra), nunca a medias.
"""

import re
import threading
import time
from collections.abc import Callable, Iterator
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from app.parqueadero import casos_de_uso as casos
from app.parqueadero import consultas, reporte
from app.parqueadero.balance import balance, periodo
from app.parqueadero.errores import (
    CobroNoCorregible,
    SinCajaAbierta,
    ValorInvalido,
    YaEstaAdentro,
)
from app.parqueadero.estados import EstadoParqueo, TipoCorreccion
from app.parqueadero.tablas import CajaParqueadero, CorreccionCobro, PagoMensualidad, Parqueo
from pruebas.fabricas import LUNES_8AM, debe_rechazar, minutos
from pruebas.test_parqueadero import CARRO, EFECTIVO, HOY, NEQUI, con_caja, entra, mensualista, sale

DESPUES = LUNES_8AM + minutos(70)  # cuando se corrige, en las pruebas


def correcciones(sesion: Session) -> list[CorreccionCobro]:
    return list(sesion.scalars(select(CorreccionCobro).order_by(CorreccionCobro.id)))


def cobrado(sesion: Session, placa: str = "ABC123", despues: int = 60, **datos) -> Parqueo:
    """Un carro que entró a las 8:00 y salió `despues` minutos más tarde, ya cobrado."""
    return sale(sesion, entra(sesion, placa), despues=despues, **datos)


def mes_cobrado(sesion: Session, **datos) -> PagoMensualidad:
    datos.setdefault("medio_pago", NEQUI)
    return casos.pagar_mensualidad(
        sesion,
        mensualista(sesion).id,
        desde=HOY,
        hasta=HOY + timedelta(days=29),
        valor=120000,
        ahora=LUNES_8AM,
        **datos,
    )


def cuadre(sesion: Session):
    return casos.cuadre_de_caja(sesion, casos.caja_abierta(sesion))


# --- Corregir el valor o el medio de pago de una salida ------------------------------------


def test_era_nequi_y_no_efectivo(sesion: Session):
    con_caja(sesion)
    parqueo = cobrado(sesion, recibido=10000)  # 1 h: $3.000, pagó con $10.000

    correccion = casos.corregir_salida(
        sesion,
        parqueo.id,
        valor_cobrado=3000,
        medio_pago=NEQUI,
        motivo="Pagó por Nequi",
        ahora=DESPUES,
    )

    assert (parqueo.valor_cobrado, parqueo.medio_pago, parqueo.recibido) == (3000, NEQUI, None)
    assert (cuadre(sesion).por_medio(EFECTIVO), cuadre(sesion).por_medio(NEQUI)) == (0, 3000)
    assert cuadre(sesion).efectivo_esperado == 50000
    assert correccion.tipo is TipoCorreccion.CORREGIDO
    assert (correccion.valor_antes, correccion.medio_antes) == (3000, EFECTIVO)
    assert (correccion.valor_despues, correccion.medio_despues) == (3000, NEQUI)
    assert correccion.detalle == f"Salida de las 9:00 a.m. · ticket {parqueo.id}"
    assert correccion.placa == "ABC123" and correccion.motivo == "Pagó por Nequi"


def test_se_escribio_30000_y_eran_3000(sesion: Session):
    con_caja(sesion)
    parqueo = cobrado(sesion, valor_cobrado=30000, motivo="Noche")

    casos.corregir_salida(
        sesion, parqueo.id, valor_cobrado=3000, medio_pago=EFECTIVO, motivo="Se escribió mal"
    )

    # Quedó en la tarifa: ya no es un cobro distinto a la tarifa.
    assert (parqueo.valor_cobrado, parqueo.motivo_ajuste) == (3000, None)
    assert cuadre(sesion).total == 3000


def test_un_valor_distinto_a_la_tarifa_queda_con_el_motivo_de_la_correccion(sesion: Session):
    con_caja(sesion)
    parqueo = cobrado(sesion)  # tarifa $3.000

    casos.corregir_salida(
        sesion, parqueo.id, valor_cobrado=2000, medio_pago=EFECTIVO, motivo="Cliente frecuente"
    )

    assert (parqueo.valor_calculado, parqueo.valor_cobrado) == (3000, 2000)
    assert parqueo.motivo_ajuste == "Cliente frecuente"


def test_corregir_a_cero_deja_la_salida_sin_cobro(sesion: Session):
    con_caja(sesion)
    parqueo = cobrado(sesion)

    casos.corregir_salida(
        sesion, parqueo.id, valor_cobrado=0, medio_pago=EFECTIVO, motivo="Era mensualista"
    )

    assert (parqueo.estado, parqueo.valor_cobrado, parqueo.medio_pago) == (
        EstadoParqueo.SALIO,
        0,
        None,
    )
    assert cuadre(sesion).total == 0


@pytest.mark.parametrize(
    ("valor", "medio", "motivo", "error"),
    [
        (3000, EFECTIVO, "Por si acaso", "No hay nada que cambiar: ya está en $3.000 en efectivo"),
        (3000, NEQUI, "   ", "Escribe por qué se corrige"),
        (3000, None, "Otro medio", "Elige el medio de pago"),
        (-1, EFECTIVO, "Negativo", "no puede ser negativo"),
    ],
)
def test_lo_que_no_se_deja_corregir(sesion: Session, valor, medio, motivo, error):
    con_caja(sesion)
    parqueo = cobrado(sesion)

    with pytest.raises(ValorInvalido, match=re.escape(error)):
        casos.corregir_salida(
            sesion, parqueo.id, valor_cobrado=valor, medio_pago=medio, motivo=motivo
        )
    assert correcciones(sesion) == []


# --- Deshacer la salida ------------------------------------------------------------------


def test_deshacer_la_salida_lo_deja_adentro_como_estaba(sesion: Session):
    con_caja(sesion)
    parqueo = cobrado(sesion, despues=120)  # $6.000 en efectivo
    ticket, entro, valor_hora = parqueo.id, parqueo.entrada_en, parqueo.valor_hora

    correccion = casos.deshacer_salida(sesion, parqueo.id, motivo="Era otro carro", ahora=DESPUES)

    assert parqueo.estado is EstadoParqueo.ADENTRO
    assert (parqueo.id, parqueo.entrada_en, parqueo.valor_hora) == (ticket, entro, valor_hora)
    for campo in ("salida_en", "valor_cobrado", "medio_pago", "caja_id", "minutos"):
        assert getattr(parqueo, campo) is None, campo
    assert cuadre(sesion).total == 0
    assert correccion.tipo is TipoCorreccion.SALIDA_DESHECHA
    assert (correccion.valor_antes, correccion.medio_antes) == (6000, EFECTIVO)
    assert (correccion.valor_despues, correccion.medio_despues) == (0, None)
    # Cuando por fin sale, se cobra desde su hora de entrada, con su ticket.
    otra_vez = sale(sesion, parqueo, despues=180)
    assert (otra_vez.id, otra_vez.valor_cobrado) == (ticket, 9000)


def test_no_se_deshace_si_la_placa_ya_volvio_a_entrar(sesion: Session):
    con_caja(sesion)
    parqueo = cobrado(sesion)
    casos.registrar_entrada(sesion, placa="ABC123", tipo=CARRO, ahora=DESPUES)

    with pytest.raises(YaEstaAdentro, match="ABC123 ya volvió a entrar"):
        casos.deshacer_salida(sesion, parqueo.id, motivo="Era otro carro")
    assert parqueo.estado is EstadoParqueo.SALIO and correcciones(sesion) == []


def test_un_carro_que_esta_adentro_no_tiene_salida_que_deshacer(sesion: Session):
    con_caja(sesion)
    parqueo = entra(sesion)

    with pytest.raises(CobroNoCorregible, match=r"\(ticket \d+\) no tiene una salida .* adentro"):
        casos.deshacer_salida(sesion, parqueo.id, motivo="Error")


# --- Pagos de mensualidad ----------------------------------------------------------------


def test_corregir_el_medio_de_pago_de_un_mes(sesion: Session):
    con_caja(sesion, base=0)
    pago = mes_cobrado(sesion)

    correccion = casos.corregir_pago(
        sesion, pago.id, valor=120000, medio_pago=EFECTIVO, motivo="Pagó en efectivo"
    )

    assert (pago.valor, pago.medio_pago) == (120000, EFECTIVO)
    assert cuadre(sesion).efectivo_esperado == 120000
    assert correccion.detalle == "Mensualidad del 26 de octubre de 2026 al 24 de noviembre de 2026"
    assert (correccion.mensualidad_id, correccion.pago_id) == (pago.mensualidad_id, pago.id)


def test_un_pago_no_se_corrige_a_cero(sesion: Session):
    con_caja(sesion, base=0)
    pago = mes_cobrado(sesion)

    with pytest.raises(ValorInvalido, match="Para quitar el pago, anúlalo"):
        casos.corregir_pago(sesion, pago.id, valor=0, medio_pago=NEQUI, motivo="Nada")


def test_anular_un_mes_cobrado_dos_veces(sesion: Session):
    con_caja(sesion, base=0)
    pago = mes_cobrado(sesion)
    mensualidad_id, pago_id = pago.mensualidad_id, pago.id

    correccion = casos.anular_pago(sesion, pago.id, motivo="Se cobró dos veces", ahora=DESPUES)

    assert sesion.get(PagoMensualidad, pago_id) is None
    assert cuadre(sesion).total == 0
    assert casos.cubierta_hasta(sesion, mensualidad_id, HOY) is None  # esos días se deben
    (fila,) = [
        f for f in consultas.mensualidades(sesion, DESPUES) if f.mensualidad.placa == "MEN123"
    ]
    assert fila.por_cobrar
    assert correccion.tipo is TipoCorreccion.PAGO_ANULADO
    assert (correccion.valor_antes, correccion.medio_antes, correccion.pago_id) == (
        120000,
        NEQUI,
        pago_id,
    )
    with pytest.raises(CobroNoCorregible, match="Ese pago ya no existe"):
        casos.anular_pago(sesion, pago_id, motivo="Otra vez")


def test_los_dias_que_ya_traia_no_son_un_cobro(sesion: Session):
    con_caja(sesion)
    traido = casos.crear_mensualidad(
        sesion,
        placa="TRA456",
        tipo=CARRO,
        titular="Luis Gómez",
        valor_mensual=100000,
        autorizo_datos=True,
        pagada_hasta=HOY + timedelta(days=10),
        ahora=LUNES_8AM,
    )
    pago = sesion.scalars(
        select(PagoMensualidad).where(PagoMensualidad.mensualidad_id == traido.id)
    ).one()

    with pytest.raises(CobroNoCorregible, match="no entró a ninguna caja"):
        casos.anular_pago(sesion, pago.id, motivo="Error")


# --- En la caja del cobro, hasta 30 días después de cerrada ----------------------------------


def test_pasados_30_dias_del_cierre_no_se_corrige_nada(sesion: Session):
    caja = con_caja(sesion, base=0)
    parqueo = cobrado(sesion)
    pago = mes_cobrado(sesion)
    casos.cerrar_caja(sesion, caja.id, efectivo_contado=3000, ahora=DESPUES)
    tarde = DESPUES + timedelta(days=30, minutes=1)

    for corregir in (
        lambda: casos.corregir_salida(
            sesion, parqueo.id, valor_cobrado=1000, medio_pago=EFECTIVO, motivo="Tarde", ahora=tarde
        ),
        lambda: casos.deshacer_salida(sesion, parqueo.id, motivo="Tarde", ahora=tarde),
        lambda: casos.corregir_pago(
            sesion, pago.id, valor=100000, medio_pago=NEQUI, motivo="Tarde", ahora=tarde
        ),
        lambda: casos.anular_pago(sesion, pago.id, motivo="Tarde", ahora=tarde),
    ):
        with pytest.raises(CobroNoCorregible, match="se cerró hace más de 30 días"):
            corregir()
    assert correcciones(sesion) == []
    assert (parqueo.valor_cobrado, sesion.get(PagoMensualidad, pago.id).valor) == (3000, 120000)


def test_lo_de_una_caja_anterior_se_corrige_en_esa_caja_no_en_la_de_hoy(sesion: Session):
    ayer = con_caja(sesion, base=0)
    parqueo = cobrado(sesion)
    casos.cerrar_caja(sesion, ayer.id, efectivo_contado=3000, ahora=DESPUES)
    hoy = casos.abrir_caja(sesion, base=0, ahora=DESPUES + timedelta(hours=12))

    correccion = casos.deshacer_salida(
        sesion, parqueo.id, motivo="Era otro", ahora=DESPUES + timedelta(hours=13)
    )

    assert (correccion.caja_id, correccion.despues_del_cierre) == (ayer.id, True)
    assert ayer.diferencia == 3000  # se contaron $3.000 de un cobro que no debió pasar
    assert casos.cuadre_de_caja(sesion, hoy).efectivo_esperado == 0  # la de hoy, igual


# --- Lo que se ve después ---------------------------------------------------------------


def test_la_caja_el_balance_y_el_reporte_muestran_cada_correccion(sesion: Session):
    caja = con_caja(sesion)
    nequi = cobrado(sesion, "NEQ111")
    otro = cobrado(sesion, "OTR222", despues=120)
    pago = mes_cobrado(sesion)
    casos.corregir_salida(
        sesion, nequi.id, valor_cobrado=3000, medio_pago=NEQUI, motivo="Pagó por Nequi",
        ahora=LUNES_8AM + minutos(130),
    )  # fmt: skip
    casos.deshacer_salida(sesion, otro.id, motivo="Era otro carro", ahora=LUNES_8AM + minutos(140))
    casos.anular_pago(sesion, pago.id, motivo="Se cobró dos veces", ahora=LUNES_8AM + minutos(150))
    casos.cerrar_caja(sesion, caja.id, efectivo_contado=50000, ahora=LUNES_8AM + minutos(600))

    resumen = consultas.resumen_de_caja(sesion, caja)
    assert caja.efectivo_esperado == 50000 and caja.diferencia == 0  # todo salió del efectivo
    assert [f.correccion.placa for f in resumen.correcciones] == ["NEQ111", "OTR222", "MEN123"]
    assert [f.que_paso for f in resumen.correcciones] == [
        "Se corrigió el cobro",
        "Se deshizo la salida",
        "Se anuló el pago",
    ]
    assert [f.cambio_en_efectivo for f in resumen.correcciones] == [-3000, -6000, 0]
    assert resumen.correcciones[0].despues == "$3.000 por Nequi"
    assert resumen.correcciones[1].despues is None

    b = balance(sesion, periodo("dia", HOY))
    assert len(b.correcciones) == 3 and b.total == 3000

    texto = reporte.texto(reporte.armar(sesion, HOY, LUNES_8AM + minutos(610)), negrilla=False)
    lineas = texto.splitlines()
    assert "Correcciones: 3" in lineas
    assert (
        "- NEQ111: se corrigió el cobro ($3.000 en efectivo → $3.000 por Nequi). "
        "Motivo: Pagó por Nequi. Por —." in lineas
    )
    assert (
        "- OTR222: se deshizo la salida ($6.000 en efectivo). Motivo: Era otro carro. Por —."
        in lineas
    )
    assert (
        "- MEN123: se anuló el pago ($120.000 por Nequi). Motivo: Se cobró dos veces. Por —."
        in lineas
    )


def test_la_base_no_guarda_una_correccion_sin_motivo_ni_de_dos_cobros(sesion: Session):
    caja = con_caja(sesion)
    parqueo = cobrado(sesion)
    pago = mes_cobrado(sesion)

    def correccion(**cambios) -> CorreccionCobro:
        datos = {
            "tipo": TipoCorreccion.CORREGIDO,
            "caja_id": caja.id,
            "parqueo_id": parqueo.id,
            "placa": "ABC123",
            "valor_antes": 3000,
            "valor_despues": 2000,
            "detalle": "Salida",
            "motivo": "Un motivo",
        }
        return CorreccionCobro(**(datos | cambios))

    with debe_rechazar(sesion, "ck_correccion_cobro_con_motivo"):
        sesion.add(correccion(motivo="  "))
    with debe_rechazar(sesion, "ck_correccion_cobro_de_un_cobro"):
        sesion.add(correccion(mensualidad_id=pago.mensualidad_id))
    with debe_rechazar(sesion, "ck_correccion_cobro_de_un_cobro"):
        sesion.add(correccion(parqueo_id=None))
    with debe_rechazar(sesion, "ck_correccion_cobro_valores_no_negativos"):
        sesion.add(correccion(valor_despues=-1))


# --- Al mismo tiempo ---------------------------------------------------------------------


@pytest.fixture
def base(motor: Engine) -> Iterator[Engine]:
    yield motor
    with motor.begin() as conexion:
        conexion.execute(
            text(
                "TRUNCATE correccion_cobro, parqueo, pago_mensualidad, mensualidad, "
                "caja_parqueadero CASCADE"
            )
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


def test_doble_clic_en_deshacer_la_salida_la_deshace_una_vez(base: Engine):
    with Session(base) as sesion, sesion.begin():
        con_caja(sesion)
        parqueo_id = cobrado(sesion).id

    def deshacer(sesion: Session) -> int:
        return casos.deshacer_salida(sesion, parqueo_id, motivo="Era otro carro").id

    resultados = al_mismo_tiempo(base, deshacer, deshacer)

    assert sorted(map(type, resultados), key=str) == sorted([int, CobroNoCorregible], key=str)
    with Session(base) as sesion:
        assert len(correcciones(sesion)) == 1


def test_doble_clic_en_anular_el_pago_lo_anula_una_vez(base: Engine):
    with Session(base) as sesion, sesion.begin():
        con_caja(sesion, base=0)
        pago_id = mes_cobrado(sesion).id

    def anular(sesion: Session) -> int:
        return casos.anular_pago(sesion, pago_id, motivo="Dos veces").id

    resultados = al_mismo_tiempo(base, anular, anular)

    assert sorted(map(type, resultados), key=str) == sorted([int, CobroNoCorregible], key=str)
    with Session(base) as sesion:
        assert len(correcciones(sesion)) == 1


def esperar_a_que_alguien_espere(motor: Engine) -> None:
    """Hasta que otra conexión quede esperando un candado (o 10 segundos)."""
    fin = time.monotonic() + 10
    with motor.connect() as conexion:
        while time.monotonic() < fin:
            esperando = conexion.execute(
                text(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE wait_event_type = 'Lock' AND datname = current_database()"
                )
            ).scalar()
            if esperando:
                return
            time.sleep(0.02)
    raise AssertionError("Nadie quedó esperando el candado.")


def en_otro_hilo(motor: Engine, accion: Callable[[Session], Any]) -> tuple[threading.Thread, list]:
    resultado: list[Any] = []

    def correr() -> None:
        try:
            with Session(motor) as sesion, sesion.begin():
                resultado.append(accion(sesion))
        except Exception as error:
            resultado.append(error)

    hilo = threading.Thread(target=correr)
    hilo.start()
    return hilo, resultado


def test_cerrar_la_caja_mientras_se_cobra_espera_y_cuenta_ese_cobro(base: Engine):
    """El cierre espera a que termine el cobro que se está guardando y lo cuenta."""
    with Session(base) as sesion, sesion.begin():
        caja_id = con_caja(sesion, base=0).id
        parqueo_id = entra(sesion).id

    with Session(base) as cobra:
        casos.cobrar_salida(cobra, parqueo_id, medio_pago=EFECTIVO, ahora=LUNES_8AM + minutos(60))
        hilo, cierre = en_otro_hilo(
            base, lambda s: casos.cerrar_caja(s, caja_id, efectivo_contado=3000).diferencia
        )
        esperar_a_que_alguien_espere(base)  # el cierre espera a que termine el cobro
        cobra.commit()
    hilo.join(timeout=10)

    assert cierre == [0]  # contó los $3.000: cuadró
    with Session(base) as sesion:
        caja = sesion.get(CajaParqueadero, caja_id)
        assert caja.efectivo_esperado == casos.cuadre_de_caja(sesion, caja).efectivo_esperado


def test_cobrar_mientras_se_cierra_la_caja_espera_y_no_cobra(base: Engine):
    """Antes, el cobro esperaba a que terminara el cierre y después se guardaba en la caja ya
    cerrada: quedaba por fuera de su cuadre y el cierre ya no cuadraba con sus cobros."""
    with Session(base) as sesion, sesion.begin():
        caja_id = con_caja(sesion, base=0).id
        parqueo_id = entra(sesion).id

    with Session(base) as cierra:
        casos.cerrar_caja(cierra, caja_id, efectivo_contado=0)
        hilo, cobro = en_otro_hilo(
            base,
            lambda s: casos.cobrar_salida(
                s, parqueo_id, medio_pago=EFECTIVO, ahora=LUNES_8AM + minutos(60)
            ),
        )
        esperar_a_que_alguien_espere(base)  # el cobro espera a que termine el cierre
        cierra.commit()
    hilo.join(timeout=10)

    assert len(cobro) == 1 and isinstance(cobro[0], SinCajaAbierta)
    with Session(base) as sesion:
        assert sesion.get(Parqueo, parqueo_id).estado is EstadoParqueo.ADENTRO


def test_corregir_mientras_se_cierra_la_caja_espera_y_corrige_despues_del_cierre(base: Engine):
    """La corrección espera a que termine el cierre y después se hace en la caja ya cerrada:
    queda marcada como de después del cierre y lo que debía haber en el cajón se pone al día
    (sin el candado, el cierre podía contar el cobro que ya no existía, o al revés)."""
    with Session(base) as sesion, sesion.begin():
        caja_id = con_caja(sesion, base=0).id
        parqueo_id = cobrado(sesion).id

    with Session(base) as cierra:
        casos.cerrar_caja(cierra, caja_id, efectivo_contado=3000)
        hilo, correccion = en_otro_hilo(
            base, lambda s: casos.deshacer_salida(s, parqueo_id, motivo="Era otro").id
        )
        esperar_a_que_alguien_espere(base)
        cierra.commit()
    hilo.join(timeout=10)

    assert len(correccion) == 1 and isinstance(correccion[0], int), correccion
    with Session(base) as sesion:
        assert sesion.get(Parqueo, parqueo_id).estado is EstadoParqueo.ADENTRO
        (hecha,) = correcciones(sesion)
        assert hecha.despues_del_cierre
        caja = sesion.get(CajaParqueadero, caja_id)
        assert caja.efectivo_esperado == casos.cuadre_de_caja(sesion, caja).efectivo_esperado == 0
        assert caja.diferencia == 3000


def test_una_correccion_nunca_toca_otra_caja(sesion: Session):
    """La corrección queda en la caja del cobro, que es la abierta."""
    caja = con_caja(sesion)
    parqueo = cobrado(sesion)

    correccion = casos.deshacer_salida(sesion, parqueo.id, motivo="Era otro carro")

    assert correccion.caja_id == caja.id
