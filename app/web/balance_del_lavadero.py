"""El Balance del lavadero en el dashboard: lo que se cobró y lo que pasó en un día, una semana,
un mes o los días que se escojan (hasta 92), con las gráficas día por día y por día de la
semana (en plata o en turnos). El del día es también el reporte para los jefes: se imprime o
se manda por WhatsApp.

Los periodos y las gráficas son los mismos del Balance del parqueadero (app/caja).
"""

from datetime import date, timedelta
from typing import Any
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response

from app import formato
from app.caja import graficas
from app.caja.graficas import DiaDeLaGrafica, Medida
from app.caja.periodos import DIAS_MAXIMOS_DEL_RANGO, TIPOS_DE_PERIODO, Periodo, rango
from app.caja.periodos import periodo as periodo_de
from app.caja.reglas import NOMBRE_DEL_MEDIO
from app.turnos import balance as balance_del_lavadero
from app.turnos.balance import Balance, DiaDelLavadero
from app.web.caja import fecha as leer_fecha
from app.web.caja import hoy, ticket
from app.web.caja_del_lavadero import ajustes_del_ticket
from app.web.comun import Admin, SesionBD, pagina, verificar_origen

router = APIRouter(dependencies=[Depends(verificar_origen)])

# Lo que se puede ver en las gráficas: la plata cobrada o los turnos cobrados.
QUE_SE_VE = {"plata": "Plata", "turnos": "Turnos"}
_MEDIDAS = {
    "plata": Medida("plata cobrada"),
    "turnos": Medida("turnos cobrados", "turno", "turnos"),
}


def _veces(n: int, una: str, varias: str) -> str:
    return f"{n} {una if n == 1 else varias}"


def _dias_de_la_grafica(b: Balance, ver: str) -> list[DiaDeLaGrafica]:
    def detalle(d: DiaDelLavadero) -> str:
        if ver == "turnos":
            return (
                f"{formato.pesos(d.cobrado)} · {_veces(d.turnos, 'turno pedido', 'turnos pedidos')}"
            )
        return f"{_veces(d.cobros, 'turno cobrado', 'turnos cobrados')}"

    return [
        DiaDeLaGrafica(
            dia=d.dia,
            valor=d.cobros if ver == "turnos" else d.cobrado,
            detalle=detalle(d),
            con_movimiento=bool(d.cobros or d.turnos),
        )
        for d in b.dias
    ]


def _enlace(p: Periodo, ver: str = "plata", dia: date | None = None) -> str:
    """La dirección del Balance de un periodo, con lo que se está viendo en las gráficas."""
    consulta = {"periodo": p.tipo}
    if p.tipo == "rango":
        consulta |= {"desde": p.desde.isoformat(), "hasta": p.hasta.isoformat()}
    else:
        consulta["fecha"] = (dia or p.desde).isoformat()
    if ver != "plata":
        consulta["ver"] = ver
    return f"/balance?{urlencode(consulta)}"


def _ultimos(dias: int, de_hoy: date) -> Periodo:
    return rango(de_hoy - timedelta(days=dias - 1), de_hoy)


# --- El reporte del día ----------------------------------------------------------------------


def _caja_en_palabras(b: Balance) -> str:
    if not b.cajas:
        return "No se abrió caja."
    if not b.cajas_cerradas:
        return "La caja sigue abierta."
    partes = []
    if b.cajas_que_cuadraron:
        partes.append(_veces(b.cajas_que_cuadraron, "cuadró", "cuadraron"))
    if b.sobrantes:
        partes.append(f"sobraron {formato.pesos(sum(b.sobrantes))}")
    if b.faltantes:
        partes.append(f"faltaron {formato.pesos(sum(b.faltantes))}")
    texto = ", ".join(partes)
    return f"Caja: {texto}." if len(b.cajas_cerradas) == 1 else f"Cajas: {texto}."


def texto_del_reporte(b: Balance, negocio: str, negrilla: bool = True) -> str:
    """El reporte del día en texto, para WhatsApp (con *negrilla*) o para leer en la pantalla.
    Con placas y plata, sin nombres ni celulares de los clientes."""
    n = "*" if negrilla else ""
    lineas = [
        f"{n}{negocio} · {b.periodo.titulo}{n}",
        f"Cobrado: {n}{formato.pesos(b.cobrado)}{n} en "
        + _veces(b.cobros, "turno", "turnos")
        + (f" (promedio {formato.pesos(b.ticket_promedio)})" if b.ticket_promedio else ""),
    ]
    if b.por_medio:
        lineas.append(
            " · ".join(
                f"{NOMBRE_DEL_MEDIO[medio]} {formato.pesos(valor)}"
                for medio, valor in b.por_medio.items()
            )
        )
    lineas.append(
        f"Turnos pedidos: {b.turnos} (WhatsApp {b.por_whatsapp}, en el local {b.en_el_local})"
        f" · cancelados {b.cancelados} · no llegaron {b.no_llegaron}"
    )
    if b.lavado_promedio_min is not None:
        lineas.append(f"Lavado promedio: {formato.duracion(b.lavado_promedio_min)}")
    if b.descuentos:
        lineas.append(
            f"Descuentos: {formato.pesos(b.descuentos)} en "
            + _veces(b.con_descuento, "turno", "turnos")
        )
    lineas.append(_caja_en_palabras(b))
    correcciones = [*b.correcciones, *b.correcciones_de_antes]
    if correcciones:
        lineas.append(f"Correcciones ({len(correcciones)}):")
        lineas += [f"- {fila.en_una_linea}" for fila in correcciones]
    return "\n".join(lineas)


# --- Las pantallas ---------------------------------------------------------------------------


@router.get("/balance", response_class=HTMLResponse)
def pagina_balance(
    request: Request,
    sesion: SesionBD,
    _: Admin,
    periodo: str = "dia",
    fecha: str | None = None,
    desde: str | None = None,
    hasta: str | None = None,
    ver: str = "plata",
) -> Response:
    """Lo que se cobró y lo que pasó en un día, una semana, un mes o los días que se escojan."""
    de_hoy = hoy()
    ver = ver if ver in QUE_SE_VE else "plata"
    # Del futuro no hay nada que ver: una fecha que no ha llegado muestra hoy.
    if periodo == "rango":
        p = rango(
            min(leer_fecha(desde) or de_hoy - timedelta(days=29), de_hoy),
            min(leer_fecha(hasta) or de_hoy, de_hoy),
        )
    else:
        tipo = periodo if periodo in TIPOS_DE_PERIODO else "dia"
        p = periodo_de(tipo, min(leer_fecha(fecha) or de_hoy, de_hoy))
    b = balance_del_lavadero.balance(sesion, p)

    def ir(otro: Periodo) -> str:
        return _enlace(otro, ver)

    referencia = de_hoy if p.contiene(de_hoy) else p.desde
    pestanas = [
        (nombre, _enlace(periodo_de(clave, referencia), ver, referencia), clave == p.tipo)
        for clave, nombre in (("dia", "Día"), ("semana", "Semana"), ("mes", "Mes"))
    ]
    pestanas.append(
        ("Rango", ir(p if p.tipo == "rango" else _ultimos(30, de_hoy)), p.tipo == "rango")
    )
    contexto: dict[str, Any] = {
        "b": b,
        "p": p,
        "hoy": de_hoy,
        "anterior": balance_del_lavadero.total_cobrado(sesion, p.anterior()),
        "nombre_del_anterior": p.nombre_del_anterior(de_hoy),
        "nombre_del_medio": NOMBRE_DEL_MEDIO,
        "pestanas": pestanas,
        "ir_anterior": ir(p.anterior()),
        "ir_siguiente": ir(p.siguiente()) if p.hasta < de_hoy else None,
        "ir_a_hoy": (
            ir(
                _ultimos(p.cuantos_dias, de_hoy)
                if p.tipo == "rango"
                else periodo_de(p.tipo, de_hoy)
            )
            if not p.contiene(de_hoy)
            else None
        ),
        "ultimos_30": _enlace(_ultimos(30, de_hoy), ver),
        "ultimos_90": _enlace(_ultimos(90, de_hoy), ver),
        "dias_maximos": DIAS_MAXIMOS_DEL_RANGO,
        "ver": ver,
        "enlace_del_dia": lambda dia: _enlace(periodo_de("dia", dia)),
    }
    if p.tipo == "dia":
        texto = texto_del_reporte(b, "Lavadero")
        contexto |= {
            "whatsapp": f"https://wa.me/?text={quote(texto)}",
            "texto_del_reporte": texto_del_reporte(b, "Lavadero", negrilla=False),
            "ver_el_mes": ir(periodo_de("mes", p.desde)),
        }
    else:
        medida = _MEDIDAS[ver]
        dias = _dias_de_la_grafica(b, ver)
        contexto |= {
            "opciones_ver": [
                (nombre, _enlace(p, clave), clave == ver) for clave, nombre in QUE_SE_VE.items()
            ],
            "dia_a_dia": graficas.dia_por_dia(
                dias,
                de_hoy,
                medida,
                medida.en_el_titulo,
                lambda dia: ir(periodo_de("dia", dia)),
            ),
            "por_dia_de_la_semana": graficas.por_dia_de_la_semana(
                dias, de_hoy, medida, medida.en_el_titulo
            ),
        }
    return pagina(request, sesion, "balance.html", contexto, seccion="lavadero/balance")


@router.get("/balance/ticket", response_class=HTMLResponse)
def ticket_del_reporte(
    request: Request, sesion: SesionBD, _: Admin, fecha: date | None = None
) -> Response:
    """El reporte del día impreso, para firmar o dejar en la caja."""
    b = balance_del_lavadero.balance(sesion, periodo_de("dia", min(fecha or hoy(), hoy())))
    contexto = {"b": b, "nombre_del_medio": NOMBRE_DEL_MEDIO}
    return ticket(request, "tickets/reporte_lavadero.html", contexto, ajustes_del_ticket(sesion))
