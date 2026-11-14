"""El reporte del día para los jefes: lo que se hizo y lo que se recogió.

Es el balance del día, más lo que queda adentro y las mensualidades pendientes, escrito
como texto. Sale por WhatsApp (el botón abre WhatsApp con el texto listo para elegir a quién
mandarlo) o por correo, solo, al cerrar la caja (si el correo está configurado).

Lleva placas y plata, no nombres ni celulares: es lo que los jefes necesitan para controlar.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from urllib.parse import quote

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app import formato, tiempo
from app.config import obtener_entorno
from app.integraciones import correo
from app.parqueadero import consultas
from app.parqueadero.balance import Balance, balance, limites, periodo
from app.parqueadero.consultas import ResumenDeCaja
from app.parqueadero.estados import EstadoParqueo, TipoParqueo
from app.parqueadero.reglas import NOMBRE_DEL_MEDIO
from app.parqueadero.tablas import Parqueo
from app.parqueadero.tarifas import leer_ajustes

logger = logging.getLogger(__name__)


# En el texto (WhatsApp y correo) van hasta tantas líneas de detalle por lista; el resto se
# cuenta ("y 4 más") y se ve completo en el Balance. Un mensaje eterno no se lee.
DETALLE_EN_EL_TEXTO = 10


@dataclass(frozen=True)
class ReporteDelDia:
    parqueadero: str
    dia: date
    balance: Balance
    adentro: dict[TipoParqueo, int]  # al terminar el día (o ahora, si el día es hoy)
    pendientes: list[str]  # placas con la mensualidad pendiente de pago, hoy
    hecho_en: datetime

    @property
    def es_de_hoy(self) -> bool:
        return self.dia == tiempo.hora_local(self.hecho_en).date()

    @property
    def nombre_del_dia(self) -> str:
        """ "Jueves 12 de noviembre de 2026" """
        return f"{formato.fecha_larga(self.dia)} de {self.dia.year}"


def armar(
    sesion: Session,
    dia: date,
    ahora: datetime | None = None,
    *,
    balance_del_dia: Balance | None = None,
) -> ReporteDelDia:
    """El reporte de `dia`. Si ya se calculó el balance de ese día, se pasa y no se repite."""
    ahora = ahora or tiempo.ahora()
    _, fin_del_dia = limites(periodo("dia", dia))
    pendientes = [
        fila.mensualidad.placa for fila in consultas.mensualidades(sesion, ahora) if fila.por_cobrar
    ]
    return ReporteDelDia(
        parqueadero=leer_ajustes(sesion).nombre,
        dia=dia,
        balance=balance_del_dia or balance(sesion, periodo("dia", dia)),
        adentro=adentro_en(sesion, min(ahora, fin_del_dia)),
        pendientes=pendientes,
        hecho_en=ahora,
    )


def adentro_en(sesion: Session, momento: datetime) -> dict[TipoParqueo, int]:
    """Cuántos vehículos había adentro en ese momento: ya habían entrado y todavía no habían
    salido (el que sale justo en ese momento ya no cuenta). Las entradas anuladas no cuentan:
    fueron un error, no un vehículo."""
    adentro = {tipo: 0 for tipo in TipoParqueo}
    for tipo, cuantos in sesion.execute(
        select(Parqueo.tipo, func.count())
        .where(
            Parqueo.estado != EstadoParqueo.ANULADO,
            Parqueo.entrada_en <= momento,
            or_(Parqueo.salida_en.is_(None), Parqueo.salida_en > momento),
        )
        .group_by(Parqueo.tipo)
    ):
        adentro[tipo] = cuantos
    return adentro


def _motos_y_carros(cuantos: dict[TipoParqueo, int]) -> str:
    motos, carros = cuantos[TipoParqueo.MOTO], cuantos[TipoParqueo.CARRO]
    return f"{motos} moto{'s' if motos != 1 else ''}, {carros} carro{'s' if carros != 1 else ''}"


def asunto(reporte: ReporteDelDia) -> str:
    return (
        f"{reporte.parqueadero} · Reporte del {formato.fecha(reporte.dia)}: "
        f"{formato.pesos(reporte.balance.total)}"
    )


def texto(reporte: ReporteDelDia, *, negrilla: bool = True) -> str:
    """El reporte para leer en el celular. Con `negrilla`, los títulos van entre asteriscos
    (WhatsApp los pone en negrilla); en el correo van sin asteriscos."""

    def fuerte(linea: str) -> str:
        return f"*{linea}*" if negrilla else linea

    b = reporte.balance
    pesos = formato.pesos
    dia = reporte.nombre_del_dia.lower()
    if reporte.es_de_hoy:
        # El día va por la mitad: hasta qué hora van las cuentas.
        dia += f", hasta {formato.hora_con_articulo(tiempo.hora_local(reporte.hecho_en))}"
    lineas = [
        fuerte(reporte.parqueadero),
        f"Reporte del {dia}",
        "",
        fuerte(f"Recogido: {pesos(b.total)}"),
    ]
    medios = [f"{NOMBRE_DEL_MEDIO[m]}: {pesos(v)}" for m, v in b.por_medio.items() if v]
    lineas += medios or ["No se recogió plata."]
    lineas += [
        "",
        f"Salidas: {b.cuantas_salidas} · {pesos(b.total_salidas)} ({_motos_y_carros(b.salidas)})",
        f"Mensualidades: {b.pagos_mensualidad} pago{'s' if b.pagos_mensualidad != 1 else ''}"
        f" · {pesos(b.cobrado_mensualidades)}",
        f"Entradas: {b.cuantas_entradas} ({_motos_y_carros(b.entradas)})",
    ]
    if b.mensualistas_sin_pagar:
        lineas.append(f"Mensualistas al día que salieron: {b.mensualistas_sin_pagar}")
    if b.ajustes:
        lineas += ["", fuerte("Cobros distintos a la tarifa")]
        if b.descuentos:
            lineas.append(
                f"Descuentos: {pesos(b.descuentos)} en {b.salidas_con_descuento} "
                f"salida{'s' if b.salidas_con_descuento != 1 else ''}"
            )
        if b.recargos:
            lineas.append(f"Cobrado de más: {pesos(b.recargos)}")
        lineas += [
            f"- {a.placa}: {pesos(a.calculado)} → {pesos(a.cobrado)} ({a.motivo})"
            for a in b.ajustes[:DETALLE_EN_EL_TEXTO]
        ]
        lineas += _y_mas(b.cuantos_ajustes - min(len(b.ajustes), DETALLE_EN_EL_TEXTO))
    if b.total_anuladas:
        lineas += ["", fuerte(f"Entradas anuladas: {b.total_anuladas}")]
        lineas += [
            f"- {a.placa} (ticket {a.ticket}): {a.motivo}" for a in b.anuladas[:DETALLE_EN_EL_TEXTO]
        ]
        lineas += _y_mas(b.total_anuladas - min(len(b.anuladas), DETALLE_EN_EL_TEXTO))
    lineas += ["", fuerte("Caja")]
    if not b.cajas:
        lineas.append("No se abrió caja este día.")
    for resumen in b.cajas:
        lineas += _lineas_de_la_caja(resumen, reporte.dia)
    if b.correcciones_de_antes:
        lineas += [
            "",
            fuerte(f"Correcciones a cajas de días anteriores: {len(b.correcciones_de_antes)}"),
        ]
        lineas += _lineas_de_correcciones_de_antes(b)
    adentro = f"{sum(reporte.adentro.values())} ({_motos_y_carros(reporte.adentro)})"
    lineas += [
        "",
        f"Quedan adentro: {adentro}"
        if reporte.es_de_hoy
        else f"Adentro al terminar el día: {adentro}",
    ]
    if reporte.pendientes:
        hoy = formato.dia_y_mes(tiempo.hora_local(reporte.hecho_en).date())
        cuando = "" if reporte.es_de_hoy else f" hoy ({hoy})"
        placas = ", ".join(reporte.pendientes[:DETALLE_EN_EL_TEXTO])
        if len(reporte.pendientes) > DETALLE_EN_EL_TEXTO:
            placas += f" y {len(reporte.pendientes) - DETALLE_EN_EL_TEXTO} más"
        lineas.append(
            f"Mensualidades pendientes de pago{cuando}: {len(reporte.pendientes)} ({placas})"
        )
    return "\n".join(lineas)


def _y_mas(cuantos: int) -> list[str]:
    return [f"- y {cuantos} más (ver el Balance)"] if cuantos > 0 else []


def _lineas_de_la_caja(resumen: ResumenDeCaja, dia: date) -> list[str]:
    """Cómo cerró la caja (o lo que debería tener, si sigue abierta) y sus correcciones."""
    pesos = formato.pesos
    caja = resumen.caja
    if caja.cerrada_en is None:
        lineas = [
            f"Sigue abierta: debería haber {pesos(resumen.cuadre.efectivo_esperado)} en "
            "efectivo (con la base)."
        ]
    else:
        cerro = tiempo.hora_local(caja.cerrada_en)
        cuando = f"a {formato.hora_con_articulo(cerro)}"
        if cerro.date() != dia:  # se cerró pasada la medianoche
            cuando = f"el {formato.dia_y_mes(cerro.date())} {cuando}"
        quien = f" {resumen.cerro}" if resumen.cerro not in (None, "—") else ""
        lineas = [
            f"Cerró{quien} {cuando}",
            f"Base {pesos(caja.base)} · Debía haber {pesos(caja.efectivo_esperado or 0)} · "
            f"Contaron {pesos(caja.efectivo_contado or 0)}",
            _diferencia(caja.diferencia or 0),
        ]
        if resumen.corregida_despues_del_cierre:
            al_cerrar = _diferencia(resumen.diferencia_al_cerrar or 0)
            lineas.append(
                f"Se corrigió después del cierre. Al cerrar: {al_cerrar[0].lower()}{al_cerrar[1:]}"
            )
        if caja.nota_cierre:
            lineas.append(f"Nota: {caja.nota_cierre}")
    if resumen.correcciones:
        # Lo que se corrigió después de cobrar: es lo primero que un jefe debe revisar.
        lineas.append(f"Correcciones: {len(resumen.correcciones)}")
        lineas += [f"- {fila.en_una_linea}" for fila in resumen.correcciones[:DETALLE_EN_EL_TEXTO]]
        lineas += _y_mas(len(resumen.correcciones) - DETALLE_EN_EL_TEXTO)
    return lineas


def _lineas_de_correcciones_de_antes(b: Balance) -> list[str]:
    """Lo que se corrigió este día en cajas de días anteriores, con el día de cada caja."""
    lineas = [
        f"- Caja del {formato.dia_y_mes(fila.dia_de_la_caja)}: {fila.en_una_linea}"
        if fila.dia_de_la_caja
        else f"- {fila.en_una_linea}"
        for fila in b.correcciones_de_antes[:DETALLE_EN_EL_TEXTO]
    ]
    return lineas + _y_mas(len(b.correcciones_de_antes) - DETALLE_EN_EL_TEXTO)


def _diferencia(diferencia: int) -> str:
    if diferencia == 0:
        return "Cuadró exacto."
    if diferencia > 0:
        return f"Sobraron {formato.pesos(diferencia)}."
    return f"Faltaron {formato.pesos(-diferencia)}."


def enlace_de_whatsapp(reporte: ReporteDelDia) -> str:
    """Abre WhatsApp (en el celular o en el computador) con el reporte escrito: solo falta
    elegir el chat de los jefes y enviarlo."""
    return "https://wa.me/?text=" + quote(texto(reporte))


def enviar_por_correo(sesion: Session, dia: date, ahora: datetime | None = None) -> int:
    """Manda el reporte del día a los correos de los jefes. Devuelve a cuántos.

    Lanza correo.CorreoNoEnviado con un mensaje para el administrador si no se pudo.
    """
    destinos = leer_ajustes(sesion).destinos_del_reporte
    reporte = armar(sesion, dia, ahora)
    correo.enviar(
        asunto(reporte),
        texto(reporte, negrilla=False),
        destinos,
        nombre_remitente=reporte.parqueadero,
    )
    return len(destinos)


def enviar_al_cerrar(abrir_sesion: Callable[[], Session], dia: date) -> None:
    """Después de cerrar la caja (en segundo plano): manda el reporte si hay a quién y el
    correo está configurado. Si falla, queda en el registro; el cierre ya está guardado."""
    if not obtener_entorno().correo_configurado:
        return
    with abrir_sesion() as sesion:
        if not leer_ajustes(sesion).destinos_del_reporte:
            return
        try:
            cuantos = enviar_por_correo(sesion, dia)
        except correo.CorreoNoEnviado as error:
            logger.warning("El reporte del día no salió por correo: %s", error)
            return
        logger.info("Reporte del día enviado por correo a %d destinos.", cuantos)
