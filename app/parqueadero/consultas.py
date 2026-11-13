"""Lo que muestran las pantallas del parqueadero: quién está adentro, la caja y las mensualidades.

Solo lee; no cambia nada (eso vive en casos_de_uso).
"""

from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import formato, tiempo
from app.parqueadero import casos_de_uso as casos
from app.parqueadero import reglas
from app.parqueadero.estados import NOMBRE_DEL_TIPO, EstadoParqueo, TipoParqueo
from app.parqueadero.tablas import CajaParqueadero, Mensualidad, PagoMensualidad, Parqueo
from app.turnos.estados import MedioPago
from app.web.tablas import Administrador


@dataclass(frozen=True)
class VehiculoAdentro:
    id: int
    placa: str
    tipo: TipoParqueo
    entrada_en: datetime
    minutos: int
    valor_a_la_fecha: int
    mensualista_hasta: date | None
    observaciones: str | None
    mensualidad_vencida: bool = False  # tiene mensualidad, pero hoy no está pagada


def adentro(sesion: Session, ahora: datetime | None = None) -> list[VehiculoAdentro]:
    """Los vehículos que están adentro, del que lleva más tiempo al más nuevo, con lo que
    pagarían si salieran ahora."""
    ahora = ahora or tiempo.ahora()
    parqueos = sesion.scalars(
        select(Parqueo)
        .where(Parqueo.estado == EstadoParqueo.ADENTRO)
        .order_by(Parqueo.entrada_en, Parqueo.id)
    ).all()
    vehiculos = []
    for parqueo in parqueos:
        liquidacion = casos.liquidar(sesion, parqueo, ahora)
        vehiculos.append(
            VehiculoAdentro(
                id=parqueo.id,
                placa=parqueo.placa,
                tipo=parqueo.tipo,
                entrada_en=parqueo.entrada_en,
                minutos=liquidacion.cobro.minutos,
                valor_a_la_fecha=liquidacion.valor,
                mensualista_hasta=liquidacion.al_dia_hasta,
                observaciones=parqueo.observaciones,
                mensualidad_vencida=liquidacion.debe_la_mensualidad,
            )
        )
    return vehiculos


def contar_por_tipo(vehiculos: list[VehiculoAdentro]) -> dict[TipoParqueo, int]:
    return {tipo: sum(1 for v in vehiculos if v.tipo is tipo) for tipo in TipoParqueo}


# --- Caja -------------------------------------------------------------------------------


@dataclass(frozen=True)
class MovimientoDeCaja:
    """Una línea de la caja: una salida cobrada o un pago de mensualidad."""

    momento: datetime
    concepto: str  # "Salida ABC123 · ticket 12" o "Mensualidad ABC123 · Ana Ruiz"
    detalle: str  # "2 h 05 min" o "del 10 de noviembre al 9 de diciembre"
    medio: MedioPago | None
    valor: int
    ajuste: str | None = None


def movimientos_de_caja(sesion: Session, caja_id: int) -> list[MovimientoDeCaja]:
    salidas = sesion.scalars(
        select(Parqueo)
        .where(Parqueo.caja_id == caja_id, Parqueo.estado == EstadoParqueo.SALIO)
        .order_by(Parqueo.salida_en)
    ).all()
    pagos = sesion.execute(
        select(PagoMensualidad, Mensualidad)
        .join(Mensualidad, Mensualidad.id == PagoMensualidad.mensualidad_id)
        .where(PagoMensualidad.caja_id == caja_id)
        .order_by(PagoMensualidad.fecha_hora)
    ).all()
    lineas = [
        MovimientoDeCaja(
            momento=parqueo.salida_en,
            concepto=f"Salida {parqueo.placa} · ticket {parqueo.id}",
            detalle=(
                "Mensualista" if parqueo.mensualidad_id else formato.duracion(parqueo.minutos or 0)
            ),
            medio=parqueo.medio_pago,
            valor=parqueo.valor_cobrado or 0,
            ajuste=parqueo.motivo_ajuste,
        )
        for parqueo in salidas
    ]
    lineas += [
        MovimientoDeCaja(
            momento=pago.fecha_hora,
            concepto=f"Mensualidad {mensualidad.placa} · {mensualidad.titular}",
            detalle=f"del {formato.fecha(pago.desde)} al {formato.fecha(pago.hasta)}",
            medio=pago.medio_pago,
            valor=pago.valor,
        )
        for pago, mensualidad in pagos
    ]
    return sorted(lineas, key=lambda linea: linea.momento, reverse=True)


@dataclass(frozen=True)
class ResumenDeCaja:
    caja: CajaParqueadero
    cuadre: reglas.Cuadre
    salidas: int  # vehículos que salieron con esta caja abierta (cobrados o mensualistas)
    mensualidades: int  # pagos de mensualidad cobrados en esta caja
    abrio: str
    cerro: str | None


def _nombre(sesion: Session, administrador_id: int | None) -> str:
    if administrador_id is None:
        return "—"
    administrador = sesion.get(Administrador, administrador_id)
    return administrador.usuario if administrador else "—"


def resumen_de_caja(sesion: Session, caja: CajaParqueadero) -> ResumenDeCaja:
    salidas = sesion.scalar(
        select(func.count())
        .select_from(Parqueo)
        .where(Parqueo.caja_id == caja.id, Parqueo.estado == EstadoParqueo.SALIO)
    )
    mensualidades = sesion.scalar(
        select(func.count()).select_from(PagoMensualidad).where(PagoMensualidad.caja_id == caja.id)
    )
    return ResumenDeCaja(
        caja=caja,
        cuadre=casos.cuadre_de_caja(sesion, caja),
        salidas=salidas or 0,
        mensualidades=mensualidades or 0,
        abrio=_nombre(sesion, caja.abierta_por),
        cerro=_nombre(sesion, caja.cerrada_por) if caja.cerrada_en else None,
    )


def cajas_cerradas(sesion: Session, limite: int = 30) -> list[ResumenDeCaja]:
    cajas = sesion.scalars(
        select(CajaParqueadero)
        .where(CajaParqueadero.cerrada_en.is_not(None))
        .order_by(CajaParqueadero.cerrada_en.desc())
        .limit(limite)
    ).all()
    return [resumen_de_caja(sesion, caja) for caja in cajas]


# --- Mensualidades ----------------------------------------------------------------------


@dataclass(frozen=True)
class FilaMensualidad:
    mensualidad: Mensualidad
    # Al día: hasta dónde va lo pagado. Vencida: el último día que pagó.
    pagada_hasta: date | None
    estado: str  # "al_dia", "vence_pronto", "vencida", "sin_pagos"
    dias_restantes: int | None  # negativo: hace cuántos días venció

    @property
    def por_cobrar(self) -> bool:
        return self.estado in ("vencida", "sin_pagos")

    @property
    def debe_desde(self) -> date | None:
        """Vencida: el primer día que no ha pagado."""
        if self.estado != "vencida" or self.pagada_hasta is None:
            return None
        return self.pagada_hasta + timedelta(days=1)

    @property
    def se_puede_cobrar(self) -> bool:
        """Al día por más de 3 días: el mes siguiente todavía no se cobra (sería adelantarlo)."""
        return self.estado != "al_dia"

    @property
    def cobrar_desde(self) -> date | None:
        """Al día: desde qué día se le puede cobrar el mes siguiente."""
        if self.estado != "al_dia" or self.pagada_hasta is None:
            return None
        return reglas.cobrar_desde(self.pagada_hasta)


def _fila(
    mensualidad: Mensualidad, periodos: list[tuple[date, date]], hoy: date
) -> FilaMensualidad:
    if not periodos:
        return FilaMensualidad(mensualidad, None, "sin_pagos", None)
    al_dia = reglas.cubierta_hasta(periodos, hoy)
    if al_dia is None:
        pasados = [fin for _, fin in periodos if fin < hoy]
        ultimo = max(pasados) if pasados else None
        dias = (ultimo - hoy).days if ultimo is not None else None
        return FilaMensualidad(mensualidad, ultimo, "vencida", dias)
    estado = "vence_pronto" if reglas.se_puede_cobrar(al_dia, hoy) else "al_dia"
    return FilaMensualidad(mensualidad, al_dia, estado, (al_dia - hoy).days)


def mensualidades(
    sesion: Session, ahora: datetime | None = None, *, incluir_de_baja: bool = False
) -> list[FilaMensualidad]:
    """Las mensualidades, primero las que hay que cobrar (vencidas y sin pagos)."""
    hoy = tiempo.hora_local(ahora or tiempo.ahora()).date()
    consulta = select(Mensualidad).order_by(Mensualidad.placa)
    if not incluir_de_baja:
        consulta = consulta.where(Mensualidad.activa)
    lista = sesion.scalars(consulta).all()
    # Todos los pagos en una sola consulta, no dos por mensualidad: la lista se pide seguido.
    periodos: dict[int, list[tuple[date, date]]] = defaultdict(list)
    if lista:
        pagos = sesion.execute(
            select(
                PagoMensualidad.mensualidad_id, PagoMensualidad.desde, PagoMensualidad.hasta
            ).where(PagoMensualidad.mensualidad_id.in_([m.id for m in lista]))
        )
        for mensualidad_id, desde, hasta in pagos:
            periodos[mensualidad_id].append((desde, hasta))
    filas = [_fila(mensualidad, periodos[mensualidad.id], hoy) for mensualidad in lista]
    orden = {"vencida": 0, "sin_pagos": 1, "vence_pronto": 2, "al_dia": 3}
    return sorted(filas, key=lambda fila: (orden[fila.estado], fila.mensualidad.placa))


def pagos_de_mensualidad(sesion: Session, mensualidad_id: int) -> list[PagoMensualidad]:
    return list(
        sesion.scalars(
            select(PagoMensualidad)
            .where(PagoMensualidad.mensualidad_id == mensualidad_id)
            .order_by(PagoMensualidad.desde.desc())
        )
    )


# --- Exportar a Excel -------------------------------------------------------------------


def lineas_para_excel(sesion: Session, desde: date, hasta: date) -> Iterator[tuple[str, ...]]:
    """Todo lo que entró a las cajas del parqueadero entre dos días (hora de Bogotá), una
    línea por cobro, para abrir en Excel y llevar las cuentas."""
    zona = tiempo.zona_horaria()
    inicio = datetime.combine(desde, datetime.min.time(), zona)
    fin = datetime.combine(hasta + timedelta(days=1), datetime.min.time(), zona)
    yield (
        "Fecha", "Hora", "Concepto", "Placa", "Tipo", "Tiempo", "Medio de pago", "Valor",
        "Ajuste", "Caja",
    )  # fmt: skip
    salidas = sesion.scalars(
        select(Parqueo)
        .where(
            Parqueo.estado == EstadoParqueo.SALIO,
            Parqueo.salida_en >= inicio,
            Parqueo.salida_en < fin,
        )
        .order_by(Parqueo.salida_en)
    ).all()
    pagos = sesion.execute(
        select(PagoMensualidad, Mensualidad)
        .join(Mensualidad, Mensualidad.id == PagoMensualidad.mensualidad_id)
        .where(
            PagoMensualidad.fecha_hora >= inicio,
            PagoMensualidad.fecha_hora < fin,
            # Los días que ya traía pagados no son plata que entró a una caja.
            PagoMensualidad.caja_id.is_not(None),
        )
        .order_by(PagoMensualidad.fecha_hora)
    ).all()
    lineas = []
    for parqueo in salidas:
        local = tiempo.hora_local(parqueo.salida_en)
        lineas.append(
            (
                local,
                (
                    local.date().isoformat(),
                    formato.hora(local),
                    f"Salida · ticket {parqueo.id}"
                    + (" · mensualista" if parqueo.mensualidad_id else ""),
                    parqueo.placa,
                    NOMBRE_DEL_TIPO[parqueo.tipo],
                    formato.duracion(parqueo.minutos or 0),
                    parqueo.medio_pago.value.capitalize() if parqueo.medio_pago else "",
                    str(parqueo.valor_cobrado or 0),
                    parqueo.motivo_ajuste or "",
                    str(parqueo.caja_id or ""),
                ),
            )
        )
    for pago, mensualidad in pagos:
        local = tiempo.hora_local(pago.fecha_hora)
        lineas.append(
            (
                local,
                (
                    local.date().isoformat(),
                    formato.hora(local),
                    f"Mensualidad · {mensualidad.titular} · "
                    f"{pago.desde.isoformat()} a {pago.hasta.isoformat()}",
                    mensualidad.placa,
                    NOMBRE_DEL_TIPO[mensualidad.tipo],
                    "",
                    pago.medio_pago.value.capitalize(),
                    str(pago.valor),
                    "",
                    str(pago.caja_id),
                ),
            )
        )
    for _, linea in sorted(lineas, key=lambda par: par[0]):
        yield linea
