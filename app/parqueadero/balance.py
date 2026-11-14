"""El balance del parqueadero: lo que entró y lo que pasó en un día, una semana o un mes.

Solo lee. La plata cuenta el día en que se cobró, en la hora de Bogotá: las salidas por su
hora de salida y las mensualidades por la hora del pago. Los días que alguien ya traía
pagados (con el dueño anterior) no son plata de este sistema y no aparecen.
"""

from dataclasses import dataclass, field
from datetime import date, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.caja.consultas import SalidasDelPeriodo, cajas_abiertas_entre, salidas_del_periodo
from app.caja.periodos import (  # noqa: F401  (los periodos son de los dos negocios)
    DIAS_MAXIMOS_DEL_RANGO,
    TIPOS_DE_PERIODO,
    Periodo,
    dia_en_bogota,
    limites,
    periodo,
    rango,
)
from app.parqueadero.casos_de_uso import NEGOCIO
from app.parqueadero.consultas import (
    Correccion,
    ResumenDeCaja,
    correcciones_a_cajas_de_antes,
    resumenes_de_cajas,
)
from app.parqueadero.estados import EstadoParqueo, TipoParqueo
from app.parqueadero.tablas import Mensualidad, PagoMensualidad, Parqueo
from app.turnos.estados import MedioPago

# Cuántos ajustes y anulaciones se muestran con su detalle (el total siempre cuenta todos).
DETALLE_MAXIMO = 50


# --- Lo que muestra -----------------------------------------------------------------------


@dataclass(frozen=True)
class CifrasDelDia:
    entradas: int = 0
    salidas: int = 0
    cobrado_salidas: int = 0
    cobrado_mensualidades: int = 0

    @property
    def total(self) -> int:
        return self.cobrado_salidas + self.cobrado_mensualidades


@dataclass(frozen=True)
class DiaDelBalance(CifrasDelDia):
    """Las cifras de un día (de todos los vehículos) y, aparte, las de carros y las de motos."""

    dia: date = date.min
    por_tipo: dict[TipoParqueo, CifrasDelDia] = field(default_factory=dict)

    def de(self, tipo: TipoParqueo | None) -> CifrasDelDia:
        """Las de un tipo, o las de todos si no se dice cuál."""
        return self if tipo is None else self.por_tipo.get(tipo, CifrasDelDia())


@dataclass(frozen=True)
class Ajuste:
    """Una salida que se cobró distinto a la tarifa (con su motivo)."""

    ticket: int
    placa: str
    hora: datetime
    calculado: int
    cobrado: int
    motivo: str


@dataclass(frozen=True)
class Anulada:
    ticket: int
    placa: str
    hora: datetime
    motivo: str


@dataclass(frozen=True)
class Balance:
    periodo: Periodo
    entradas: dict[TipoParqueo, int]
    salidas: dict[TipoParqueo, int]  # vehículos que salieron (los que pagaron y mensualistas)
    cobrado_salidas: dict[TipoParqueo, int]
    mensualistas_sin_pagar: int  # salieron en $0 porque estaban al día
    pagos_mensualidad: int
    cobrado_mensualidades: int
    por_medio: dict[MedioPago, int]
    descuentos: int  # lo que se dejó de cobrar en salidas cobradas por debajo de la tarifa
    salidas_con_descuento: int
    recargos: int  # lo que se cobró de más (noche completa, por ejemplo)
    ajustes: list[Ajuste]  # los primeros DETALLE_MAXIMO
    cuantos_ajustes: int
    anuladas: list[Anulada]  # las primeras DETALLE_MAXIMO
    total_anuladas: int
    cajas: list[ResumenDeCaja]
    dias: list[DiaDelBalance] = field(default_factory=list)
    # Las que se hicieron en el periodo a cajas de antes (ya cerradas): cambiaron días que ya
    # se habían revisado. Las de las cajas del periodo están en `correcciones`.
    correcciones_de_antes: list[Correccion] = field(default_factory=list)
    # La plata que salió de las cajas del periodo: gastos, retiros y consignaciones.
    salidas_de_caja: SalidasDelPeriodo = field(default_factory=lambda: SalidasDelPeriodo(gastos=[]))

    @property
    def total_salidas(self) -> int:
        return sum(self.cobrado_salidas.values())

    @property
    def total(self) -> int:
        """Todo lo que se recogió: salidas y mensualidades, por cualquier medio."""
        return self.total_salidas + self.cobrado_mensualidades

    @property
    def cuantas_salidas(self) -> int:
        return sum(self.salidas.values())

    @property
    def cuantas_entradas(self) -> int:
        return sum(self.entradas.values())

    @property
    def cajas_cerradas(self) -> list[ResumenDeCaja]:
        return [r for r in self.cajas if r.caja.cerrada_en is not None]

    @property
    def diferencia_de_cajas(self) -> int:
        """Lo que sobró (positivo) o faltó (negativo) al cerrar las cajas del periodo, neto."""
        return sum(r.caja.diferencia or 0 for r in self.cajas_cerradas)

    @property
    def cajas_que_cuadraron(self) -> int:
        return sum(1 for r in self.cajas_cerradas if not r.caja.diferencia)

    @property
    def sobrantes(self) -> list[int]:
        """Lo que sobró en cada caja que no cuadró por sobrar. Va aparte de los faltantes: un
        sobrante de un día no tapa un faltante de otro."""
        return [r.caja.diferencia for r in self.cajas_cerradas if (r.caja.diferencia or 0) > 0]

    @property
    def faltantes(self) -> list[int]:
        return [-r.caja.diferencia for r in self.cajas_cerradas if (r.caja.diferencia or 0) < 0]

    @property
    def mayor_dia(self) -> int:
        return max((dia.total for dia in self.dias), default=0)

    @property
    def correcciones(self) -> list[Correccion]:
        """Lo que se corrigió en las cajas del periodo (cobros, bases y conteos), aunque se
        haya corregido después, de la primera caja a la última."""
        return [fila for resumen in self.cajas for fila in resumen.correcciones]


_dia_de = dia_en_bogota


_CIFRAS = ("entradas", "salidas", "cobrado_salidas", "cobrado_mensualidades")


def _dia_del_balance(dia: date, por_tipo: dict[TipoParqueo, dict[str, int]]) -> DiaDelBalance:
    totales = {cifra: sum(valores[cifra] for valores in por_tipo.values()) for cifra in _CIFRAS}
    return DiaDelBalance(
        dia=dia,
        por_tipo={tipo: CifrasDelDia(**valores) for tipo, valores in por_tipo.items()},
        **totales,
    )


def balance(sesion: Session, p: Periodo) -> Balance:
    inicio, fin = limites(p)
    # Cada cifra de cada día, por tipo de vehículo; el total del día es la suma de los tipos.
    por_dia: dict[date, dict[TipoParqueo, dict[str, int]]] = {
        dia: {tipo: dict.fromkeys(_CIFRAS, 0) for tipo in TipoParqueo} for dia in p.dias
    }
    por_medio = {medio: 0 for medio in MedioPago}

    salidas = {tipo: 0 for tipo in TipoParqueo}
    cobrado_salidas = {tipo: 0 for tipo in TipoParqueo}
    mensualistas = 0
    filas = sesion.execute(
        select(
            Parqueo.tipo,
            Parqueo.medio_pago,
            _dia_de(Parqueo.salida_en),
            func.count(),
            func.coalesce(func.sum(Parqueo.valor_cobrado), 0),
            func.count().filter(Parqueo.mensualidad_id.is_not(None), Parqueo.valor_cobrado == 0),
        )
        .where(
            Parqueo.estado == EstadoParqueo.SALIO,
            Parqueo.salida_en >= inicio,
            Parqueo.salida_en < fin,
        )
        .group_by(Parqueo.tipo, Parqueo.medio_pago, _dia_de(Parqueo.salida_en))
    )
    for tipo, medio, dia, cuantas, cobrado, de_mensualistas in filas:
        salidas[tipo] += cuantas
        cobrado_salidas[tipo] += int(cobrado)
        mensualistas += de_mensualistas
        if medio is not None:
            por_medio[medio] += int(cobrado)
        if dia in por_dia:
            por_dia[dia][tipo]["salidas"] += cuantas
            por_dia[dia][tipo]["cobrado_salidas"] += int(cobrado)

    entradas = {tipo: 0 for tipo in TipoParqueo}
    for tipo, dia, cuantas in sesion.execute(
        select(Parqueo.tipo, _dia_de(Parqueo.entrada_en), func.count())
        .where(
            Parqueo.estado != EstadoParqueo.ANULADO,
            Parqueo.entrada_en >= inicio,
            Parqueo.entrada_en < fin,
        )
        .group_by(Parqueo.tipo, _dia_de(Parqueo.entrada_en))
    ):
        entradas[tipo] += cuantas
        if dia in por_dia:
            por_dia[dia][tipo]["entradas"] += cuantas

    pagos = 0
    cobrado_mensualidades = 0
    for medio, tipo, dia, cuantos, cobrado in sesion.execute(
        select(
            PagoMensualidad.medio_pago,
            Mensualidad.tipo,
            _dia_de(PagoMensualidad.fecha_hora),
            func.count(),
            func.sum(PagoMensualidad.valor),
        )
        .join(Mensualidad, Mensualidad.id == PagoMensualidad.mensualidad_id)
        .where(
            PagoMensualidad.caja_id.is_not(None),
            PagoMensualidad.fecha_hora >= inicio,
            PagoMensualidad.fecha_hora < fin,
        )
        .group_by(PagoMensualidad.medio_pago, Mensualidad.tipo, _dia_de(PagoMensualidad.fecha_hora))
    ):
        pagos += cuantos
        cobrado_mensualidades += int(cobrado)
        por_medio[medio] += int(cobrado)
        if dia in por_dia:
            por_dia[dia][tipo]["cobrado_mensualidades"] += int(cobrado)

    ajustes = [
        Ajuste(
            parqueo.id,
            parqueo.placa,
            parqueo.salida_en,
            parqueo.valor_calculado,
            parqueo.valor_cobrado,
            parqueo.motivo_ajuste or "",
        )
        for parqueo in sesion.scalars(
            select(Parqueo)
            .where(
                Parqueo.estado == EstadoParqueo.SALIO,
                Parqueo.salida_en >= inicio,
                Parqueo.salida_en < fin,
                Parqueo.valor_cobrado != Parqueo.valor_calculado,
            )
            .order_by(Parqueo.salida_en)
        )
    ]
    anuladas_consulta = select(Parqueo).where(
        Parqueo.estado == EstadoParqueo.ANULADO,
        Parqueo.salida_en >= inicio,
        Parqueo.salida_en < fin,
    )
    total_anuladas = sesion.scalar(select(func.count()).select_from(anuladas_consulta.subquery()))
    anuladas = [
        Anulada(parqueo.id, parqueo.placa, parqueo.salida_en, parqueo.motivo_anulacion or "")
        for parqueo in sesion.scalars(
            anuladas_consulta.order_by(Parqueo.salida_en).limit(DETALLE_MAXIMO)
        )
    ]
    # Cada caja es del día en que se abrió: la que se cierra pasada la medianoche sigue siendo
    # la del día anterior, y así cada caja sale en un solo día, una sola semana y un solo mes.
    cajas = cajas_abiertas_entre(sesion, NEGOCIO, inicio, fin)

    return Balance(
        periodo=p,
        entradas=entradas,
        salidas=salidas,
        cobrado_salidas=cobrado_salidas,
        mensualistas_sin_pagar=mensualistas,
        pagos_mensualidad=pagos,
        cobrado_mensualidades=cobrado_mensualidades,
        por_medio=por_medio,
        descuentos=sum(max(0, a.calculado - a.cobrado) for a in ajustes),
        salidas_con_descuento=sum(1 for a in ajustes if a.cobrado < a.calculado),
        recargos=sum(max(0, a.cobrado - a.calculado) for a in ajustes),
        ajustes=ajustes[:DETALLE_MAXIMO],
        cuantos_ajustes=len(ajustes),
        anuladas=anuladas,
        total_anuladas=total_anuladas or 0,
        cajas=resumenes_de_cajas(sesion, cajas),
        dias=[_dia_del_balance(dia, por_tipo) for dia, por_tipo in por_dia.items()],
        correcciones_de_antes=correcciones_a_cajas_de_antes(sesion, inicio, fin),
        salidas_de_caja=salidas_del_periodo(sesion, NEGOCIO, inicio, fin),
    )


def total_recogido(sesion: Session, p: Periodo) -> int:
    """Solo el total del periodo (salidas y mensualidades), para comparar con el anterior."""
    inicio, fin = limites(p)
    salidas = sesion.scalar(
        select(func.coalesce(func.sum(Parqueo.valor_cobrado), 0)).where(
            Parqueo.estado == EstadoParqueo.SALIO,
            Parqueo.salida_en >= inicio,
            Parqueo.salida_en < fin,
        )
    )
    mensualidades = sesion.scalar(
        select(func.coalesce(func.sum(PagoMensualidad.valor), 0)).where(
            PagoMensualidad.caja_id.is_not(None),
            PagoMensualidad.fecha_hora >= inicio,
            PagoMensualidad.fecha_hora < fin,
        )
    )
    return int(salidas or 0) + int(mensualidades or 0)
