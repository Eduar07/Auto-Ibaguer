"""Las gráficas del Balance: la plata (o los vehículos) día por día y por día de la semana.

Aquí se calcula lo que dice cada barra: su valor, qué tan alta va (en % del alto de la
gráfica), si es el mejor día o el más bajo, y las líneas del fondo. La plantilla las pinta
como columnas de HTML, no como un dibujo: así la letra queda del tamaño de la página en el
celular y en el computador, y no hace falta una librería de gráficas.

Solo lee lo que ya calculó el balance: no hace consultas.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from math import ceil, floor, log10

from app import formato
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

# Con menos días completos que estos, el promedio por día de la semana no dice nada (una
# sola muestra de cada día): esa gráfica no sale.
DIAS_PARA_VER_LA_SEMANA = 14

# Las líneas del fondo: cuatro, sin contar la del cero.
LINEAS = 4

_DIAS = formato.DIAS


@dataclass(frozen=True)
class Barra:
    nombre: str  # lo que dice el aviso al pasar el puntero: "Lunes 3 de noviembre"
    etiqueta: str | None  # debajo de la columna; None si no cabe (lo dice el aviso)
    valor: int
    texto: str  # "$380.000" o "12 vehículos"
    detalle: str  # "15 salidas · $300.000 · mensualidades $80.000"
    alto: float  # % del alto de la gráfica, de 0 a 100
    marca: str | None = None  # "Mejor día" o "Más bajo": lleva su valor encima
    lado: str = "centro"  # dónde va el valor encima: "inicio", "centro" o "fin" (en las orillas)
    en_curso: bool = False  # hoy, que va por la mitad: no cuenta para mejor, bajo ni promedio
    enlace: str | None = None  # a dónde lleva tocarla (el Balance de ese día)


@dataclass(frozen=True)
class Linea:
    alto: float  # % del alto de la gráfica
    texto: str


@dataclass(frozen=True)
class Grafica:
    titulo: str
    barras: list[Barra]
    rejilla: list[Linea]  # de abajo (el cero) hacia arriba
    promedio: Linea | None
    resumen: str  # en una frase: debajo de la gráfica y para el lector de pantalla


# --- Los números ------------------------------------------------------------------------


def _valor(cifras: CifrasDelDia, ver: str) -> int:
    if ver == "vehiculos":
        return cifras.entradas
    return cifras.cobrado_salidas if ver == "salidas" else cifras.total


def _que(ver: str, tipo: TipoParqueo | None) -> str:
    """Lo que muestra la gráfica, para su título: "plata recogida (carros)"."""
    if tipo is None:
        return _EN_EL_TITULO[ver]
    return f"{_EN_EL_TITULO[ver]} ({DE_QUIEN[tipo.value].lower()})"


def _primer_dia_con_datos(dias: list[DiaDelBalance]) -> date | None:
    """El primer día con algún movimiento (de cualquier vehículo). Los de antes no son días
    flojos: el sistema todavía no se usaba, y no cuentan para comparar."""
    for dia in dias:
        if dia.entradas or dia.salidas or dia.total:
            return dia.dia
    return None


def _texto(valor: int, ver: str) -> str:
    if ver == "vehiculos":
        return f"{valor} vehículo{'s' if valor != 1 else ''}"
    return formato.pesos(valor)


def _corto(valor: int, ver: str) -> str:
    """Para el eje: "$0", "$50 mil", "$1,5 M" o, en vehículos, el número."""
    if ver == "vehiculos" or valor == 0:
        return str(valor) if ver == "vehiculos" else "$0"
    if valor >= 1_000_000:
        millones = f"{valor / 1_000_000:.1f}".rstrip("0").rstrip(".").replace(".", ",")
        return f"${millones} M"
    if valor >= 1000:
        if valor % 1000 == 0:
            return f"${valor // 1000} mil"
        return f"${valor / 1000:.1f} mil".replace(".", ",")
    return formato.pesos(valor)


def tope_y_paso(maximo: int) -> tuple[int, int]:
    """Hasta dónde llega el eje (un número redondo, al menos `maximo`) y cada cuánto va una
    línea, para que salgan a lo sumo cuatro: 380.000 → hasta 400.000, de 100.000 en 100.000."""
    if maximo <= 0:
        return LINEAS, 1
    crudo = maximo / LINEAS
    potencia = 10 ** floor(log10(crudo))
    paso = 10 * potencia
    for factor in (1, 2, 2.5, 5):
        candidato = factor * potencia
        # Los pasos con decimales no sirven: no hay medio vehículo ni medio peso.
        if candidato >= crudo and candidato == int(candidato):
            paso = candidato
            break
    paso = max(1, int(paso))
    return ceil(maximo / paso) * paso, paso


def _rejilla(tope: int, paso: int, ver: str) -> list[Linea]:
    return [Linea(alto=100 * v / tope, texto=_corto(v, ver)) for v in range(0, tope + 1, paso)]


def _lado(indice: int, cuantas: int) -> str:
    """El valor encima de una barra de la orilla se corre hacia adentro: no se sale."""
    if cuantas <= 1:
        return "centro"
    if indice < cuantas * 0.15:
        return "inicio"
    if indice >= cuantas * 0.85:
        return "fin"
    return "centro"


def _mejor_y_mas_bajo(valores: list[tuple[int, int]]) -> tuple[int | None, int | None]:
    """Los índices del mayor y del menor entre (índice, valor) que cuentan. Si todos valen lo
    mismo, no hay ni mejor ni más bajo."""
    if len(valores) < 2:
        return None, None
    mayor = max(valores, key=lambda par: par[1])
    menor = min(valores, key=lambda par: par[1])
    if mayor[1] == menor[1]:
        return None, None
    return mayor[0], menor[0]


# --- Día por día ------------------------------------------------------------------------


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


def _etiquetas_de_los_dias(dias: list[date], hoy: date) -> list[str | None]:
    """Lo que va debajo de cada columna, si cabe: "Lun 3" en una semana. En un mes o un rango,
    el mes donde empieza ("1 nov", y el primer día) y, entre esos, el número del día cada
    tanto, sin pegarse a los del mes ni a "Hoy" (se encimaría la letra)."""
    if len(dias) <= 8:
        return [
            "Hoy" if dia == hoy else f"{_DIAS[dia.weekday()][:3].capitalize()} {dia.day}"
            for dia in dias
        ]
    cada = ceil(len(dias) / 10)

    def lejos(i: int, de: set[int]) -> bool:
        return all(abs(i - j) * 2 >= cada for j in de)

    # Primero "Hoy", después cada cambio de mes y al final el primer día: una que quede pegada
    # a otra más importante no va.
    candidatas = [i for i, dia in enumerate(dias) if dia == hoy]
    candidatas += [i for i, dia in enumerate(dias) if dia.day == 1 and dia != hoy]
    candidatas.append(0)
    fijas: set[int] = set()
    for i in candidatas:
        if i not in fijas and lejos(i, fijas):
            fijas.add(i)
    etiquetas: list[str | None] = []
    for i, dia in enumerate(dias):
        if i in fijas:
            etiquetas.append(
                "Hoy" if dia == hoy else f"{dia.day} {formato.MESES[dia.month - 1][:3]}"
            )
        elif i % cada == 0 and lejos(i, fijas):
            etiquetas.append(str(dia.day))
        else:
            etiquetas.append(None)
    return etiquetas


def dia_por_dia(
    b: Balance,
    hoy: date,
    ver: str,
    tipo: TipoParqueo | None,
    enlace: Callable[[date], str] | None = None,
) -> Grafica:
    """Una barra por día, hasta hoy. Hoy va más clara (el día va por la mitad) y no cuenta
    para el mejor día, el más bajo ni el promedio; tampoco los días antes del primero con
    movimiento (el sistema todavía no se usaba)."""
    dias = [dia for dia in b.dias if dia.dia <= hoy]
    valores = [_valor(dia.de(tipo), ver) for dia in dias]
    empieza = _primer_dia_con_datos(dias)
    completos = [
        (i, v)
        for i, (dia, v) in enumerate(zip(dias, valores, strict=True))
        if empieza is not None and empieza <= dia.dia < hoy
    ]
    mejor, bajo = _mejor_y_mas_bajo(completos)
    promedio = round(sum(v for _, v in completos) / len(completos)) if len(completos) >= 2 else None
    tope, paso = tope_y_paso(max(valores, default=0))
    etiquetas = _etiquetas_de_los_dias([dia.dia for dia in dias], hoy)
    barras = [
        Barra(
            nombre=formato.fecha_larga(dia.dia) + (" (va hasta ahora)" if dia.dia == hoy else ""),
            etiqueta=etiquetas[i],
            valor=valor,
            texto=_texto(valor, ver),
            detalle=_detalle_del_dia(dia, ver, tipo),
            alto=100 * valor / tope,
            marca="Mejor día" if i == mejor else ("Más bajo" if i == bajo else None),
            lado=_lado(i, len(dias)),
            en_curso=dia.dia == hoy,
            enlace=enlace(dia.dia) if enlace else None,
        )
        for i, (dia, valor) in enumerate(zip(dias, valores, strict=True))
    ]
    partes = []
    if mejor is not None and bajo is not None:
        partes.append(f"Mejor día: {_dia_en_frase(dias[mejor].dia)}, {barras[mejor].texto}")
        partes.append(f"más bajo: {_dia_en_frase(dias[bajo].dia)}, {barras[bajo].texto}")
    if promedio is not None:
        partes.append(f"promedio: {_texto(promedio, ver)} por día")
    if partes and empieza is not None and empieza > dias[0].dia:
        partes.append(f"cuenta desde el {formato.fecha(empieza)}, el primer día con movimiento")
    resumen = (
        ". ".join(_mayuscula(p) for p in partes) + "."
        if partes
        else "Todavía no hay días completos para comparar."
    )
    return Grafica(
        titulo=f"Día por día: {_que(ver, tipo)}",
        barras=barras,
        rejilla=_rejilla(tope, paso, ver),
        promedio=Linea(100 * promedio / tope, f"Promedio {_texto(promedio, ver)}")
        if promedio is not None
        else None,
        resumen=resumen,
    )


def _dia_en_frase(dia: date) -> str:
    """ "el lunes 3 de noviembre" """
    return f"el {formato.fecha_larga(dia)[0].lower()}{formato.fecha_larga(dia)[1:]}"


def _mayuscula(texto: str) -> str:
    return texto[:1].upper() + texto[1:]


# --- Por día de la semana -----------------------------------------------------------------


def _dias_que_son(nombre: str, cuantos: int) -> str:
    """ "lunes" no cambia; "sábado" → "sábados" (si son varios)."""
    return nombre if cuantos == 1 or nombre.endswith("s") else f"{nombre}s"


def por_dia_de_la_semana(
    b: Balance, hoy: date, ver: str, tipo: TipoParqueo | None
) -> Grafica | None:
    """El promedio de cada día de la semana (los lunes, los martes...), con los días ya
    completos: para saber cuáles son los días flojos y cuáles los buenos. Con menos de dos
    semanas de días completos (contando desde el primero con movimiento) no sale: un solo
    lunes no es un promedio."""
    empieza = _primer_dia_con_datos([dia for dia in b.dias if dia.dia <= hoy])
    completos = [dia for dia in b.dias if empieza is not None and empieza <= dia.dia < hoy]
    if len(completos) < DIAS_PARA_VER_LA_SEMANA:
        return None
    grupos: list[list[int]] = [[] for _ in _DIAS]
    for dia in completos:
        grupos[dia.dia.weekday()].append(_valor(dia.de(tipo), ver))
    promedios = [round(sum(g) / len(g)) if g else 0 for g in grupos]
    con_datos = [(i, v) for i, v in enumerate(promedios) if grupos[i]]
    mejor, bajo = _mejor_y_mas_bajo(con_datos)
    tope, paso = tope_y_paso(max(promedios, default=0))
    general = round(sum(_valor(d.de(tipo), ver) for d in completos) / len(completos))
    barras = [
        Barra(
            nombre=f"Los {_dias_que_son(nombre, 2)}",
            etiqueta=nombre[:3].capitalize(),
            valor=valor,
            texto=_texto(valor, ver),
            detalle=f"Promedio de {len(grupos[i])} {_dias_que_son(nombre, len(grupos[i]))}",
            alto=100 * valor / tope,
            marca="El mejor" if i == mejor else ("El más bajo" if i == bajo else None),
            lado=_lado(i, len(_DIAS)),
        )
        for i, (nombre, valor) in enumerate(zip(_DIAS, promedios, strict=True))
    ]
    resumen = (
        f"En promedio, el mejor día es el {_DIAS[mejor]} ({barras[mejor].texto}) y el más "
        f"bajo, el {_DIAS[bajo]} ({barras[bajo].texto})."
        if mejor is not None and bajo is not None
        else "Todos los días de la semana van parecido."
    )
    return Grafica(
        titulo=f"Promedio por día de la semana: {_que(ver, tipo)}",
        barras=barras,
        rejilla=_rejilla(tope, paso, ver),
        promedio=Linea(100 * general / tope, f"Promedio de todos {_texto(general, ver)}"),
        resumen=resumen,
    )
