"""El balance del parqueadero: lo que entró y lo que pasó en un día, una semana o un mes.

Solo lee. La plata cuenta el día en que se cobró, en la hora de Bogotá: las salidas por su
hora de salida y las mensualidades por la hora del pago. Los días que alguien ya traía
pagados (con el dueño anterior) no son plata de este sistema y no aparecen.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import String, func, literal, select
from sqlalchemy.orm import Session

from app import formato, tiempo
from app.parqueadero.consultas import FilaCorreccion, ResumenDeCaja, resumenes_de_cajas
from app.parqueadero.estados import EstadoParqueo, TipoParqueo
from app.parqueadero.tablas import CajaParqueadero, PagoMensualidad, Parqueo
from app.turnos.estados import MedioPago

TIPOS_DE_PERIODO = ("dia", "semana", "mes")

# Cuántos ajustes y anulaciones se muestran con su detalle (el total siempre cuenta todos).
DETALLE_MAXIMO = 50


# --- El periodo ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Periodo:
    tipo: str  # "dia", "semana" (de lunes a domingo) o "mes"
    desde: date
    hasta: date  # incluido

    @property
    def dias(self) -> list[date]:
        return [self.desde + timedelta(days=n) for n in range((self.hasta - self.desde).days + 1)]

    def anterior(self) -> "Periodo":
        return periodo(self.tipo, self.desde - timedelta(days=1))

    def siguiente(self) -> "Periodo":
        return periodo(self.tipo, self.hasta + timedelta(days=1))

    def contiene(self, dia: date) -> bool:
        return self.desde <= dia <= self.hasta

    @property
    def titulo(self) -> str:
        """ "Martes 12 de noviembre de 2026", "Semana del 9 al 15 de noviembre de 2026" o
        "Noviembre de 2026"."""
        if self.tipo == "mes":
            return f"{formato.MESES[self.desde.month - 1].capitalize()} de {self.desde.year}"
        if self.tipo == "semana":
            return f"Semana {_del_al(self.desde, self.hasta)}"
        return f"{formato.fecha_larga(self.desde)} de {self.desde.year}"

    def nombre_del_anterior(self, hoy: date) -> str:
        """Con qué se compara: "Ayer (todo el día)" si es el de hoy (que va por la mitad), o
        "El día anterior" si ya pasó."""
        if self.contiene(hoy):
            return {
                "dia": "Ayer (todo el día)",
                "semana": "La semana pasada (completa)",
                "mes": "El mes pasado (completo)",
            }[self.tipo]
        return {"dia": "El día anterior", "semana": "La semana anterior", "mes": "El mes anterior"}[
            self.tipo
        ]


def _del_al(desde: date, hasta: date) -> str:
    """ "del 9 al 15 de noviembre de 2026", "del 30 de noviembre al 6 de diciembre de 2026" """
    if desde.year != hasta.year:
        return f"del {formato.fecha(desde)} al {formato.fecha(hasta)}"
    if desde.month != hasta.month:
        return f"del {desde.day} de {formato.MESES[desde.month - 1]} al {formato.fecha(hasta)}"
    return f"del {desde.day} al {formato.fecha(hasta)}"


def periodo(tipo: str, dia: date) -> Periodo:
    """El día, la semana (de lunes a domingo) o el mes que contiene `dia`."""
    if tipo == "semana":
        lunes = dia - timedelta(days=dia.weekday())
        return Periodo("semana", lunes, lunes + timedelta(days=6))
    if tipo == "mes":
        primero = dia.replace(day=1)
        siguiente = (primero + timedelta(days=32)).replace(day=1)
        return Periodo("mes", primero, siguiente - timedelta(days=1))
    return Periodo("dia", dia, dia)


# --- Lo que muestra -----------------------------------------------------------------------


@dataclass(frozen=True)
class DiaDelBalance:
    dia: date
    entradas: int = 0
    salidas: int = 0
    cobrado_salidas: int = 0
    cobrado_mensualidades: int = 0

    @property
    def total(self) -> int:
        return self.cobrado_salidas + self.cobrado_mensualidades


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
    def correcciones(self) -> list[FilaCorreccion]:
        """Los cobros corregidos en las cajas del periodo, de la primera a la última."""
        return [fila for resumen in self.cajas for fila in resumen.correcciones]


def limites(p: Periodo) -> tuple[datetime, datetime]:
    """Desde la medianoche del primer día hasta la medianoche después del último, en Bogotá."""
    zona = tiempo.zona_horaria()
    inicio = datetime.combine(p.desde, datetime.min.time(), zona)
    fin = datetime.combine(p.hasta + timedelta(days=1), datetime.min.time(), zona)
    return inicio, fin


def _dia_de(columna: Any) -> Any:
    """El día (en Bogotá) de una fecha y hora guardada con zona horaria.

    La zona va escrita en la consulta (no como parámetro): así el SELECT y el GROUP BY
    tienen la misma expresión y PostgreSQL acepta agrupar por ella."""
    zona = literal(tiempo.zona_horaria().key, String, literal_execute=True)
    return func.date(func.timezone(zona, columna))


def balance(sesion: Session, p: Periodo) -> Balance:
    inicio, fin = limites(p)
    por_dia: dict[date, dict[str, int]] = {
        dia: {"entradas": 0, "salidas": 0, "cobrado_salidas": 0, "cobrado_mensualidades": 0}
        for dia in p.dias
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
            por_dia[dia]["salidas"] += cuantas
            por_dia[dia]["cobrado_salidas"] += int(cobrado)

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
            por_dia[dia]["entradas"] += cuantas

    pagos = 0
    cobrado_mensualidades = 0
    for medio, dia, cuantos, cobrado in sesion.execute(
        select(
            PagoMensualidad.medio_pago,
            _dia_de(PagoMensualidad.fecha_hora),
            func.count(),
            func.sum(PagoMensualidad.valor),
        )
        .where(
            PagoMensualidad.caja_id.is_not(None),
            PagoMensualidad.fecha_hora >= inicio,
            PagoMensualidad.fecha_hora < fin,
        )
        .group_by(PagoMensualidad.medio_pago, _dia_de(PagoMensualidad.fecha_hora))
    ):
        pagos += cuantos
        cobrado_mensualidades += int(cobrado)
        por_medio[medio] += int(cobrado)
        if dia in por_dia:
            por_dia[dia]["cobrado_mensualidades"] += int(cobrado)

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
    cajas = sesion.scalars(
        select(CajaParqueadero)
        .where(CajaParqueadero.abierta_en >= inicio, CajaParqueadero.abierta_en < fin)
        .order_by(CajaParqueadero.abierta_en)
    ).all()

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
        dias=[DiaDelBalance(dia, **valores) for dia, valores in por_dia.items()],
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
