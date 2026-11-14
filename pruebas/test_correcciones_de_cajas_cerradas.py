"""Corregir una caja que ya se cerró: un cobro, la base o el efectivo que se contó.

Lo que tiene que cumplirse siempre:
- se corrige hasta 30 días después del cierre; después, ya no;
- la caja queda con los números corregidos: lo que debía haber en el cajón es siempre lo que
  dicen sus cobros (más la base), y la diferencia cambia con la corrección;
- cada corrección queda anotada como hecha después del cierre, y de ahí sale cómo estaba la
  caja al cerrar;
- el reporte del día de la caja y el del día en que se corrigió lo cuentan;
- dos correcciones al mismo tiempo en la misma caja no se pisan la cuenta.
"""

from collections.abc import Iterator
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from app.parqueadero import casos_de_uso as casos
from app.parqueadero import consultas, reporte
from app.parqueadero.balance import balance, periodo
from app.parqueadero.errores import CajaNoCorregible, ValorInvalido
from app.parqueadero.estados import DatoDeCaja, EstadoParqueo
from app.parqueadero.tablas import Caja, CorreccionDeCaja, Parqueo
from pruebas.fabricas import LUNES_8AM, debe_rechazar, minutos
from pruebas.navegador import HTMX, aviso, error, eventos
from pruebas.test_correcciones_de_cobros import (
    cobrado,
    en_otro_hilo,
    esperar_a_que_alguien_espere,
    mes_cobrado,
)
from pruebas.test_parqueadero import EFECTIVO, HOY, NEQUI, con_caja

CIERRE = LUNES_8AM + minutos(600)  # las 6:00 p.m. del lunes
MARTES = CIERRE + timedelta(hours=16)  # el martes a las 10:00 a.m., cuando se corrige


def esperado_por_los_cobros(sesion: Session, caja: Caja) -> int:
    return casos.cuadre_de_caja(sesion, caja).efectivo_esperado


def cerrada(sesion: Session, *, base: int = 0, contado: int) -> tuple[Caja, Parqueo]:
    """Una caja con un carro que pagó $3.000 en efectivo, cerrada el lunes a las 6:00 p.m."""
    caja = con_caja(sesion, base=base)
    parqueo = cobrado(sesion)
    casos.cerrar_caja(sesion, caja.id, efectivo_contado=contado, ahora=CIERRE)
    return caja, parqueo


# --- Un cobro de una caja cerrada -----------------------------------------------------------


def test_un_cobro_de_una_caja_cerrada_se_corrige_y_la_diferencia_se_pone_al_dia(sesion: Session):
    """Faltaron $3.000 al cerrar porque el cobro se anotó en efectivo y fue por Nequi."""
    caja, parqueo = cerrada(sesion, contado=0)
    assert caja.diferencia == -3000

    correccion = casos.corregir_salida(
        sesion, parqueo.id, valor_cobrado=3000, medio_pago=NEQUI, motivo="Era Nequi", ahora=MARTES
    )

    assert correccion.despues_del_cierre and correccion.caja_id == caja.id
    assert caja.efectivo_esperado == esperado_por_los_cobros(sesion, caja) == 0
    assert caja.diferencia == 0
    resumen = consultas.resumen_de_caja(sesion, caja)
    assert resumen.corregida_despues_del_cierre
    assert resumen.diferencia_al_cerrar == -3000  # cómo quedó al cerrar no se pierde


def test_lo_que_debia_haber_siempre_es_lo_que_dicen_los_cobros(sesion: Session):
    """Después de cada corrección, lo guardado en la caja es lo mismo que sale de sus cobros."""
    caja = con_caja(sesion, base=10000)
    uno = cobrado(sesion, "UNO111")
    dos = cobrado(sesion, "DOS222", despues=120)
    pago = mes_cobrado(sesion, medio_pago=EFECTIVO)
    casos.cerrar_caja(sesion, caja.id, efectivo_contado=139000, ahora=CIERRE)
    assert caja.diferencia == 0

    for corregir in (
        lambda: casos.corregir_salida(
            sesion, uno.id, valor_cobrado=2000, medio_pago=EFECTIVO, motivo="Mal", ahora=MARTES
        ),
        lambda: casos.corregir_pago(
            sesion, pago.id, valor=120000, medio_pago=NEQUI, motivo="Nequi", ahora=MARTES
        ),
        lambda: casos.deshacer_salida(sesion, dos.id, motivo="Era otro", ahora=MARTES),
        lambda: casos.corregir_base(sesion, caja.id, base=5000, motivo="Base", ahora=MARTES),
        lambda: casos.anular_pago(sesion, pago.id, motivo="Dos veces", ahora=MARTES),
    ):
        corregir()
        assert caja.efectivo_esperado == esperado_por_los_cobros(sesion, caja)

    assert caja.efectivo_esperado == 5000 + 2000
    resumen = consultas.resumen_de_caja(sesion, caja)
    assert len(resumen.correcciones) == 5
    assert all(fila.despues_del_cierre for fila in resumen.correcciones)
    assert resumen.diferencia_al_cerrar == 0


def test_lo_que_se_corrigio_con_la_caja_abierta_no_queda_como_de_despues(sesion: Session):
    caja = con_caja(sesion, base=0)
    parqueo = cobrado(sesion)
    casos.corregir_salida(
        sesion, parqueo.id, valor_cobrado=3000, medio_pago=NEQUI, motivo="Nequi", ahora=CIERRE
    )
    casos.cerrar_caja(sesion, caja.id, efectivo_contado=0, ahora=CIERRE + minutos(1))

    resumen = consultas.resumen_de_caja(sesion, caja)
    assert not resumen.corregida_despues_del_cierre
    assert resumen.diferencia_al_cerrar == caja.diferencia == 0


# --- El conteo y la base --------------------------------------------------------------------


def test_corregir_el_efectivo_contado(sesion: Session):
    """Se escribió $150.000 y en el cajón había $103.000: la diferencia se pone al día."""
    caja, _ = cerrada(sesion, base=100000, contado=150000)
    assert caja.diferencia == 47000

    correccion = casos.corregir_conteo(
        sesion, caja.id, efectivo_contado=103000, motivo="Se escribió mal", ahora=MARTES
    )

    assert (correccion.dato, correccion.valor_antes, correccion.valor_despues) == (
        DatoDeCaja.CONTEO,
        150000,
        103000,
    )
    assert correccion.despues_del_cierre
    assert (caja.efectivo_contado, caja.diferencia) == (103000, 0)
    resumen = consultas.resumen_de_caja(sesion, caja)
    assert resumen.conteo_corregido and resumen.diferencia_al_cerrar == 47000
    (fila,) = resumen.correcciones
    assert fila.en_una_linea == (
        "Se corrigió el efectivo contado ($150.000 → $103.000), después del cierre. "
        "Motivo: Se escribió mal. Por —."
    )
    assert fila.efecto == ("Contado", -47000)


def test_con_dos_correcciones_del_conteo_al_cerrar_es_el_primero(sesion: Session):
    caja, _ = cerrada(sesion, contado=10000)
    casos.corregir_conteo(sesion, caja.id, efectivo_contado=5000, motivo="Uno", ahora=MARTES)
    casos.corregir_conteo(
        sesion, caja.id, efectivo_contado=3000, motivo="Dos", ahora=MARTES + minutos(5)
    )

    resumen = consultas.resumen_de_caja(sesion, caja)
    assert (caja.diferencia, resumen.diferencia_al_cerrar) == (0, 7000)


def test_el_conteo_de_una_caja_abierta_no_se_corrige(sesion: Session):
    caja = con_caja(sesion)

    with pytest.raises(CajaNoCorregible, match="sigue abierta"):
        casos.corregir_conteo(sesion, caja.id, efectivo_contado=1000, motivo="x", ahora=MARTES)


def test_la_base_de_la_caja_abierta(sesion: Session):
    """Se abrió con $50.000 escritos y eran $5.000: el cierre ya cuenta con la base buena."""
    caja = con_caja(sesion, base=50000)
    cobrado(sesion)

    correccion = casos.corregir_base(sesion, caja.id, base=5000, motivo="Eran $5.000", ahora=CIERRE)
    casos.cerrar_caja(sesion, caja.id, efectivo_contado=8000, ahora=CIERRE + minutos(1))

    assert not correccion.despues_del_cierre
    assert (caja.base, caja.efectivo_esperado, caja.diferencia) == (5000, 8000, 0)


def test_la_base_de_una_caja_cerrada_cambia_la_diferencia(sesion: Session):
    caja, _ = cerrada(sesion, base=50000, contado=8000)
    assert caja.diferencia == -45000

    casos.corregir_base(sesion, caja.id, base=5000, motivo="Eran $5.000", ahora=MARTES)

    assert (caja.efectivo_esperado, caja.diferencia) == (8000, 0)
    resumen = consultas.resumen_de_caja(sesion, caja)
    assert resumen.diferencia_al_cerrar == -45000
    assert resumen.correcciones[0].efecto == ("Base", -45000)


@pytest.mark.parametrize(
    ("corregir", "error"),
    [
        (lambda s, c: casos.corregir_conteo(s, c, efectivo_contado=0, motivo=" "), "por qué"),
        (lambda s, c: casos.corregir_conteo(s, c, efectivo_contado=-1, motivo="x"), "negativo"),
        (lambda s, c: casos.corregir_conteo(s, c, efectivo_contado=3000, motivo="x"), "ya es"),
        (lambda s, c: casos.corregir_base(s, c, base=-1, motivo="x"), "negativa"),
        (lambda s, c: casos.corregir_base(s, c, base=0, motivo="x"), "ya es"),
    ],
)
def test_lo_que_no_tiene_sentido_no_se_guarda(sesion: Session, corregir, error: str):
    caja, _ = cerrada(sesion, contado=3000)

    with pytest.raises(ValorInvalido, match=error):
        corregir(sesion, caja.id)
    assert sesion.scalars(select(CorreccionDeCaja)).all() == []


def test_pasados_30_dias_la_caja_ya_no_se_corrige(sesion: Session):
    caja, _ = cerrada(sesion, contado=3000)
    tarde = CIERRE + timedelta(days=30, minutes=1)

    with pytest.raises(CajaNoCorregible, match="más de 30 días"):
        casos.corregir_conteo(sesion, caja.id, efectivo_contado=1000, motivo="x", ahora=tarde)
    with pytest.raises(CajaNoCorregible, match="más de 30 días"):
        casos.corregir_base(sesion, caja.id, base=1000, motivo="x", ahora=tarde)
    # El día 30, todavía sí.
    casos.corregir_base(sesion, caja.id, base=1000, motivo="x", ahora=CIERRE + timedelta(days=30))


def test_la_base_no_guarda_una_correccion_de_caja_sin_sentido(sesion: Session):
    caja = con_caja(sesion)

    def correccion(**cambios) -> CorreccionDeCaja:
        datos = {
            "dato": DatoDeCaja.BASE,
            "caja_id": caja.id,
            "valor_antes": 50000,
            "valor_despues": 5000,
            "motivo": "Un motivo",
        }
        return CorreccionDeCaja(**(datos | cambios))

    with debe_rechazar(sesion, "ck_correccion_de_caja_con_motivo"):
        sesion.add(correccion(motivo=" "))
    with debe_rechazar(sesion, "ck_correccion_de_caja_algo_cambio"):
        sesion.add(correccion(valor_despues=50000))
    with debe_rechazar(sesion, "ck_correccion_de_caja_valores_no_negativos"):
        sesion.add(correccion(valor_despues=-1))
    with debe_rechazar(sesion, "ck_correccion_de_caja_conteo_ya_cerrada"):
        sesion.add(correccion(dato=DatoDeCaja.CONTEO))  # el conteo, sin la caja cerrada


# --- Lo que ven los jefes -------------------------------------------------------------------


def test_el_reporte_del_dia_de_la_caja_y_el_del_dia_en_que_se_corrigio(sesion: Session):
    caja, parqueo = cerrada(sesion, contado=0)
    casos.corregir_salida(
        sesion, parqueo.id, valor_cobrado=3000, medio_pago=NEQUI, motivo="Era Nequi", ahora=MARTES
    )

    lunes = reporte.texto(reporte.armar(sesion, HOY, MARTES), negrilla=False).splitlines()
    assert "Cuadró exacto." in lunes
    assert "Se corrigió después del cierre. Al cerrar: faltaron $3.000." in lunes
    assert (
        "- ABC123: se corrigió el cobro ($3.000 en efectivo → $3.000 por Nequi), después del "
        "cierre. Motivo: Era Nequi. Por —." in lunes
    )
    martes = reporte.texto(
        reporte.armar(sesion, HOY + timedelta(days=1), MARTES), negrilla=False
    ).splitlines()
    assert "Correcciones a cajas de días anteriores: 1" in martes
    assert (
        "- Caja del 26 de octubre: ABC123: se corrigió el cobro ($3.000 en efectivo → $3.000 "
        "por Nequi), después del cierre. Motivo: Era Nequi. Por —." in martes
    )


def test_el_balance_cuenta_la_caja_corregida(sesion: Session):
    caja, _ = cerrada(sesion, contado=0)
    casos.corregir_conteo(sesion, caja.id, efectivo_contado=3000, motivo="Contó mal", ahora=MARTES)

    lunes = balance(sesion, periodo("dia", HOY))
    martes = balance(sesion, periodo("dia", HOY + timedelta(days=1)))

    assert (lunes.cajas_que_cuadraron, lunes.faltantes) == (1, [])
    assert len(lunes.correcciones) == 1 and lunes.correcciones_de_antes == []
    assert [f.dia_de_la_caja for f in martes.correcciones_de_antes] == [HOY]
    assert martes.correcciones == []


# --- Al mismo tiempo ----------------------------------------------------------------------


@pytest.fixture
def base(motor: Engine) -> Iterator[Engine]:
    yield motor
    with motor.begin() as conexion:
        conexion.execute(
            text(
                "TRUNCATE correccion_cobro, correccion_de_caja, parqueo, pago_mensualidad, "
                "mensualidad, caja CASCADE"
            )
        )


def test_dos_correcciones_a_la_vez_en_una_caja_cerrada_no_se_pisan(base: Engine):
    """Cada una cambia lo que debía haber en el cajón. Sin el candado, la segunda leía la
    cuenta de antes de la primera y escribía encima: la caja quedaba descuadrada con sus
    propios cobros. Con él, la segunda espera a que termine la primera y parte de su cuenta."""
    with Session(base) as sesion, sesion.begin():
        caja = con_caja(sesion, base=0)
        uno, dos = cobrado(sesion, "UNO111").id, cobrado(sesion, "DOS222", despues=120).id
        casos.cerrar_caja(sesion, caja.id, efectivo_contado=9000, ahora=CIERRE)
        caja_id = caja.id

    def a_nequi(sesion: Session, parqueo_id: int) -> int:
        return casos.corregir_salida(
            sesion, parqueo_id, valor_cobrado=3000, medio_pago=NEQUI, motivo="Nequi"
        ).id

    with Session(base) as primera:
        a_nequi(primera, uno)  # ya escribió su cuenta, pero no ha terminado
        hilo, segunda = en_otro_hilo(base, lambda s: a_nequi(s, dos))
        esperar_a_que_alguien_espere(base)
        primera.commit()
    hilo.join(timeout=10)

    assert len(segunda) == 1 and isinstance(segunda[0], int), segunda
    with Session(base) as sesion:
        caja = sesion.get(Caja, caja_id)
        # Los $9.000 que se contaron eran por Nequi: en el cajón no debía haber nada.
        assert caja.efectivo_esperado == esperado_por_los_cobros(sesion, caja) == 0


# --- En la pantalla -----------------------------------------------------------------------


def en_la_caja(caja_id: int) -> dict[str, str]:
    """Lo que manda htmx desde la página de esa caja: dónde está la pantalla."""
    return HTMX | {"HX-Current-URL": f"http://testserver/parqueadero/caja/{caja_id}"}


@pytest.fixture
def caja_cerrada(adentro: TestClient, sesion: Session, reloj: dict) -> Caja:
    """Base $10.000, ABC123 pagó $3.000 en efectivo y al cerrar contaron $10.000."""
    adentro.post("/parqueadero/caja/abrir", data={"base": "10.000"}, headers=HTMX)
    adentro.post("/parqueadero/entradas", data={"placa": "ABC123", "tipo": "carro"}, headers=HTMX)
    reloj["ahora"] += minutos(60)
    parqueo = sesion.scalars(select(Parqueo).where(Parqueo.placa == "ABC123")).one()
    adentro.post(f"/parqueadero/{parqueo.id}/salida", data={"medio_pago": "efectivo"}, headers=HTMX)
    caja = sesion.scalars(select(Caja)).one()
    adentro.post(
        "/parqueadero/caja/cerrar",
        data={"caja_id": caja.id, "efectivo_contado": "10.000"},
        headers=HTMX,
    )
    sesion.refresh(caja)
    assert caja.diferencia == -3000
    return caja


def test_la_caja_cerrada_se_puede_corregir_desde_su_pagina(
    adentro: TestClient, sesion: Session, caja_cerrada: Caja
):
    pagina = adentro.get(f"/parqueadero/caja/{caja_cerrada.id}").text

    ticket = sesion.scalars(select(Parqueo)).one().id
    assert f'hx-get="/parqueadero/{ticket}/corregir"' in pagina
    assert f'hx-get="/parqueadero/caja/{caja_cerrada.id}/corregir"' in pagina
    assert "Corregir el conteo o la base" in pagina
    assert "Se corrige aquí hasta el 25 de noviembre" in pagina
    assert 'id="una-caja"' in pagina


def test_corregir_un_cobro_desde_la_caja_cerrada(
    adentro: TestClient, sesion: Session, caja_cerrada: Caja
):
    ticket = sesion.scalars(select(Parqueo)).one().id
    dialogo = adentro.get(f"/parqueadero/{ticket}/corregir", headers=en_la_caja(caja_cerrada.id))
    assert "Este cobro es de una caja que ya se cerró" in dialogo.text

    respuesta = adentro.post(
        f"/parqueadero/{ticket}/corregir",
        data={"valor": "3.000", "medio_pago": "nequi", "motivo": "Pagó por Nequi"},
        headers=en_la_caja(caja_cerrada.id),
    )

    assert aviso(respuesta) == "Listo: el cobro de ABC123 quedó en $3.000 por Nequi."
    assert respuesta.headers["HX-Retarget"] == "#una-caja"
    assert "Corregida después del cierre" in respuesta.text
    assert "Cuadró exacto" in respuesta.text and "Al cerrar: faltaron $3.000" in respuesta.text
    assert "Después del cierre" in respuesta.text


def test_corregir_el_conteo_desde_la_caja_cerrada(
    adentro: TestClient, sesion: Session, caja_cerrada: Caja
):
    dialogo = adentro.get(
        f"/parqueadero/caja/{caja_cerrada.id}/corregir", headers=en_la_caja(caja_cerrada.id)
    ).text
    assert "El efectivo que se contó" in dialogo and "La base para vueltos" in dialogo
    assert 'data-esperado="13000"' in dialogo and 'value="10.000"' in dialogo

    sin_motivo = adentro.post(
        f"/parqueadero/caja/{caja_cerrada.id}/corregir-conteo",
        data={"efectivo_contado": "13.000", "motivo": ""},
        headers=en_la_caja(caja_cerrada.id),
    )
    assert "Escribe por qué se corrige" in sin_motivo.text
    assert 'value="13.000"' in sin_motivo.text  # lo escrito sigue ahí

    respuesta = adentro.post(
        f"/parqueadero/caja/{caja_cerrada.id}/corregir-conteo",
        data={"efectivo_contado": "13.000", "motivo": "Contaron otra vez"},
        headers=en_la_caja(caja_cerrada.id),
    )

    assert aviso(respuesta) == f"Listo: el conteo de la caja {caja_cerrada.id} quedó en $13.000."
    assert "cerrar-dialogo" in eventos(respuesta)
    assert (
        "Contado (corregido)" in respuesta.text
        and "Se corrigió el efectivo contado" in respuesta.text
    )
    cierres = adentro.get("/parqueadero/caja").text
    assert "Corregida</span>" in cierres


def test_corregir_la_base_desde_la_caja_del_dia(adentro: TestClient, sesion: Session, reloj):
    adentro.post("/parqueadero/caja/abrir", data={"base": "50.000"}, headers=HTMX)
    caja = sesion.scalars(select(Caja)).one()

    dialogo = adentro.get(f"/parqueadero/caja/{caja.id}/corregir", headers=HTMX).text
    respuesta = adentro.post(
        f"/parqueadero/caja/{caja.id}/corregir-base",
        data={"base": "5.000", "motivo": "Eran $5.000"},
        headers=HTMX | {"HX-Current-URL": "http://testserver/parqueadero/caja"},
    )

    assert "El efectivo que se contó" not in dialogo  # abierta: todavía no se ha contado
    assert aviso(respuesta) == f"Listo: la base de la caja {caja.id} quedó en $5.000."
    assert respuesta.headers["HX-Retarget"] == "#caja-abierta"
    assert "Se corrigió la base" in respuesta.text


def test_el_balance_del_dia_siguiente_muestra_la_correccion(
    adentro: TestClient, sesion: Session, caja_cerrada: Caja, reloj: dict
):
    reloj["ahora"] += timedelta(days=1)
    adentro.post(
        f"/parqueadero/caja/{caja_cerrada.id}/corregir-conteo",
        data={"efectivo_contado": "13.000", "motivo": "Contaron otra vez"},
        headers=en_la_caja(caja_cerrada.id),
    )

    martes = adentro.get("/parqueadero/balance").text

    assert "Correcciones a cajas de días anteriores (1)" in martes
    assert "Caja del 26 de octubre" in martes
    assert "Correcciones a cajas de días anteriores: 1" in martes  # el texto del reporte


def test_pasados_30_dias_la_pagina_lo_dice_y_no_deja_corregir(
    adentro: TestClient, sesion: Session, caja_cerrada: Caja, reloj: dict
):
    reloj["ahora"] += timedelta(days=31)

    pagina = adentro.get(f"/parqueadero/caja/{caja_cerrada.id}").text
    dialogo = adentro.get(f"/parqueadero/caja/{caja_cerrada.id}/corregir", headers=HTMX)
    conteo = adentro.post(
        f"/parqueadero/caja/{caja_cerrada.id}/corregir-conteo",
        data={"efectivo_contado": "13.000", "motivo": "Tarde"},
        headers=en_la_caja(caja_cerrada.id),
    )

    assert "se cerró hace más de 30 días" in pagina and "Corregir</button>" not in pagina
    assert "ya no se corrige" in error(dialogo)
    assert "se cerró hace más de 30 días" in error(conteo)
    assert sesion.get(Parqueo, sesion.scalars(select(Parqueo.id)).one()).estado is (
        EstadoParqueo.SALIO
    )
