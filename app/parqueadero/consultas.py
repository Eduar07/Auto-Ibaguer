"""Lo que muestran las pantallas del parqueadero: quién está adentro, la caja y las mensualidades.

Solo lee; no cambia nada (eso vive en casos_de_uso).
"""

from collections import defaultdict
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import formato, tiempo
from app.parqueadero import casos_de_uso as casos
from app.parqueadero import reglas
from app.parqueadero.estados import NOMBRE_DEL_TIPO, EstadoParqueo, TipoCorreccion, TipoParqueo
from app.parqueadero.tablas import (
    CajaParqueadero,
    CorreccionCobro,
    Mensualidad,
    PagoMensualidad,
    Parqueo,
)
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
    # Las mensualidades de los que están adentro y sus pagos, en dos consultas para todos
    # (no dos por vehículo): con el parqueadero lleno, esta lista se pide cada minuto.
    mensualidades = (
        {
            m.placa: m
            for m in sesion.scalars(
                select(Mensualidad).where(
                    Mensualidad.placa.in_({p.placa for p in parqueos}), Mensualidad.activa
                )
            )
        }
        if parqueos
        else {}
    )
    pagos = casos.periodos_por_mensualidad(sesion, [m.id for m in mensualidades.values()])
    vehiculos = []
    for parqueo in parqueos:
        mensualidad = mensualidades.get(parqueo.placa)
        liquidacion = casos.liquidar_con(
            parqueo, ahora, mensualidad, pagos[mensualidad.id] if mensualidad else []
        )
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
    parqueo_id: int | None = None
    pago_id: int | None = None

    @property
    def para_corregir(self) -> str:
        """Dónde se abre el diálogo para corregir este cobro."""
        if self.pago_id is not None:
            return f"/parqueadero/mensualidades/pagos/{self.pago_id}/corregir"
        return f"/parqueadero/{self.parqueo_id}/corregir"


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
            parqueo_id=parqueo.id,
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
            pago_id=pago.id,
        )
        for pago, mensualidad in pagos
    ]
    return sorted(lineas, key=lambda linea: linea.momento, reverse=True)


@dataclass(frozen=True)
class FilaCorreccion:
    """Una corrección de un cobro, lista para mostrar: qué cambió y quién lo hizo."""

    correccion: CorreccionCobro
    quien: str

    @property
    def que_paso(self) -> str:
        """ "Se deshizo la salida", "Se anuló el pago" o "Se corrigió el cobro" """
        return {
            TipoCorreccion.SALIDA_DESHECHA: "Se deshizo la salida",
            TipoCorreccion.PAGO_ANULADO: "Se anuló el pago",
            TipoCorreccion.CORREGIDO: "Se corrigió el cobro",
        }[self.correccion.tipo]

    @property
    def antes(self) -> str:
        c = self.correccion
        return f"{formato.pesos(c.valor_antes)} {reglas.como_se_pago(c.medio_antes)}"

    @property
    def despues(self) -> str | None:
        """Cómo quedó, si quedó un cobro (deshecho o anulado no queda nada)."""
        c = self.correccion
        if c.tipo is not TipoCorreccion.CORREGIDO:
            return None
        return f"{formato.pesos(c.valor_despues)} {reglas.como_se_pago(c.medio_despues)}"

    @property
    def cambio_en_efectivo(self) -> int:
        """Cuánto cambió el efectivo que debe haber en el cajón: negativo si salió plata."""
        c = self.correccion
        despues = c.valor_despues if c.medio_despues is MedioPago.EFECTIVO else 0
        antes = c.valor_antes if c.medio_antes is MedioPago.EFECTIVO else 0
        return despues - antes

    @property
    def en_una_linea(self) -> str:
        """Para el reporte y el ticket: "ABC123: se deshizo la salida ($6.000 en efectivo).
        Motivo: era otro carro. Por eduar." """
        c = self.correccion
        cambio = f"{self.antes} → {self.despues}" if self.despues else self.antes
        return (
            f"{c.placa}: {self.que_paso.lower()} ({cambio}). Motivo: {c.motivo}. Por {self.quien}."
        )


@dataclass(frozen=True)
class ResumenDeCaja:
    caja: CajaParqueadero
    cuadre: reglas.Cuadre
    salidas: int  # vehículos que salieron con esta caja abierta (cobrados o mensualistas)
    mensualidades: int  # pagos de mensualidad cobrados en esta caja
    abrio: str
    cerro: str | None
    correcciones: list[FilaCorreccion] = field(default_factory=list)  # de la primera a la última


def resumen_de_caja(sesion: Session, caja: CajaParqueadero) -> ResumenDeCaja:
    return resumenes_de_cajas(sesion, [caja])[0]


def resumenes_de_cajas(sesion: Session, cajas: Sequence[CajaParqueadero]) -> list[ResumenDeCaja]:
    """El cuadre de varias cajas con unas pocas consultas para todas (no cinco por caja): la
    lista de cierres y el balance del mes muestran decenas."""
    if not cajas:
        return []
    ids = [caja.id for caja in cajas]
    cobros: dict[int, list[tuple[MedioPago, int]]] = defaultdict(list)
    for caja_id, medio, total in sesion.execute(
        select(Parqueo.caja_id, Parqueo.medio_pago, func.sum(Parqueo.valor_cobrado))
        .where(
            Parqueo.caja_id.in_(ids),
            Parqueo.estado == EstadoParqueo.SALIO,
            Parqueo.valor_cobrado > 0,
        )
        .group_by(Parqueo.caja_id, Parqueo.medio_pago)
    ):
        cobros[caja_id].append((medio, int(total)))
    pagos: dict[int, list[tuple[MedioPago, int]]] = defaultdict(list)
    for caja_id, medio, total in sesion.execute(
        select(PagoMensualidad.caja_id, PagoMensualidad.medio_pago, func.sum(PagoMensualidad.valor))
        .where(PagoMensualidad.caja_id.in_(ids))
        .group_by(PagoMensualidad.caja_id, PagoMensualidad.medio_pago)
    ):
        pagos[caja_id].append((medio, int(total)))
    salidas = dict(
        sesion.execute(
            select(Parqueo.caja_id, func.count())
            .where(Parqueo.caja_id.in_(ids), Parqueo.estado == EstadoParqueo.SALIO)
            .group_by(Parqueo.caja_id)
        ).all()
    )
    cuantos_pagos = dict(
        sesion.execute(
            select(PagoMensualidad.caja_id, func.count())
            .where(PagoMensualidad.caja_id.in_(ids))
            .group_by(PagoMensualidad.caja_id)
        ).all()
    )
    correcciones = sesion.scalars(
        select(CorreccionCobro)
        .where(CorreccionCobro.caja_id.in_(ids))
        .order_by(CorreccionCobro.fecha_hora, CorreccionCobro.id)
    ).all()
    quienes = {caja.abierta_por for caja in cajas} | {caja.cerrada_por for caja in cajas}
    quienes |= {correccion.administrador_id for correccion in correcciones}
    quienes.discard(None)
    nombres = (
        dict(
            sesion.execute(
                select(Administrador.id, Administrador.usuario).where(Administrador.id.in_(quienes))
            ).all()
        )
        if quienes
        else {}
    )
    por_caja: dict[int, list[FilaCorreccion]] = defaultdict(list)
    for correccion in correcciones:
        por_caja[correccion.caja_id].append(
            FilaCorreccion(correccion, nombres.get(correccion.administrador_id, "—"))
        )
    return [
        ResumenDeCaja(
            caja=caja,
            cuadre=reglas.Cuadre(
                base=caja.base,
                parqueos=reglas.sumar_por_medio(cobros[caja.id]),
                mensualidades=reglas.sumar_por_medio(pagos[caja.id]),
            ),
            salidas=salidas.get(caja.id, 0),
            mensualidades=cuantos_pagos.get(caja.id, 0),
            abrio=nombres.get(caja.abierta_por, "—"),
            cerro=nombres.get(caja.cerrada_por, "—") if caja.cerrada_en else None,
            correcciones=por_caja[caja.id],
        )
        for caja in cajas
    ]


def cajas_cerradas(sesion: Session, limite: int = 30) -> list[ResumenDeCaja]:
    cajas = sesion.scalars(
        select(CajaParqueadero)
        .where(CajaParqueadero.cerrada_en.is_not(None))
        .order_by(CajaParqueadero.cerrada_en.desc())
        .limit(limite)
    ).all()
    return resumenes_de_cajas(sesion, cajas)


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
    periodos = casos.periodos_por_mensualidad(sesion, [m.id for m in lista])
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
