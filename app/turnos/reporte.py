"""El reporte del día del lavadero para los jefes: lo que se cobró y lo que pasó.

Es el balance del día escrito como texto, con el mismo orden que el del parqueadero: lo
cobrado y por qué medio, los turnos, los cobros distintos al menú, la caja (cómo cerró y sus
correcciones) y lo que se corrigió en cajas de días anteriores. Sale por WhatsApp desde el
Balance (el botón abre WhatsApp con el texto listo) o por correo, solo, al cerrar la caja
del lavadero (si el correo está configurado y hay a quién).

Lleva placas y plata, no nombres ni celulares: es lo que los jefes necesitan para controlar.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from urllib.parse import quote

from sqlalchemy.orm import Session

from app import formato, tiempo
from app.caja.periodos import periodo
from app.caja.reglas import NOMBRE_DEL_MEDIO
from app.caja.reporte import (
    DETALLE_EN_EL_TEXTO,
    lineas_de_correcciones_de_antes,
    lineas_de_la_caja,
    lineas_de_las_salidas,
    y_mas,
)
from app.catalogo.parametros import leer_parametros
from app.config import obtener_entorno
from app.integraciones import correo
from app.turnos.balance import Balance, balance
from app.turnos.recibo import leer_ajustes

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReporteDelDia:
    negocio: str  # el nombre del lavadero, el de Configuración
    dia: date
    balance: Balance
    hecho_en: datetime

    @property
    def es_de_hoy(self) -> bool:
        return self.dia == tiempo.hora_local(self.hecho_en).date()

    @property
    def nombre_del_dia(self) -> str:
        """ "Lunes 26 de octubre de 2026" """
        return f"{formato.fecha_larga(self.dia)} de {self.dia.year}"


def armar(
    sesion: Session,
    dia: date,
    ahora: datetime | None = None,
    *,
    balance_del_dia: Balance | None = None,
) -> ReporteDelDia:
    """El reporte de `dia`. Si ya se calculó el balance de ese día, se pasa y no se repite."""
    return ReporteDelDia(
        negocio=leer_parametros(sesion).nombre_negocio,
        dia=dia,
        balance=balance_del_dia or balance(sesion, periodo("dia", dia)),
        hecho_en=ahora or tiempo.ahora(),
    )


def _veces(n: int, una: str, varias: str) -> str:
    return f"{n} {una if n == 1 else varias}"


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
        fuerte(reporte.negocio),
        f"Reporte del lavadero del {dia}",
        "",
        fuerte(f"Cobrado: {pesos(b.cobrado)}"),
    ]
    medios = [f"{NOMBRE_DEL_MEDIO[m]}: {pesos(v)}" for m, v in b.por_medio.items() if v]
    lineas += medios or ["No se cobró nada."]
    lineas += [
        "",
        f"Turnos cobrados: {b.cobros}"
        + (f" · promedio {pesos(b.ticket_promedio)}" if b.ticket_promedio else ""),
    ]
    if b.por_servicio:
        lineas.append("Servicios: " + " · ".join(f"{r.nombre} {r.cobros}" for r in b.por_servicio))
    lineas.append(
        f"Turnos pedidos: {b.turnos} (WhatsApp {b.por_whatsapp}, en el local {b.en_el_local})"
    )
    if b.cancelados or b.no_llegaron:
        lineas.append(f"Cancelados: {b.cancelados} · No llegaron: {b.no_llegaron}")
    if b.lavado_promedio_min is not None:
        lineas.append(f"Lavado promedio: {formato.duracion(b.lavado_promedio_min)}")
    if b.ajustes:
        lineas += ["", fuerte("Cobros distintos al menú")]
        if b.descuentos:
            lineas.append(
                f"Descuentos: {pesos(b.descuentos)} en {_veces(b.con_descuento, 'turno', 'turnos')}"
            )
        if b.recargos:
            lineas.append(f"Cobrado de más: {pesos(b.recargos)}")
        lineas += [
            f"- {a.placa}: {pesos(a.menu)} → {pesos(a.cobrado)}"
            + (f" ({a.motivo})" if a.motivo else "")
            for a in b.ajustes[:DETALLE_EN_EL_TEXTO]
        ]
        lineas += y_mas(b.cuantos_ajustes - min(len(b.ajustes), DETALLE_EN_EL_TEXTO))
    lineas += ["", fuerte("Caja")]
    if not b.cajas:
        lineas.append("No se abrió caja este día.")
    for resumen in b.cajas:
        lineas += lineas_de_la_caja(resumen, reporte.dia)
    if b.salidas_de_caja.total:
        lineas += ["", fuerte(f"Salidas de caja: {pesos(b.salidas_de_caja.total)}")]
        lineas += lineas_de_las_salidas(b.salidas_de_caja)
    if b.correcciones_de_antes:
        lineas += [
            "",
            fuerte(f"Correcciones a cajas de días anteriores: {len(b.correcciones_de_antes)}"),
        ]
        lineas += lineas_de_correcciones_de_antes(b.correcciones_de_antes)
    return "\n".join(lineas)


def asunto(reporte: ReporteDelDia) -> str:
    return (
        f"{reporte.negocio} · Reporte del lavadero del {formato.fecha(reporte.dia)}: "
        f"{formato.pesos(reporte.balance.cobrado)}"
    )


def enlace_de_whatsapp(reporte: ReporteDelDia) -> str:
    """Abre WhatsApp (en el celular o en el computador) con el reporte escrito: solo falta
    elegir el chat de los jefes y enviarlo."""
    return "https://wa.me/?text=" + quote(texto(reporte))


def enviar_por_correo(sesion: Session, dia: date, ahora: datetime | None = None) -> int:
    """Manda el reporte del día a los correos de los jefes. Devuelve a cuántos.

    Lanza correo.CorreoNoEnviado con un mensaje para el administrador si no se pudo.
    """
    destinos = leer_ajustes(sesion).destinos_del_reporte
    if not destinos:
        raise correo.CorreoNoEnviado(
            "No hay correos a quién mandarlo: escríbelos en Configuración > Recibo y reporte."
        )
    reporte = armar(sesion, dia, ahora)
    correo.enviar(
        asunto(reporte), texto(reporte, negrilla=False), destinos, nombre_remitente=reporte.negocio
    )
    return len(destinos)


def enviar_al_cerrar(abrir_sesion: Callable[[], Session], dia: date) -> None:
    """Después de cerrar la caja del lavadero (en segundo plano): manda el reporte si hay a
    quién y el correo está configurado. Si falla, queda en el registro; el cierre ya está
    guardado."""
    if not obtener_entorno().correo_configurado:
        return
    with abrir_sesion() as sesion:
        if not leer_ajustes(sesion).destinos_del_reporte:
            return
        try:
            cuantos = enviar_por_correo(sesion, dia)
        except correo.CorreoNoEnviado as error:
            logger.warning("El reporte del lavadero no salió por correo: %s", error)
            return
        logger.info("Reporte del lavadero enviado por correo a %d destinos.", cuantos)
