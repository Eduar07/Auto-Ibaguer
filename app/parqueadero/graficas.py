"""Las gráficas del Balance del parqueadero: la plata (o los vehículos) día por día y por día de
la semana, de todos o solo de carros o de motos.

El dibujo y las cuentas de cada barra son los de app/caja/graficas.py (los mismos del
lavadero); aquí está lo del parqueadero: qué se puede ver y qué dice cada día.
"""

from collections.abc import Callable
from datetime import date

from app import formato
from app.caja import graficas
from app.caja.graficas import (  # noqa: F401  (lo usan las pantallas y las pruebas)
    DIAS_PARA_VER_LA_SEMANA,
    Barra,
    DiaDeLaGrafica,
    Grafica,
    Linea,
    Medida,
    tope_y_paso,
)
from app.parqueadero.balance import Balance, CifrasDelDia, DiaDelBalance
from app.parqueadero.estados import TipoParqueo

# Lo que se puede ver: toda la plata recogida, solo la de las salidas (sin las mensualidades,
# que llegan de a montones el día que se pagan y hacen ver un día como bueno sin serlo) o los
# vehículos que entraron; de todos, solo carros o solo motos. El nombre corto va en el botón y
# el largo en el título de la gráfica.
QUE_SE_VE = {"plata": "Todo lo recogido", "salidas": "Salidas", "vehiculos": "Vehículos"}
_EN_EL_TITULO = {
    "plata": "plata recogida",
    "salidas": "plata de las salidas (sin mensualidades)",
    "vehiculos": "vehículos que entraron",
}
DE_QUIEN = {"todos": "Todos", "carro": "Carros", "moto": "Motos"}


def _valor(cifras: CifrasDelDia, ver: str) -> int:
    if ver == "vehiculos":
        return cifras.entradas
    return cifras.cobrado_salidas if ver == "salidas" else cifras.total


def _que(ver: str, tipo: TipoParqueo | None) -> str:
    """Lo que muestra la gráfica, para su título: "plata recogida (carros)"."""
    if tipo is None:
        return _EN_EL_TITULO[ver]
    return f"{_EN_EL_TITULO[ver]} ({DE_QUIEN[tipo.value].lower()})"


def _que(ver: str, tipo: TipoParqueo | None) -> str:
    """Lo que muestra la gráfica, para su título: "plata recogida (carros)"."""
    if tipo is None:
        return _EN_EL_TITULO[ver]
    return f"{_EN_EL_TITULO[ver]} ({DE_QUIEN[tipo.value].lower()})"


def _medida(ver: str) -> Medida:
    if ver == "vehiculos":
        return Medida(_EN_EL_TITULO[ver], "vehículo", "vehículos")
    return Medida(_EN_EL_TITULO[ver])


def _detalle_del_dia(dia: DiaDelBalance, ver: str, tipo: TipoParqueo | None) -> str:
    cifras = dia.de(tipo)
    if ver == "vehiculos":
        if tipo is not None:
            return f"{cifras.salidas} salieron"
        carros, motos = dia.de(TipoParqueo.CARRO).entradas, dia.de(TipoParqueo.MOTO).entradas
        return (
            f"{carros} carro{'s' if carros != 1 else ''}, {motos} moto{'s' if motos != 1 else ''}"
        )
    partes = [
        f"{cifras.salidas} salida{'s' if cifras.salidas != 1 else ''}: "
        f"{formato.pesos(cifras.cobrado_salidas)}"
    ]
    if cifras.cobrado_mensualidades:
        partes.append(f"mensualidades: {formato.pesos(cifras.cobrado_mensualidades)}")
    return " · ".join(partes)


def _dias(b: Balance, ver: str, tipo: TipoParqueo | None) -> list[DiaDeLaGrafica]:
    return [
        DiaDeLaGrafica(
            dia=dia.dia,
            valor=_valor(dia.de(tipo), ver),
            detalle=_detalle_del_dia(dia, ver, tipo),
            # Con movimiento de cualquier vehículo, aunque se vean solo los carros.
            con_movimiento=bool(dia.entradas or dia.salidas or dia.total),
        )
        for dia in b.dias
    ]


def dia_por_dia(
    b: Balance,
    hoy: date,
    ver: str,
    tipo: TipoParqueo | None,
    enlace: Callable[[date], str] | None = None,
) -> Grafica:
    return graficas.dia_por_dia(_dias(b, ver, tipo), hoy, _medida(ver), _que(ver, tipo), enlace)


def por_dia_de_la_semana(
    b: Balance, hoy: date, ver: str, tipo: TipoParqueo | None
) -> Grafica | None:
    return graficas.por_dia_de_la_semana(_dias(b, ver, tipo), hoy, _medida(ver), _que(ver, tipo))
