"""La pantalla de elegir negocio (/inicio): lo primero que se ve después de entrar.

Salen solo los dos negocios, el lavadero y el parqueadero, cada uno con lo de hoy para
decidir de un vistazo a cuál entrar: cuántos carros esperan, cuánta plata ha entrado, si la
caja está abierta. Desde cualquier pantalla se vuelve aquí tocando el nombre del negocio,
arriba a la izquierda.
"""

from dataclasses import dataclass
from datetime import datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import tiempo
from app.catalogo.parametros import leer_parametros
from app.comentarios.consultas import por_atender as comentarios_por_atender
from app.parqueadero import balance
from app.parqueadero.casos_de_uso import caja_abierta
from app.parqueadero.estados import EstadoParqueo
from app.parqueadero.tablas import Caja, Parqueo
from app.turnos.estados import ESTADOS_ACTIVOS, ESTADOS_EN_ESPERA, EstadoTurno
from app.turnos.tablas import Pago, Turno
from app.web.comun import Admin, SesionBD, pagina, verificar_origen
from app.web.negocios import LAVADERO, PARQUEADERO
from app.web.tablero import inicio_del_dia

router = APIRouter(dependencies=[Depends(verificar_origen)])


@dataclass(frozen=True)
class HoyEnElLavadero:
    en_espera: int
    en_lavado: int
    por_cobrar: int  # ya listos, esperando que el cliente pague
    cobrado: int
    cobros: int
    bot_activo: bool
    comentarios_pendientes: int


@dataclass(frozen=True)
class HoyEnElParqueadero:
    adentro: int
    salidas: int
    recogido: int  # salidas y mensualidades, como en el Balance
    caja: Caja | None
    caja_de_otro_dia: bool  # alguien la dejó abierta desde un día anterior


def hoy_en_el_lavadero(sesion: Session, ahora: datetime) -> HoyEnElLavadero:
    por_estado: dict[str, int] = {
        estado: cuantos
        for estado, cuantos in sesion.execute(
            select(Turno.estado, func.count())
            .where(Turno.estado.in_(ESTADOS_ACTIVOS))
            .group_by(Turno.estado)
        )
    }
    # Igual que "Cobrado hoy" en la fila: lo que se cobró desde las 12:00 a.m.
    cobrado, cobros = sesion.execute(
        select(func.coalesce(func.sum(Pago.precio_cobrado), 0), func.count(Pago.id)).where(
            Pago.fecha_hora >= inicio_del_dia(ahora)
        )
    ).one()
    return HoyEnElLavadero(
        en_espera=sum(por_estado.get(estado, 0) for estado in ESTADOS_EN_ESPERA),
        en_lavado=por_estado.get(EstadoTurno.EN_LAVADO, 0),
        por_cobrar=por_estado.get(EstadoTurno.LISTO, 0),
        cobrado=int(cobrado),
        cobros=int(cobros),
        bot_activo=leer_parametros(sesion).bot_activo,
        comentarios_pendientes=comentarios_por_atender(sesion),
    )


def hoy_en_el_parqueadero(sesion: Session, ahora: datetime) -> HoyEnElParqueadero:
    hoy = tiempo.hora_local(ahora).date()
    dia = balance.periodo("dia", hoy)
    inicio, fin = balance.limites(dia)
    adentro, salidas = sesion.execute(
        select(
            func.count().filter(Parqueo.estado == EstadoParqueo.ADENTRO),
            func.count().filter(
                Parqueo.estado == EstadoParqueo.SALIO,
                Parqueo.salida_en >= inicio,
                Parqueo.salida_en < fin,
            ),
        )
    ).one()
    caja = caja_abierta(sesion)
    return HoyEnElParqueadero(
        adentro=int(adentro),
        salidas=int(salidas),
        recogido=balance.total_recogido(sesion, dia),
        caja=caja,
        caja_de_otro_dia=caja is not None and tiempo.hora_local(caja.abierta_en).date() < hoy,
    )


def saludo(ahora: datetime) -> str:
    """Como se saluda en Colombia, según la hora de Bogotá."""
    hora = tiempo.hora_local(ahora).hour
    if 5 <= hora < 12:
        return "Buenos días"
    return "Buenas tardes" if 12 <= hora < 19 else "Buenas noches"


@router.get("/inicio", response_class=HTMLResponse)
def pagina_inicio(request: Request, sesion: SesionBD, administrador: Admin) -> Response:
    ahora = tiempo.ahora()
    contexto = {
        "saludo": saludo(ahora),
        "usuario": administrador.usuario,
        "lavadero": LAVADERO,
        "parqueadero": PARQUEADERO,
        "hoy_lavadero": hoy_en_el_lavadero(sesion, ahora),
        "hoy_parqueadero": hoy_en_el_parqueadero(sesion, ahora),
    }
    return pagina(request, sesion, "inicio.html", contexto, seccion="inicio")
