"""Salidas de caja: la plata que sale del cajón (un gasto, un retiro o una consignación), en
los dos negocios.

Lo que tiene que cumplirse siempre:
- lo que debe haber al cerrar es la base, más lo cobrado en efectivo, menos las salidas;
- no sale más plata de la que debe haber en el cajón;
- una salida no se borra: se anula, con motivo y quién. Si la caja ya se cerró (hasta 30 días),
  queda como corrección después del cierre y la diferencia se pone al día;
- la salida de un negocio no se ve ni se toca desde el otro;
- el cierre, su ticket, el Balance, el reporte y el Excel dicen qué salió.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta

import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.caja import casos_de_uso as cajas
from app.caja.errores import SinCajaAbierta, ValorInvalido
from app.caja.estados import CategoriaDeGasto, DatoDeCaja, Negocio, TipoDeSalida
from app.caja.periodos import periodo
from app.caja.tablas import CorreccionDeCaja, SalidaDeCaja
from app.cli import configuracion_alembic, migrar
from app.parqueadero import balance as balance_parqueadero
from app.parqueadero import casos_de_uso as parqueo
from app.parqueadero import consultas as consultas_parqueadero
from app.parqueadero import reporte as reporte_parqueadero
from app.parqueadero.estados import TipoParqueo
from app.turnos import balance as balance_lavadero
from app.turnos import caja as caja_lavadero
from app.turnos import casos_de_uso as casos
from app.turnos import consultas_de_caja as consultas_lavadero
from app.turnos import reporte as reporte_lavadero
from app.turnos.estados import MedioPago
from pruebas.base_de_pruebas import URL_PRUEBAS, borrar_base, recrear_base
from pruebas.fabricas import LUNES_8AM, minutos, turno_listo
from pruebas.navegador import HTMX, aviso, error

HOY = LUNES_8AM.date()
ABRIO = LUNES_8AM - minutos(60)


@dataclass(frozen=True)
class Negocio_:
    """Lo que las pruebas necesitan de cada negocio para hacer lo mismo en los dos."""

    negocio: Negocio
    prefijo: str
    abrir: Callable
    cerrar: Callable
    cuadre: Callable
    resumen: Callable
    cobrado: int  # lo que entra en efectivo con `cobrar`

    def cobrar(self, sesion: Session) -> None:
        """Un cobro en efectivo a las 9:00 (un lavado de $40.000, o una hora de carro de $3.000)."""
        if self.negocio is Negocio.LAVADERO:
            turno = turno_listo(sesion)
            casos.cobrar(
                sesion, turno, medio_pago=MedioPago.EFECTIVO, ahora=LUNES_8AM + minutos(60)
            )
        else:
            carro = parqueo.registrar_entrada(
                sesion, placa="ABC123", tipo=TipoParqueo.CARRO, ahora=LUNES_8AM
            )
            parqueo.cobrar_salida(
                sesion, carro.id, medio_pago=MedioPago.EFECTIVO, ahora=LUNES_8AM + minutos(60)
            )

    def esperado(self, sesion: Session, caja) -> int:
        return self.cuadre(sesion, caja).efectivo_esperado

    def salida(self, sesion: Session, valor: int, **datos):
        datos = {"tipo": TipoDeSalida.GASTO, "categoria": CategoriaDeGasto.INSUMOS} | datos
        return cajas.registrar_salida(
            sesion,
            self.negocio,
            descripcion=datos.pop("descripcion", "Jabón y cera"),
            valor=valor,
            efectivo_esperado=self.esperado,
            ahora=LUNES_8AM + minutos(90),
            **datos,
        )


LAVADERO = Negocio_(
    Negocio.LAVADERO,
    "",
    caja_lavadero.abrir_caja,
    caja_lavadero.cerrar_caja,
    caja_lavadero.cuadre_de_caja,
    consultas_lavadero.resumen_de_caja,
    40_000,
)
PARQUEADERO = Negocio_(
    Negocio.PARQUEADERO,
    "/parqueadero",
    parqueo.abrir_caja,
    parqueo.cerrar_caja,
    parqueo.cuadre_de_caja,
    consultas_parqueadero.resumen_de_caja,
    3_000,
)
LOS_DOS = pytest.mark.parametrize("n", [LAVADERO, PARQUEADERO], ids=["lavadero", "parqueadero"])


@pytest.fixture
def caja_con_un_cobro(sesion: Session, request):
    """La caja del negocio abierta con $50.000 de base y un cobro en efectivo."""
    n: Negocio_ = request.getfixturevalue("n")
    caja = n.abrir(sesion, base=50_000, ahora=ABRIO)
    n.cobrar(sesion)
    return caja


# --- Las reglas ----------------------------------------------------------------------------


@LOS_DOS
def test_una_salida_baja_lo_que_debe_haber(sesion: Session, n: Negocio_, caja_con_un_cobro):
    caja = caja_con_un_cobro
    antes = n.esperado(sesion, caja)

    salida = n.salida(sesion, 2_000, descripcion="  Bolsas   de basura ")

    assert (salida.caja_id, salida.valor, salida.descripcion) == (
        caja.id,
        2_000,
        "Bolsas de basura",
    )
    assert n.esperado(sesion, caja) == antes - 2_000 == 50_000 + n.cobrado - 2_000
    cerrada = n.cerrar(
        sesion, caja.id, efectivo_contado=antes - 2_000, ahora=LUNES_8AM + minutos(600)
    )
    assert (cerrada.efectivo_esperado, cerrada.diferencia) == (antes - 2_000, 0)


@LOS_DOS
def test_no_sale_mas_de_lo_que_hay(sesion: Session, n: Negocio_, caja_con_un_cobro):
    hay = n.esperado(sesion, caja_con_un_cobro)

    with pytest.raises(ValorInvalido, match="En el cajón debe haber"):
        n.salida(sesion, hay + 1)

    assert n.salida(sesion, hay).valor == hay  # todo lo que hay, sí


@pytest.mark.parametrize(
    ("datos", "dice"),
    [
        ({"tipo": "prestamo"}, "Elige qué fue"),
        ({"categoria": None}, "Elige en qué se gastó"),
        ({"categoria": "fiesta"}, "Elige en qué se gastó"),
        ({"descripcion": "   "}, "Escribe en qué se fue la plata"),
        ({"descripcion": "x" * 121}, "máximo 120 letras"),
        ({"valor": 0}, "Escribe cuánta plata salió"),
        ({"valor": None}, "Escribe cuánta plata salió"),
        ({"valor": 100_000_001}, "revisa los ceros"),
    ],
)
def test_lo_que_se_revisa(sesion: Session, datos: dict, dice: str):
    LAVADERO.abrir(sesion, base=50_000, ahora=ABRIO)
    valor = datos.pop("valor", 5_000)

    with pytest.raises(ValorInvalido, match=dice):
        LAVADERO.salida(sesion, valor, **datos)

    assert sesion.scalars(select(SalidaDeCaja)).all() == []


def test_un_retiro_no_tiene_categoria(sesion: Session):
    LAVADERO.abrir(sesion, base=50_000, ahora=ABRIO)

    retiro = LAVADERO.salida(
        sesion, 30_000, tipo="retiro", categoria="aseo", descripcion="Para el arriendo"
    )

    assert (retiro.tipo, retiro.categoria) == (TipoDeSalida.RETIRO, None)


def test_sin_caja_abierta_no_sale_plata(sesion: Session):
    with pytest.raises(SinCajaAbierta, match="ábrela para registrar una salida"):
        LAVADERO.salida(sesion, 5_000)


@LOS_DOS
def test_anular_con_la_caja_abierta(sesion: Session, n: Negocio_, caja_con_un_cobro):
    caja = caja_con_un_cobro
    antes = n.esperado(sesion, caja)
    salida = n.salida(sesion, 2_000)

    with pytest.raises(ValorInvalido, match="Escribe por qué se anula"):
        cajas.anular_salida(sesion, n.negocio, salida.id, motivo=" ")
    cajas.anular_salida(sesion, n.negocio, salida.id, motivo="Se registró dos veces")

    assert salida.anulada and salida.motivo_anulacion == "Se registró dos veces"
    assert n.esperado(sesion, caja) == antes
    correccion = sesion.scalars(select(CorreccionDeCaja)).one()
    assert (correccion.dato, correccion.despues_del_cierre) == (DatoDeCaja.SALIDA, False)
    assert correccion.detalle == "Gasto · Insumos: Jabón y cera"
    with pytest.raises(ValorInvalido, match="ya estaba anulada"):
        cajas.anular_salida(sesion, n.negocio, salida.id, motivo="Otra vez")


@LOS_DOS
def test_anular_despues_del_cierre_cambia_la_diferencia(
    sesion: Session, n: Negocio_, caja_con_un_cobro
):
    """Se anotó una salida de $2.000 que no salió: al contar sobraron $2.000. Al anularla
    después del cierre, la caja cuadra, y queda que al cerrar sobraban."""
    caja = caja_con_un_cobro
    contado = n.esperado(sesion, caja)
    n.salida(sesion, 2_000)
    n.cerrar(sesion, caja.id, efectivo_contado=contado, ahora=LUNES_8AM + minutos(600))
    assert caja.diferencia == 2_000
    salida = sesion.scalars(select(SalidaDeCaja)).one()

    cajas.anular_salida(
        sesion, n.negocio, salida.id, motivo="No salió", ahora=LUNES_8AM + timedelta(days=1)
    )

    assert (caja.efectivo_esperado, caja.diferencia) == (contado, 0)
    resumen = n.resumen(sesion, caja)
    assert resumen.corregida_despues_del_cierre
    assert resumen.diferencia_al_cerrar == 2_000
    (fila,) = resumen.correcciones
    assert fila.en_una_linea.startswith(
        "Se anuló la salida «Gasto · Insumos: Jabón y cera» de $2.000, después del cierre"
    )


def test_la_salida_de_un_negocio_no_se_toca_desde_el_otro(sesion: Session):
    PARQUEADERO.abrir(sesion, base=50_000, ahora=ABRIO)
    del_parqueadero = PARQUEADERO.salida(sesion, 5_000)

    with pytest.raises(ValorInvalido, match="Esa salida no existe"):
        cajas.anular_salida(sesion, Negocio.LAVADERO, del_parqueadero.id, motivo="No")


# --- El Balance y el reporte ---------------------------------------------------------------


def test_el_balance_y_el_reporte_del_lavadero(sesion: Session):
    caja = LAVADERO.abrir(sesion, base=50_000, ahora=ABRIO)
    LAVADERO.cobrar(sesion)
    LAVADERO.salida(sesion, 10_000, categoria="aseo", descripcion="Escobas")
    LAVADERO.salida(sesion, 5_000, descripcion="Cera")
    LAVADERO.salida(sesion, 20_000, tipo="retiro", descripcion="Para el arriendo")
    anulada = LAVADERO.salida(sesion, 1_000, descripcion="No era")
    cajas.anular_salida(sesion, Negocio.LAVADERO, anulada.id, motivo="Se repitió")
    LAVADERO.cerrar(sesion, caja.id, efectivo_contado=55_000, ahora=LUNES_8AM + minutos(600))

    b = balance_lavadero.balance(sesion, periodo("dia", HOY))
    texto = reporte_lavadero.texto(reporte_lavadero.armar(sesion, HOY, LUNES_8AM + minutos(660)))

    s = b.salidas_de_caja
    assert s.gastos == [("Aseo", 10_000, 1), ("Insumos", 5_000, 1)]
    assert (s.total_gastos, s.retiros, s.consignaciones, s.total) == (15_000, 20_000, 0, 35_000)
    assert "Base $50.000 · Salidas $35.000 · Debía haber $55.000 · Contaron $55.000" in texto
    assert "*Salidas de caja: $35.000*\nGastos: $15.000 (Aseo $10.000, Insumos $5.000)" in texto
    assert "Retiros: $20.000" in texto


def test_el_reporte_del_parqueadero_con_la_caja_abierta(sesion: Session):
    PARQUEADERO.abrir(sesion, base=50_000, ahora=ABRIO)
    PARQUEADERO.cobrar(sesion)
    PARQUEADERO.salida(sesion, 40_000, tipo="consignacion", descripcion="Bancolombia")

    b = balance_parqueadero.balance(sesion, periodo("dia", HOY))
    texto = reporte_parqueadero.texto(
        reporte_parqueadero.armar(sesion, HOY, LUNES_8AM + minutos(120))
    )

    assert b.salidas_de_caja.consignaciones == 40_000
    assert "debería haber $13.000 en efectivo (con la base, y sin las salidas de $40.000)" in texto
    assert "Consignaciones: $40.000" in texto


# --- En la pantalla -------------------------------------------------------------------------


@LOS_DOS
def test_registrar_y_anular_desde_la_caja(
    adentro: TestClient, sesion: Session, n: Negocio_, caja_con_un_cobro
):
    caja = caja_con_un_cobro
    sesion.commit()
    dialogo = adentro.get(f"{n.prefijo}/caja/salidas/nueva", headers=HTMX).text
    assert "Registrar una salida" in dialogo and 'name="categoria"' in dialogo

    respuesta = adentro.post(
        f"{n.prefijo}/caja/salidas",
        data={
            "tipo": "gasto",
            "categoria": "aseo",
            "descripcion": "Bolsas de basura",
            "valor": "$2.000",
        },
        headers=HTMX,
    )

    assert aviso(respuesta) == "Salida anotada: $2.000 (gasto). La caja la descuenta."
    assert "Bolsas de basura" in respuesta.text and "−$2.000" in respuesta.text
    pagina = adentro.get(f"{n.prefijo}/caja").text
    assert "Salidas de esta caja" in pagina
    assert "− salidas $2.000" in pagina
    salida = sesion.scalars(select(SalidaDeCaja)).one()
    ticket = adentro.get(f"{n.prefijo}/caja/{caja.id}/ticket").text
    assert "<dt>Salidas</dt><dd>−$2.000</dd>" in ticket and "Bolsas de basura" in ticket

    anular = adentro.post(
        f"{n.prefijo}/caja/salidas/{salida.id}/anular",
        data={"motivo": "Se registró en la caja que no era"},
        headers=HTMX,
    )

    assert aviso(anular) == "Salida anulada: $2.000 vuelven a contar en el cajón."
    assert "Anulada por eduar: Se registró en la caja que no era" in anular.text


def test_una_salida_imposible_se_explica_en_el_dialogo(
    adentro: TestClient, sesion: Session, caja_del_lavadero
):
    sesion.commit()

    respuesta = adentro.post(
        "/caja/salidas",
        data={"tipo": "gasto", "categoria": "aseo", "descripcion": "Trapero", "valor": "9.000"},
        headers=HTMX,
    )

    assert respuesta.status_code == 200 and aviso(respuesta) == ""
    assert "En el cajón debe haber $0: no pueden salir $9.000" in respuesta.text
    assert 'value="Trapero"' in respuesta.text  # lo escrito sigue ahí


def test_sin_caja_abierta_el_dialogo_lo_dice(adentro: TestClient):
    respuesta = adentro.get("/caja/salidas/nueva", headers=HTMX)

    assert error(respuesta) == "La caja está cerrada: ábrela para registrar una salida."


def test_una_salida_de_otro_negocio_no_existe_aqui(adentro: TestClient, sesion: Session):
    PARQUEADERO.abrir(sesion, base=50_000, ahora=ABRIO)
    salida = PARQUEADERO.salida(sesion, 5_000)
    sesion.commit()

    assert adentro.get(f"/caja/salidas/{salida.id}/anular", headers=HTMX).status_code == 404


def test_las_salidas_para_excel_y_en_el_balance(adentro: TestClient, sesion: Session, reloj: dict):
    reloj["ahora"] = LUNES_8AM + minutos(120)
    LAVADERO.abrir(sesion, base=50_000, ahora=ABRIO)
    LAVADERO.cobrar(sesion)
    LAVADERO.salida(sesion, 10_000, categoria="aseo", descripcion="Escobas; y trapero")
    sesion.commit()

    archivo = adentro.get("/caja/exportar-salidas", params={"desde": "2026-10-26"})
    balance = adentro.get("/balance").text

    assert archivo.headers["content-disposition"].endswith(
        'salidas-lavadero-2026-10-26-a-2026-10-26.csv"'
    )
    lineas = archivo.text.lstrip("﻿").splitlines()
    assert lineas[0].startswith("Fecha;Hora;Caja;Tipo;Categoría;Descripción;Valor")
    assert '"Escobas; y trapero";10000;—;No;' in lineas[1]  # registrada sin usuario
    assert "Salidas de caja" in balance and "Gasto · Aseo" in balance


# --- La migración ---------------------------------------------------------------------------


@pytest.mark.usefixtures("motor")
def test_no_se_baja_de_version_con_salidas_registradas():
    url = (
        make_url(URL_PRUEBAS)
        .set(database="auto_ibaguer_pruebas_salidas")
        .render_as_string(hide_password=False)
    )
    recrear_base(url)
    motor = create_engine(url)
    alembic = configuracion_alembic(url, configurar_logs=False)
    try:
        migrar(url, configurar_logs=False)
        with motor.begin() as conexion:
            caja = conexion.execute(
                text("INSERT INTO caja (negocio, base) VALUES ('lavadero', 0) RETURNING id")
            ).scalar_one()
            conexion.execute(
                text(
                    "INSERT INTO salida_de_caja (caja_id, tipo, descripcion, valor) "
                    "VALUES (:caja, 'retiro', 'Para el banco', 5000)"
                ),
                {"caja": caja},
            )

        with pytest.raises(Exception, match="Hay salidas de caja registradas"):
            command.downgrade(alembic, "0012")
        with motor.begin() as conexion:
            conexion.execute(text("DELETE FROM salida_de_caja"))
        command.downgrade(alembic, "0012")
        command.upgrade(alembic, "head")
    finally:
        motor.dispose()
        borrar_base(url)
