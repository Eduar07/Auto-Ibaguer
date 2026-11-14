"""El balance del lavadero: lo que se cobró y lo que pasó en un día, una semana, un mes o los
días que se escojan.

Cada cobro cuenta el día en que se hizo y cada turno el día para el que se pidió (en Bogotá);
cada caja, el día en que se abrió. Los periodos y las gráficas son los mismos del parqueadero
(app/caja). Solo lee; no cambia nada.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.caja import consultas as de_la_caja
from app.caja.consultas import Correccion, SalidasDelPeriodo
from app.caja.periodos import Periodo, dia_en_bogota, limites
from app.catalogo.tablas import Fila, Servicio, TipoVehiculo
from app.clientes.tablas import Vehiculo
from app.turnos import reglas
from app.turnos.caja import NEGOCIO
from app.turnos.consultas_de_caja import ResumenDeCaja, resumenes_de_cajas
from app.turnos.estados import EstadoTurno, MedioPago, OrigenTurno
from app.turnos.tablas import Pago, Turno, TurnoHistorial

# Cuántos cobros con otro precio se muestran con su detalle (el total siempre cuenta todos).
DETALLE_MAXIMO = 50


@dataclass(frozen=True)
class DiaDelLavadero:
    dia: date
    cobrado: int = 0
    cobros: int = 0  # turnos cobrados ese día
    turnos: int = 0  # turnos pedidos para ese día


@dataclass(frozen=True)
class Renglon:
    """Lo cobrado de un servicio o de un tipo de vehículo."""

    nombre: str
    cobros: int
    cobrado: int


@dataclass(frozen=True)
class Ajuste:
    """Un turno que se cobró distinto al precio del menú, con su motivo."""

    turno: str  # "C-03"
    placa: str
    hora: datetime
    menu: int
    cobrado: int
    motivo: str


@dataclass(frozen=True)
class Balance:
    periodo: Periodo
    cobrado: int
    cobros: int
    por_medio: dict[MedioPago, int]
    por_servicio: list[Renglon]  # del que más dejó al que menos
    por_tipo: list[Renglon]
    descuentos: int  # lo que se dejó de cobrar en turnos cobrados por debajo del menú
    con_descuento: int
    recargos: int  # lo que se cobró de más (barro extra, por ejemplo)
    ajustes: list[Ajuste]  # los primeros DETALLE_MAXIMO
    cuantos_ajustes: int
    turnos: int  # pedidos para los días del periodo
    por_whatsapp: int
    en_el_local: int
    cancelados: int
    no_llegaron: int
    lavado_promedio_min: int | None  # de "pasó a puesto" a "listo", en los lavados terminados
    cajas: list[ResumenDeCaja]
    dias: list[DiaDelLavadero] = field(default_factory=list)
    # Las que se hicieron en el periodo a cajas de antes (ya cerradas): cambiaron días que ya
    # se habían revisado. Las de las cajas del periodo están en `correcciones`.
    correcciones_de_antes: list[Correccion] = field(default_factory=list)
    # La plata que salió de las cajas del periodo: gastos, retiros y consignaciones.
    salidas_de_caja: SalidasDelPeriodo = field(default_factory=lambda: SalidasDelPeriodo(gastos=[]))

    @property
    def ticket_promedio(self) -> int | None:
        return round(self.cobrado / self.cobros) if self.cobros else None

    @property
    def cajas_cerradas(self) -> list[ResumenDeCaja]:
        return [r for r in self.cajas if r.caja.cerrada_en is not None]

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
    def correcciones(self) -> list[Correccion]:
        """Lo que se corrigió en las cajas del periodo (cobros, bases y conteos), aunque se
        haya corregido después, de la primera caja a la última."""
        return [fila for resumen in self.cajas for fila in resumen.correcciones]


def _renglones(filas) -> list[Renglon]:
    return sorted(
        (
            Renglon(nombre or "Sin definir", int(cobros), int(cobrado))
            for nombre, cobros, cobrado in filas
        ),
        key=lambda r: (-r.cobrado, r.nombre),
    )


def balance(sesion: Session, p: Periodo) -> Balance:
    inicio, fin = limites(p)
    en_el_periodo = (Pago.fecha_hora >= inicio, Pago.fecha_hora < fin)
    con_tipo = (
        select()
        .select_from(Pago)
        .join(Turno, Turno.id == Pago.turno_id)
        .join(Vehiculo, Vehiculo.id == Turno.vehiculo_id)
        .join(TipoVehiculo, TipoVehiculo.id == Vehiculo.tipo_vehiculo_id)
        .outerjoin(Servicio, Servicio.id == Turno.servicio_id)
    )
    cobrado, cobros, descuentos, con_descuento, recargos, cuantos_ajustes = sesion.execute(
        select(
            func.coalesce(func.sum(Pago.precio_cobrado), 0),
            func.count(),
            func.coalesce(
                func.sum(Pago.precio_menu - Pago.precio_cobrado).filter(
                    Pago.precio_cobrado < Pago.precio_menu
                ),
                0,
            ),
            func.count().filter(Pago.precio_cobrado < Pago.precio_menu),
            func.coalesce(
                func.sum(Pago.precio_cobrado - Pago.precio_menu).filter(
                    Pago.precio_cobrado > Pago.precio_menu
                ),
                0,
            ),
            func.count().filter(Pago.precio_cobrado != Pago.precio_menu),
        ).where(*en_el_periodo)
    ).one()
    por_medio = {
        medio: int(total)
        for medio, total in sesion.execute(
            select(Pago.medio_pago, func.sum(Pago.precio_cobrado))
            .where(*en_el_periodo)
            .group_by(Pago.medio_pago)
        )
        if total
    }
    por_servicio = _renglones(
        sesion.execute(
            con_tipo.add_columns(Servicio.nombre, func.count(), func.sum(Pago.precio_cobrado))
            .where(*en_el_periodo)
            .group_by(Servicio.nombre)
        )
    )
    por_tipo = _renglones(
        sesion.execute(
            con_tipo.add_columns(TipoVehiculo.nombre, func.count(), func.sum(Pago.precio_cobrado))
            .where(*en_el_periodo)
            .group_by(TipoVehiculo.nombre)
        )
    )
    ajustes = [
        Ajuste(
            turno=reglas.numero_de_turno(prefijo, consecutivo),
            placa=placa or "—",
            hora=pago.fecha_hora,
            menu=pago.precio_menu,
            cobrado=pago.precio_cobrado,
            motivo=pago.motivo_ajuste or "",
        )
        for pago, prefijo, consecutivo, placa in sesion.execute(
            select(Pago, Fila.prefijo, Turno.consecutivo, Vehiculo.placa)
            .join(Turno, Turno.id == Pago.turno_id)
            .join(Fila, Fila.id == Turno.fila_id)
            .join(Vehiculo, Vehiculo.id == Turno.vehiculo_id)
            .where(*en_el_periodo, Pago.precio_cobrado != Pago.precio_menu)
            .order_by(Pago.fecha_hora)
            .limit(DETALLE_MAXIMO)
        )
    ]

    # Los turnos pedidos para los días del periodo: de dónde vinieron y cómo terminaron.
    turnos, por_whatsapp, cancelados, no_llegaron = sesion.execute(
        select(
            func.count(),
            func.count().filter(Turno.origen == OrigenTurno.WHATSAPP),
            func.count().filter(Turno.estado == EstadoTurno.CANCELADO),
            func.count().filter(Turno.estado == EstadoTurno.NO_LLEGO),
        ).where(Turno.fecha >= p.desde, Turno.fecha <= p.hasta)
    ).one()
    # Cuánto dura un lavado: de "pasó a puesto" a "listo", en los que terminaron en el periodo.
    lavado = sesion.scalar(
        select(func.avg(func.extract("epoch", TurnoHistorial.fecha_hora - Turno.en_lavado_desde)))
        .join(Turno, Turno.id == TurnoHistorial.turno_id)
        .where(
            TurnoHistorial.estado_anterior == EstadoTurno.EN_LAVADO,
            TurnoHistorial.estado_nuevo == EstadoTurno.LISTO,
            TurnoHistorial.fecha_hora >= inicio,
            TurnoHistorial.fecha_hora < fin,
            Turno.en_lavado_desde.is_not(None),
        )
    )

    # Día por día: lo cobrado y los turnos pedidos.
    por_dia: dict[date, dict[str, int]] = defaultdict(
        lambda: {"cobrado": 0, "cobros": 0, "turnos": 0}
    )
    dia_del_pago = dia_en_bogota(Pago.fecha_hora)
    for dia, total, cuantos in sesion.execute(
        select(dia_del_pago, func.sum(Pago.precio_cobrado), func.count())
        .where(*en_el_periodo)
        .group_by(dia_del_pago)
    ):
        por_dia[dia]["cobrado"] = int(total)
        por_dia[dia]["cobros"] = cuantos
    for dia, cuantos in sesion.execute(
        select(Turno.fecha, func.count())
        .where(Turno.fecha >= p.desde, Turno.fecha <= p.hasta)
        .group_by(Turno.fecha)
    ):
        por_dia[dia]["turnos"] = cuantos

    cajas = de_la_caja.cajas_abiertas_entre(sesion, NEGOCIO, inicio, fin)
    return Balance(
        periodo=p,
        cobrado=int(cobrado),
        cobros=cobros,
        por_medio={medio: por_medio[medio] for medio in MedioPago if medio in por_medio},
        por_servicio=por_servicio,
        por_tipo=por_tipo,
        descuentos=int(descuentos),
        con_descuento=con_descuento,
        recargos=int(recargos),
        ajustes=ajustes,
        cuantos_ajustes=cuantos_ajustes,
        turnos=turnos,
        por_whatsapp=por_whatsapp,
        en_el_local=turnos - por_whatsapp,
        cancelados=cancelados,
        no_llegaron=no_llegaron,
        lavado_promedio_min=round(float(lavado) / 60) if lavado is not None else None,
        cajas=resumenes_de_cajas(sesion, cajas),
        dias=[DiaDelLavadero(dia=dia, **por_dia.get(dia, {})) for dia in p.dias],
        correcciones_de_antes=de_la_caja.correcciones_a_cajas_de_antes(
            sesion, NEGOCIO, inicio, fin
        ),
        salidas_de_caja=de_la_caja.salidas_del_periodo(sesion, NEGOCIO, inicio, fin),
    )


def total_cobrado(sesion: Session, p: Periodo) -> int:
    """Solo el total del periodo, para comparar con el anterior."""
    inicio, fin = limites(p)
    return int(
        sesion.scalar(
            select(func.coalesce(func.sum(Pago.precio_cobrado), 0)).where(
                Pago.fecha_hora >= inicio, Pago.fecha_hora < fin
            )
        )
    )
