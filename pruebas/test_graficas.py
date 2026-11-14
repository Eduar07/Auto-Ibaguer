"""Las gráficas del Balance: qué dice cada barra, qué tan alta va, cuál es el mejor día y el
más bajo, y la pantalla con sus filtros (plata o vehículos; todos, carros o motos; y los días
que se escojan).

Lo que tiene que cumplirse siempre:
- cada barra mide su valor contra el tope del eje, que es un número redondo;
- hoy va aparte (el día va por la mitad): no es ni el mejor ni el más bajo ni cuenta para el
  promedio;
- el promedio por día de la semana solo sale con dos semanas de días completos;
- los filtros se quedan al moverse de periodo, y el rango nunca pasa de 92 días.
"""

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.parqueadero import graficas
from app.parqueadero.balance import (
    Balance,
    CifrasDelDia,
    DiaDelBalance,
    rango,
)
from app.parqueadero.estados import TipoParqueo
from pruebas.test_pantallas_balance import dias_de_trabajo

CARRO, MOTO = TipoParqueo.CARRO, TipoParqueo.MOTO
HOY = date(2026, 11, 13)  # un viernes


def dia(cuando: date, carros: int = 0, motos: int = 0, entradas: int = 0) -> DiaDelBalance:
    """Un día con lo que pagaron los carros y las motos al salir (una salida por cada uno que
    pagó algo). Un día en $0 no tuvo movimiento."""
    por_tipo = {
        CARRO: CifrasDelDia(entradas=entradas, salidas=int(carros > 0), cobrado_salidas=carros),
        MOTO: CifrasDelDia(entradas=0, salidas=int(motos > 0), cobrado_salidas=motos),
    }
    return DiaDelBalance(
        dia=cuando,
        entradas=entradas,
        salidas=int(carros > 0) + int(motos > 0),
        cobrado_salidas=carros + motos,
        por_tipo=por_tipo,
    )


def balance_de(*dias: DiaDelBalance) -> Balance:
    return Balance(
        periodo=rango(dias[0].dia, dias[-1].dia),
        entradas={},
        salidas={},
        cobrado_salidas={},
        mensualistas_sin_pagar=0,
        pagos_mensualidad=0,
        cobrado_mensualidades=0,
        por_medio={},
        descuentos=0,
        salidas_con_descuento=0,
        recargos=0,
        ajustes=[],
        cuantos_ajustes=0,
        anuladas=[],
        total_anuladas=0,
        cajas=[],
        dias=list(dias),
    )


def semana(*valores: int, hasta: date = HOY) -> Balance:
    """Un día por valor, el último es `hasta`."""
    primero = hasta - timedelta(days=len(valores) - 1)
    return balance_de(*(dia(primero + timedelta(days=i), v) for i, v in enumerate(valores)))


# --- El eje -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("maximo", "tope", "paso"),
    [
        (380000, 400000, 100000),
        (12500, 15000, 5000),
        (1_250_000, 1_500_000, 500000),
        (7, 8, 2),
        (3, 3, 1),
        (0, 4, 1),  # sin nada: el eje igual tiene escala
    ],
)
def test_el_tope_del_eje_es_redondo(maximo: int, tope: int, paso: int):
    assert graficas.tope_y_paso(maximo) == (tope, paso)


def test_las_lineas_del_fondo_hablan_en_pesos_cortos():
    g = graficas.dia_por_dia(semana(380000, 120000, 250000), HOY, "plata", None)

    assert [linea.texto for linea in g.rejilla] == [
        "$0",
        "$100 mil",
        "$200 mil",
        "$300 mil",
        "$400 mil",
    ]
    assert [linea.alto for linea in g.rejilla] == [0, 25, 50, 75, 100]


# --- Día por día --------------------------------------------------------------------------


def test_cada_barra_mide_su_valor_contra_el_tope():
    g = graficas.dia_por_dia(semana(380000, 0, 200000, 100000), HOY, "plata", None)

    assert [b.alto for b in g.barras] == [95, 0, 50, 25]
    assert [b.texto for b in g.barras] == ["$380.000", "$0", "$200.000", "$100.000"]


def test_hoy_va_aparte_y_no_cuenta_para_mejor_bajo_ni_promedio():
    """Hoy lleva $10.000 a mediodía: no es el día más bajo, solo va por la mitad."""
    g = graficas.dia_por_dia(semana(300000, 100000, 200000, 10000), HOY, "plata", None)

    hoy = g.barras[-1]
    assert hoy.en_curso and hoy.etiqueta == "Hoy" and hoy.marca is None
    assert "(va hasta ahora)" in hoy.nombre
    assert [b.marca for b in g.barras[:3]] == ["Mejor día", "Más bajo", None]
    assert g.promedio is not None and g.promedio.texto == "Promedio $200.000"
    assert g.resumen == (
        "Mejor día: el martes 10 de noviembre, $300.000. "
        "Más bajo: el miércoles 11 de noviembre, $100.000. "
        "Promedio: $200.000 por día."
    )


def test_si_todos_los_dias_valen_lo_mismo_no_hay_mejor_ni_bajo():
    g = graficas.dia_por_dia(semana(5000, 5000, 5000, 1000), HOY, "plata", None)

    assert all(b.marca is None for b in g.barras)


def test_sin_dias_completos_no_hay_promedio():
    g = graficas.dia_por_dia(semana(8000), HOY, "plata", None)

    assert g.promedio is None
    assert g.resumen == "Todavía no hay días completos para comparar."


def test_los_dias_que_no_han_llegado_no_salen():
    b = balance_de(*(dia(HOY + timedelta(days=n), 1000) for n in range(-2, 3)))

    g = graficas.dia_por_dia(b, HOY, "plata", None)

    assert len(g.barras) == 3


def test_solo_carros_solo_motos_o_vehiculos():
    b = balance_de(
        dia(HOY - timedelta(days=1), carros=9000, motos=3000, entradas=4),
        dia(HOY, carros=6000, motos=1500, entradas=2),
    )

    carros = graficas.dia_por_dia(b, HOY, "plata", CARRO)
    motos = graficas.dia_por_dia(b, HOY, "plata", MOTO)
    vehiculos = graficas.dia_por_dia(b, HOY, "vehiculos", None)

    assert [x.valor for x in carros.barras] == [9000, 6000]
    assert [x.valor for x in motos.barras] == [3000, 1500]
    assert [x.texto for x in vehiculos.barras] == ["4 vehículos", "2 vehículos"]
    assert vehiculos.barras[0].detalle == "4 carros, 0 motos"
    assert carros.titulo == "Día por día: plata recogida (carros)"
    assert motos.barras[0].detalle == "1 salida: $3.000"  # solo las de las motos


def test_solo_las_salidas_sin_las_mensualidades():
    """El día que se pagan mensualidades parece un gran día; las salidas dicen cómo fue el
    parqueadero en sí."""
    con_mes = DiaDelBalance(
        dia=HOY - timedelta(days=1),
        salidas=3,
        cobrado_salidas=9000,
        cobrado_mensualidades=100000,
        por_tipo={
            CARRO: CifrasDelDia(salidas=3, cobrado_salidas=9000, cobrado_mensualidades=100000)
        },
    )
    b = balance_de(con_mes, dia(HOY, 6000))

    todo = graficas.dia_por_dia(b, HOY, "plata", None)
    salidas = graficas.dia_por_dia(b, HOY, "salidas", None)

    assert (todo.barras[0].valor, salidas.barras[0].valor) == (109000, 9000)
    assert salidas.titulo == "Día por día: plata de las salidas (sin mensualidades)"
    assert todo.barras[0].detalle == "3 salidas: $9.000 · mensualidades: $100.000"


def test_los_dias_antes_de_empezar_no_cuentan():
    """Antes del primer día con movimiento, el sistema no se usaba: esos días en $0 no son el
    día más bajo ni bajan el promedio."""
    g = graficas.dia_por_dia(semana(0, 0, 5000, 3000, 4000, 1000), HOY, "plata", None)

    assert [b.marca for b in g.barras] == [None, None, "Mejor día", "Más bajo", None, None]
    assert g.promedio is not None and g.promedio.texto == "Promedio $4.000"
    assert g.resumen.endswith(
        "Cuenta desde el 10 de noviembre de 2026, el primer día con movimiento."
    )


def test_el_promedio_por_dia_cuenta_desde_el_primer_dia_con_movimiento():
    veinte_en_cero = [0] * 20
    pocas = semana(*veinte_en_cero, *[1000] * 10, 500)
    suficientes = semana(*veinte_en_cero, *[1000] * 14, 500)

    assert graficas.por_dia_de_la_semana(pocas, HOY, "plata", None) is None
    semana_ = graficas.por_dia_de_la_semana(suficientes, HOY, "plata", None)
    assert semana_ is not None
    assert {b.detalle for b in semana_.barras} == {"Promedio de 2 lunes", "Promedio de 2 martes",
        "Promedio de 2 miércoles", "Promedio de 2 jueves", "Promedio de 2 viernes",
        "Promedio de 2 sábados", "Promedio de 2 domingos"}  # fmt: skip


def test_debajo_de_cada_columna_lo_que_cabe():
    una_semana = graficas.dia_por_dia(semana(*[1000] * 7), HOY, "plata", None)
    un_mes = graficas.dia_por_dia(semana(*[1000] * 30), HOY, "plata", None)

    assert [b.etiqueta for b in una_semana.barras] == [
        "Sáb 7",
        "Dom 8",
        "Lun 9",
        "Mar 10",
        "Mié 11",
        "Jue 12",
        "Hoy",
    ]
    etiquetas = [b.etiqueta for b in un_mes.barras]
    assert etiquetas[0] == "15 oct" and etiquetas[-1] == "Hoy"
    assert "1 nov" in etiquetas  # el cambio de mes siempre se marca
    assert len([e for e in etiquetas if e]) <= 13  # no se encima la letra


def test_las_fechas_de_abajo_no_se_pegan():
    """En 90 días va una cada 9 columnas, pero ninguna a menos de 5 de un cambio de mes o de
    "Hoy": antes, el 30 y el "1 oct" quedaban uno encima del otro."""
    g = graficas.dia_por_dia(semana(*[1000] * 90), HOY, "plata", None)

    con_letra = [i for i, b in enumerate(g.barras) if b.etiqueta]
    assert all(b - a >= 5 for a, b in zip(con_letra, con_letra[1:], strict=False))
    assert {"1 sep", "1 oct", "1 nov", "Hoy"} <= {g.barras[i].etiqueta for i in con_letra}


def test_hoy_le_gana_al_cambio_de_mes_si_quedan_pegados():
    """El 2 de noviembre, "1 nov" quedaría al lado de "Hoy": va solo "Hoy"."""
    dos_de_noviembre = date(2026, 11, 2)
    g = graficas.dia_por_dia(
        semana(*[1000] * 30, hasta=dos_de_noviembre), dos_de_noviembre, "plata", None
    )

    assert [b.etiqueta for b in g.barras[-2:]] == [None, "Hoy"]
    assert g.barras[0].etiqueta == "4 oct"


def test_el_valor_de_las_orillas_se_corre_hacia_adentro():
    g = graficas.dia_por_dia(semana(*range(1000, 11000, 1000)), HOY, "plata", None)

    assert [g.barras[0].lado, g.barras[5].lado, g.barras[-1].lado] == ["inicio", "centro", "fin"]


# --- Por día de la semana -------------------------------------------------------------------


def test_con_menos_de_dos_semanas_no_sale_el_promedio_por_dia():
    assert graficas.por_dia_de_la_semana(semana(*[1000] * 14), HOY, "plata", None) is None


def test_el_promedio_de_cada_dia_de_la_semana():
    """Tres semanas completas (y hoy, que no cuenta): los sábados son los mejores y los
    lunes los más flojos."""
    hasta = HOY  # viernes
    valores = []
    for n in range(22):
        cuando = hasta - timedelta(days=21 - n)
        valores.append({0: 50000, 5: 300000}.get(cuando.weekday(), 100000) + n)
    g = graficas.por_dia_de_la_semana(semana(*valores, hasta=hasta), HOY, "plata", None)

    assert g is not None
    assert [b.etiqueta for b in g.barras] == ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]
    assert g.barras[5].nombre == "Los sábados" and g.barras[5].marca == "El mejor"
    assert g.barras[0].nombre == "Los lunes" and g.barras[0].marca == "El más bajo"
    assert g.barras[5].detalle == "Promedio de 3 sábados"
    assert g.barras[0].detalle == "Promedio de 3 lunes"
    assert g.resumen.startswith("En promedio, el mejor día es el sábado ($300.0")
    assert g.promedio is not None and g.promedio.texto.startswith("Promedio de todos")


# --- El rango -----------------------------------------------------------------------------


def test_el_rango_se_voltea_y_no_pasa_de_92_dias():
    assert rango(date(2026, 11, 10), date(2026, 11, 1)).desde == date(2026, 11, 1)
    largo = rango(date(2026, 1, 1), date(2026, 11, 13))
    assert (largo.cuantos_dias, largo.hasta) == (92, date(2026, 11, 13))


def test_el_rango_anterior_y_el_siguiente_tienen_los_mismos_dias():
    p = rango(date(2026, 10, 1), date(2026, 10, 30))

    assert (p.anterior().desde, p.anterior().hasta) == (date(2026, 9, 1), date(2026, 9, 30))
    assert (p.siguiente().desde, p.siguiente().hasta) == (date(2026, 10, 31), date(2026, 11, 29))
    assert p.titulo == "Del 1 al 30 de octubre de 2026"
    assert p.nombre_del_anterior(HOY) == "Los 30 días anteriores"


# --- En la pantalla -----------------------------------------------------------------------


@pytest.fixture
def octubre(sesion: Session) -> None:
    """Del 1 al 25 de octubre, un día de trabajo cada día (el reloj de las pruebas va el 26)."""
    dias_de_trabajo(sesion, 1, 25)
    sesion.commit()


def test_el_mes_trae_las_dos_graficas(adentro: TestClient, octubre):
    pagina = adentro.get("/parqueadero/balance", params={"periodo": "mes"}).text

    assert "Cómo va" in pagina
    assert 'id="grafica-dias"' in pagina and 'id="grafica-semana"' in pagina
    assert "Día por día: plata recogida" in pagina
    assert "Promedio por día de la semana" in pagina
    # Cada columna lleva al Balance de ese día, y dice su valor al lector de pantalla.
    assert 'href="/parqueadero/balance?periodo=dia&amp;fecha=2026-10-01"' in pagina
    assert 'aria-label="Jueves 1 de octubre: $' in pagina
    assert "Ver el promedio por día de la semana en números" in pagina


def test_la_semana_explica_cuando_sale_el_promedio_por_dia(adentro: TestClient, octubre):
    pagina = adentro.get("/parqueadero/balance", params={"periodo": "semana"}).text

    assert 'id="grafica-dias"' in pagina and 'id="grafica-semana"' not in pagina
    assert "Con dos semanas o más de días completos" in pagina


def test_el_dia_no_trae_grafica_pero_lleva_a_la_del_mes(adentro: TestClient, octubre):
    pagina = adentro.get("/parqueadero/balance").text

    assert "Cómo va" not in pagina
    assert 'href="/parqueadero/balance?periodo=mes&amp;fecha=2026-10-26"' in pagina
    assert "Ver la gráfica del mes" in pagina


def test_los_filtros_de_las_graficas(adentro: TestClient, octubre):
    pagina = adentro.get(
        "/parqueadero/balance", params={"periodo": "mes", "ver": "vehiculos", "tipo": "moto"}
    ).text

    assert "Día por día: vehículos que entraron (motos)" in pagina
    assert "Día por día · solo motos" in pagina  # la tabla sigue el filtro
    assert '<span aria-current="page">Vehículos</span>' in pagina
    assert '<span aria-current="page">Motos</span>' in pagina
    # Moverse de mes o cambiar a la semana no quita los filtros.
    assert "periodo=mes&amp;fecha=2026-09-01&amp;ver=vehiculos&amp;tipo=moto" in pagina
    assert "periodo=semana&amp;fecha=2026-10-26&amp;ver=vehiculos&amp;tipo=moto" in pagina


def test_un_filtro_raro_muestra_lo_de_siempre(adentro: TestClient, octubre):
    pagina = adentro.get(
        "/parqueadero/balance", params={"periodo": "mes", "ver": "x", "tipo": "bus"}
    ).text

    assert "Día por día: plata recogida" in pagina
    assert '<span aria-current="page">Todos</span>' in pagina


def test_los_dias_que_se_escojan(adentro: TestClient, octubre):
    pagina = adentro.get(
        "/parqueadero/balance",
        params={"periodo": "rango", "desde": "2026-10-01", "hasta": "2026-10-20"},
    ).text

    assert "Del 1 al 20 de octubre de 2026" in pagina
    assert '<span aria-current="page">Rango</span>' in pagina
    assert 'name="desde" type="date" value="2026-10-01"' in pagina
    assert "Recogido en esos días" in pagina and "Los 20 días anteriores" in pagina
    assert "periodo=rango&amp;desde=2026-09-11&amp;hasta=2026-09-30" in pagina  # anterior
    assert "periodo=rango&amp;desde=2026-10-21&amp;hasta=2026-11-09" in pagina  # siguiente
    assert 'id="grafica-semana"' in pagina


def test_un_rango_raro_muestra_los_ultimos_30_dias(adentro: TestClient, octubre):
    pagina = adentro.get(
        "/parqueadero/balance", params={"periodo": "rango", "desde": "ayer", "hasta": ""}
    ).text

    assert "Del 27 de septiembre al 26 de octubre de 2026" in pagina


def test_el_rango_no_llega_al_futuro(adentro: TestClient, octubre):
    pagina = adentro.get(
        "/parqueadero/balance",
        params={"periodo": "rango", "desde": "2026-10-20", "hasta": "2027-01-01"},
    ).text

    assert "Del 20 al 26 de octubre de 2026" in pagina
    assert "Los días siguientes" not in pagina
