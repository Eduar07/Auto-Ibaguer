"""Lo que muestran las pantallas del parqueadero: quién está adentro, la caja y las mensualidades.

Solo lee; no cambia nada (eso vive en casos_de_uso).
"""

from collections import defaultdict
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app import formato, tiempo
from app.caja import consultas as de_la_caja
from app.caja.consultas import (  # noqa: F401  (las correcciones se muestran igual en las dos cajas)  # noqa: F401  (los filtros de cierres son comunes)
    CIERRES_POR_PAGINA,
    RESULTADOS,
    Cierres,
    Correccion,
    FilaCorreccion,
    FilaCorreccionDeCaja,
    FiltroDeCierres,
)
from app.parqueadero import casos_de_uso as casos
from app.parqueadero import reglas
from app.parqueadero.estados import (
    NOMBRE_DEL_TIPO,
    EstadoParqueo,
    TipoParqueo,
)
from app.parqueadero.tablas import (
    Caja,
    Mensualidad,
    PagoMensualidad,
    Parqueo,
)
from app.turnos.estados import MedioPago


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
class ResumenDeCaja(de_la_caja.DatosDelCierre):
    caja: Caja
    cuadre: reglas.Cuadre
    salidas: int  # vehículos que salieron con esta caja abierta (cobrados o mensualistas)
    mensualidades: int  # pagos de mensualidad cobrados en esta caja
    abrio: str
    cerro: str | None
    # De cobros y de la caja misma (base o conteo), de la primera a la última.
    correcciones: list[Correccion] = field(default_factory=list)

    # Cómo se muestra en la pantalla de la caja (la misma para los dos negocios).
    columnas = ("Salidas", "Mensualidades")

    @property
    def cifras(self) -> list[tuple[int, str]]:
        """Las cuentas de al lado del total: cuántas salidas y cuántas mensualidades."""
        cifras = [(self.salidas, "salida" if self.salidas == 1 else "salidas")]
        if self.mensualidades:
            nombre = "mensualidad" if self.mensualidades == 1 else "mensualidades"
            cifras.append((self.mensualidades, nombre))
        return cifras

    @property
    def lineas(self) -> list[tuple[MedioPago, tuple[int, ...], int]]:
        """Lo que entró por cada medio de pago: (medio, (salidas, mensualidades), total), solo
        los medios por los que entró algo."""
        return [
            (
                medio,
                (self.cuadre.parqueos.get(medio, 0), self.cuadre.mensualidades.get(medio, 0)),
                total,
            )
            for medio in MedioPago
            if (total := self.cuadre.por_medio(medio))
        ]


def resumen_de_caja(sesion: Session, caja: Caja) -> ResumenDeCaja:
    return resumenes_de_cajas(sesion, [caja])[0]


def resumenes_de_cajas(sesion: Session, cajas: Sequence[Caja]) -> list[ResumenDeCaja]:
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
    por_caja, nombres = de_la_caja.correcciones_de_las_cajas(sesion, cajas)
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


# --- Los cierres anteriores, con filtros (app.caja.consultas) -------------------------------


def _lo_que_entro(sesion: Session, ids: Select) -> int:
    """Lo que entró a esas cajas del parqueadero: salidas cobradas y pagos de mensualidad."""
    salidas = sesion.scalar(
        select(func.coalesce(func.sum(Parqueo.valor_cobrado), 0)).where(
            Parqueo.caja_id.in_(ids),
            Parqueo.estado == EstadoParqueo.SALIO,
            Parqueo.valor_cobrado > 0,
        )
    )
    mensualidades = sesion.scalar(
        select(func.coalesce(func.sum(PagoMensualidad.valor), 0)).where(
            PagoMensualidad.caja_id.in_(ids)
        )
    )
    return int(salidas) + int(mensualidades)


def cierres(sesion: Session, filtro: FiltroDeCierres, pagina: int = 1) -> Cierres[ResumenDeCaja]:
    """Los cierres del parqueadero que cumplen el filtro, de a 30 por página."""
    return de_la_caja.cierres(
        sesion,
        casos.NEGOCIO,
        filtro,
        pagina,
        entro=_lo_que_entro,
        resumir=resumenes_de_cajas,
    )


def todos_los_cierres(sesion: Session, filtro: FiltroDeCierres) -> list[ResumenDeCaja]:
    """Todos los que cumplen el filtro (para el archivo de Excel), del más viejo al más nuevo."""
    return de_la_caja.todos_los_cierres(sesion, casos.NEGOCIO, filtro, resumir=resumenes_de_cajas)


def quienes_cerraron(sesion: Session) -> list[tuple[int, str]]:
    """Los administradores que han cerrado alguna caja del parqueadero."""
    return de_la_caja.quienes_cerraron(sesion, casos.NEGOCIO)


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


def correcciones_a_cajas_de_antes(
    sesion: Session, inicio: datetime, fin: datetime
) -> list[Correccion]:
    """Las correcciones hechas entre `inicio` y `fin` a cajas del parqueadero que ya estaban
    cerradas y se abrieron antes de `inicio` (para el reporte de hoy)."""
    return de_la_caja.correcciones_a_cajas_de_antes(sesion, casos.NEGOCIO, inicio, fin)
