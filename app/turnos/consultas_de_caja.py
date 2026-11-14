"""Lo que muestran las pantallas de la caja del lavadero: su cuadre, el cobro de cada turno, sus
correcciones, los cierres anteriores y el archivo para Excel.

Solo lee; no cambia nada (eso vive en app/turnos/caja.py y en los casos de uso de los turnos).
"""

from collections import defaultdict
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app import formato, tiempo
from app.caja import casos_de_uso as cajas_de_uso
from app.caja import consultas as de_la_caja
from app.caja.consultas import Cierres, Correccion, FiltroDeCierres
from app.caja.tablas import Caja
from app.catalogo.tablas import Fila, Servicio, TipoVehiculo
from app.clientes.tablas import Vehiculo
from app.turnos import caja as caja_del_lavadero
from app.turnos import reglas
from app.turnos.caja import NEGOCIO, Cuadre
from app.turnos.estados import MedioPago
from app.turnos.tablas import Pago, Turno


@dataclass(frozen=True)
class CobroDeCaja:
    """Una línea de la caja del lavadero: el cobro de un turno."""

    momento: datetime
    concepto: str  # "C-03 · KLM482"
    detalle: str  # "Básico · Automóvil"
    medio: MedioPago
    valor: int
    ajuste: str | None
    turno_id: int

    @property
    def para_corregir(self) -> str:
        """Dónde se abre el diálogo para corregir este cobro."""
        return f"/turnos/{self.turno_id}/corregir-cobro"

    @property
    def para_imprimir(self) -> str:
        """El recibo del cobro, para volver a imprimirlo."""
        return f"/turnos/{self.turno_id}/recibo"


def _cobros(sesion: Session, *condiciones, orden: tuple = ()) -> list[tuple]:
    """Los pagos con su turno, su número, su placa, su servicio y su tipo de vehículo."""
    return list(
        sesion.execute(
            select(Pago, Turno, Fila.prefijo, Vehiculo.placa, Servicio.nombre, TipoVehiculo.nombre)
            .join(Turno, Turno.id == Pago.turno_id)
            .join(Fila, Fila.id == Turno.fila_id)
            .join(Vehiculo, Vehiculo.id == Turno.vehiculo_id)
            .join(TipoVehiculo, TipoVehiculo.id == Vehiculo.tipo_vehiculo_id)
            .outerjoin(Servicio, Servicio.id == Turno.servicio_id)
            .where(*condiciones)
            .order_by(*(orden or (Pago.fecha_hora, Pago.id)))
        ).all()
    )


def cobros_de_caja(sesion: Session, caja_id: int) -> list[CobroDeCaja]:
    """Lo que entró a esa caja, del cobro más nuevo al más viejo."""
    return [
        CobroDeCaja(
            momento=pago.fecha_hora,
            concepto=f"{reglas.numero_de_turno(prefijo, turno.consecutivo)} · {placa or '—'}",
            detalle=f"{servicio or 'Sin servicio'} · {tipo}",
            medio=pago.medio_pago,
            valor=pago.precio_cobrado,
            ajuste=(
                f"Menú {formato.pesos(pago.precio_menu)}: {pago.motivo_ajuste}"
                if pago.motivo_ajuste
                else None
            ),
            turno_id=turno.id,
        )
        for pago, turno, prefijo, placa, servicio, tipo in _cobros(
            sesion, Pago.caja_id == caja_id, orden=(Pago.fecha_hora.desc(), Pago.id.desc())
        )
    ]


@dataclass(frozen=True)
class ResumenDeCaja(de_la_caja.DatosDelCierre):
    caja: Caja
    cuadre: Cuadre
    cobros: int  # turnos cobrados en esta caja
    abrio: str
    cerro: str | None
    # De cobros y de la caja misma (base o conteo), de la primera a la última.
    correcciones: list[Correccion] = field(default_factory=list)

    # Cómo se muestra en la pantalla de la caja (la misma para los dos negocios): todo lo que
    # entra es el cobro de un turno, así que basta el total por medio de pago.
    columnas = ("Turnos",)

    @property
    def cifras(self) -> list[tuple[int, str]]:
        return [(self.cobros, "turno cobrado" if self.cobros == 1 else "turnos cobrados")]

    @property
    def lineas(self) -> list[tuple[MedioPago, tuple[int, ...], int]]:
        return [
            (medio, (total,), total)
            for medio in MedioPago
            if (total := self.cuadre.por_medio(medio))
        ]


def resumen_de_caja(sesion: Session, caja: Caja) -> ResumenDeCaja:
    return resumenes_de_cajas(sesion, [caja])[0]


def resumenes_de_cajas(sesion: Session, cajas: Sequence[Caja]) -> list[ResumenDeCaja]:
    """El cuadre de varias cajas con unas pocas consultas para todas (no varias por caja)."""
    if not cajas:
        return []
    ids = [caja.id for caja in cajas]
    por_medio: dict[int, list[tuple[MedioPago, int]]] = defaultdict(list)
    cuantos: dict[int, int] = defaultdict(int)
    for caja_id, medio, total, cobros in sesion.execute(
        select(Pago.caja_id, Pago.medio_pago, func.sum(Pago.precio_cobrado), func.count())
        .where(Pago.caja_id.in_(ids))
        .group_by(Pago.caja_id, Pago.medio_pago)
    ):
        if total:
            por_medio[caja_id].append((medio, int(total)))
        cuantos[caja_id] += cobros
    correcciones, nombres = de_la_caja.correcciones_de_las_cajas(sesion, cajas)
    salidas = cajas_de_uso.salidas_por_caja(sesion, ids)
    return [
        ResumenDeCaja(
            caja=caja,
            cuadre=caja_del_lavadero.cuadre(caja.base, por_medio[caja.id], salidas.get(caja.id, 0)),
            cobros=cuantos[caja.id],
            abrio=nombres.get(caja.abierta_por, "—"),
            cerro=nombres.get(caja.cerrada_por, "—") if caja.cerrada_en else None,
            correcciones=correcciones[caja.id],
        )
        for caja in cajas
    ]


# --- Los cierres anteriores, con filtros (app.caja.consultas) -------------------------------


def _lo_que_entro(sesion: Session, ids: Select) -> int:
    return int(
        sesion.scalar(
            select(func.coalesce(func.sum(Pago.precio_cobrado), 0)).where(Pago.caja_id.in_(ids))
        )
    )


def cierres(sesion: Session, filtro: FiltroDeCierres, pagina: int = 1) -> Cierres[ResumenDeCaja]:
    """Los cierres de la caja del lavadero que cumplen el filtro, de a 30 por página."""
    return de_la_caja.cierres(
        sesion, NEGOCIO, filtro, pagina, entro=_lo_que_entro, resumir=resumenes_de_cajas
    )


def todos_los_cierres(sesion: Session, filtro: FiltroDeCierres) -> list[ResumenDeCaja]:
    return de_la_caja.todos_los_cierres(sesion, NEGOCIO, filtro, resumir=resumenes_de_cajas)


def quienes_cerraron(sesion: Session) -> list[tuple[int, str]]:
    return de_la_caja.quienes_cerraron(sesion, NEGOCIO)


# --- Para Excel ------------------------------------------------------------------------------


def lineas_para_excel(sesion: Session, desde: date, hasta: date) -> Iterator[tuple[str, ...]]:
    """Cada turno cobrado entre dos días (hora de Bogotá), una línea por cobro, para abrir en
    Excel y llevar las cuentas del lavadero: con el precio del menú y lo que se cobró, así se
    ven los descuentos."""
    zona = tiempo.zona_horaria()
    inicio = datetime.combine(desde, datetime.min.time(), zona)
    fin = datetime.combine(hasta + timedelta(days=1), datetime.min.time(), zona)
    yield (
        "Fecha", "Hora", "Turno", "Placa", "Tipo de vehículo", "Servicio", "Precio del menú",
        "Cobrado", "Descuento", "Medio de pago", "Motivo del ajuste", "Caja",
    )  # fmt: skip
    for pago, turno, prefijo, placa, servicio, tipo in _cobros(
        sesion, Pago.fecha_hora >= inicio, Pago.fecha_hora < fin
    ):
        local = tiempo.hora_local(pago.fecha_hora)
        yield (
            local.date().isoformat(),
            formato.hora(local),
            reglas.numero_de_turno(prefijo, turno.consecutivo),
            placa or "",
            tipo,
            servicio or "",
            str(pago.precio_menu),
            str(pago.precio_cobrado),
            str(max(pago.precio_menu - pago.precio_cobrado, 0)),
            pago.medio_pago.value.capitalize(),
            pago.motivo_ajuste or "",
            str(pago.caja_id or ""),
        )
