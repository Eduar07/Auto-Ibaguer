"""La caja del lavadero en el dashboard: las mismas pantallas que la del parqueadero
(app/web/caja.py), con lo que entra a ella, que es el cobro de cada turno, y la corrección de
un cobro equivocado (otro valor, otro medio de pago, o deshacerlo si era el turno que no era).
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import formato, tiempo
from app.caja.errores import CobroNoCorregible, ValorInvalido
from app.caja.reglas import como_se_pago
from app.caja.tablas import CorreccionCobro
from app.catalogo.tablas import Fila, Servicio, TipoVehiculo
from app.clientes.tablas import Vehiculo
from app.db.sesion import AbrirSesion, abrir_sesion
from app.errores import ReglaDelNegocio
from app.turnos import caja as caja_del_lavadero
from app.turnos import casos_de_uso as casos
from app.turnos import consultas_de_caja as consultas
from app.turnos import reglas
from app.turnos import reporte as reporte_del_lavadero
from app.turnos.estados import EstadoTurno
from app.turnos.recibo import datos_del_ticket
from app.turnos.tablas import Pago, Turno
from app.web.caja import (
    MEDIOS,
    CajaDeUnNegocio,
    CobroParaCorregir,
    RutasDeCaja,
    caja_de_la_pantalla,
    rutas_de_caja,
    ticket,
)
from app.web.comun import Admin, SesionBD, html, leer_pesos, verificar_origen
from app.web.tablas import Administrador


def obtener_abrir_sesion() -> AbrirSesion:
    """Con qué abrir una sesión para lo que corre después de contestar (el correo con el
    reporte al cerrar la caja). Las pruebas la cambian por una dentro de su transacción."""
    return abrir_sesion


def _al_cerrar(tareas: BackgroundTasks, abrir: AbrirSesion, dia_de_la_caja: date) -> None:
    """Después de contestar el cierre: el reporte de ese día a los jefes, si hay correo."""
    tareas.add_task(reporte_del_lavadero.enviar_al_cerrar, abrir, dia_de_la_caja)


CAJA = CajaDeUnNegocio(
    negocio=caja_del_lavadero.NEGOCIO,
    prefijo="",
    rutas=RutasDeCaja(
        caja="/caja", exportar="/caja/exportar", exportar_cierres="/caja/exportar-cierres"
    ),
    seccion="lavadero/caja",
    plantilla="caja.html",
    que_se_cobra="cada turno cobrado, con el precio del menú y el descuento si hubo",
    nombre_del_archivo="lavadero",
    caja_abierta=caja_del_lavadero.caja_abierta,
    caja_por_numero=caja_del_lavadero.caja_del_lavadero,
    abrir=caja_del_lavadero.abrir_caja,
    cerrar=caja_del_lavadero.cerrar_caja,
    corregir_base=caja_del_lavadero.corregir_base,
    corregir_conteo=caja_del_lavadero.corregir_conteo,
    resumen=consultas.resumen_de_caja,
    movimientos=consultas.cobros_de_caja,
    cierres=consultas.cierres,
    todos_los_cierres=consultas.todos_los_cierres,
    quienes_cerraron=consultas.quienes_cerraron,
    lineas_para_excel=consultas.lineas_para_excel,
    ajustes_del_ticket=datos_del_ticket,
    obtener_abrir_sesion=obtener_abrir_sesion,
    al_cerrar=_al_cerrar,
)
router_de_la_caja = rutas_de_caja(CAJA)


# --- Corregir el cobro de un turno -------------------------------------------------------------

router = APIRouter(dependencies=[Depends(verificar_origen)])


def _cobro_para_corregir(sesion: Session, turno_id: int) -> CobroParaCorregir:
    """Lo que el diálogo dice del cobro de ese turno. 404 si el turno no tiene cobro."""
    fila = sesion.execute(
        select(Turno, Pago, Fila.prefijo, Vehiculo.placa, Servicio.nombre)
        .join(Pago, Pago.turno_id == Turno.id)
        .join(Fila, Fila.id == Turno.fila_id)
        .join(Vehiculo, Vehiculo.id == Turno.vehiculo_id)
        .outerjoin(Servicio, Servicio.id == Turno.servicio_id)
        .where(Turno.id == turno_id, Turno.estado == EstadoTurno.PAGADO)
    ).one_or_none()
    if fila is None:
        raise HTTPException(status_code=404, detail="Ese turno no tiene un cobro.")
    turno, pago, prefijo, placa, servicio = fila
    numero = reglas.numero_de_turno(prefijo, turno.consecutivo)
    cobrado = tiempo.hora_local(pago.fecha_hora)
    return CobroParaCorregir(
        placa=placa or "—",
        titulo=f"Turno {numero}"
        + (f" · {servicio}" if servicio else "")
        + f" · cobrado a {formato.hora_con_articulo(cobrado)}",
        valor=pago.precio_cobrado,
        medio=pago.medio_pago,
        tarifa=pago.precio_menu,
        caja_id=pago.caja_id,
        para_corregir=f"/turnos/{turno.id}/corregir-cobro",
        para_deshacer=f"/turnos/{turno.id}/deshacer-cobro",
        nombre_deshacer="Deshacer el cobro",
        explicacion_deshacer=(
            f"Para cuando se cobró el turno que no era, o dos veces: el turno {numero} vuelve a "
            "quedar listo, por cobrar"
            + (
                f", y los {formato.pesos(pago.precio_cobrado)} {como_se_pago(pago.medio_pago)} "
                "salen de la caja."
                if pago.precio_cobrado
                else "."
            )
        ),
        ejemplo_deshacer="Se le cobró al turno que no era",
        de_referencia="El precio del menú",
    )


def _dialogo(
    request: Request,
    sesion: Session,
    cobro: CobroParaCorregir,
    *,
    formulario: dict[str, Any] | None = None,
    accion: str = "",
    error: str | None = None,
) -> Response:
    caja = caja_del_lavadero.caja_del_lavadero(sesion, cobro.caja_id) if cobro.caja_id else None
    contexto = {
        "cobro": cobro,
        "caja_cerrada": caja is not None and caja.cerrada_en is not None,
        "medios": MEDIOS,
        "formulario": formulario
        or {"valor": cobro.valor, "medio_pago": cobro.medio.value if cobro.medio else ""},
        "accion": accion,
        "error": error,
    }
    return html(request, "dialogos/corregir_cobro.html", contexto)


def _no_se_corrigio(
    request: Request,
    sesion: Session,
    error: ReglaDelNegocio,
    cobro: Callable[[], CobroParaCorregir],
    formulario: dict[str, Any],
    accion: str,
) -> Response:
    """Si el cobro ya no se puede corregir (ya se deshizo, o su caja se cerró hace más de 30
    días), el diálogo no sirve: se cierra, la caja se pinta como está y se avisa. Si no, el
    diálogo vuelve con el error y lo que se escribió."""
    if isinstance(error, CobroNoCorregible):
        return caja_de_la_pantalla(CAJA, request, sesion, cerrar_dialogo=True, error=str(error))
    return _dialogo(
        request, sesion, cobro(), formulario=formulario, accion=accion, error=str(error)
    )


@router.get("/turnos/{turno_id}/corregir-cobro", response_class=HTMLResponse)
def dialogo_corregir_cobro(request: Request, sesion: SesionBD, _: Admin, turno_id: int) -> Response:
    return _dialogo(request, sesion, _cobro_para_corregir(sesion, turno_id))


@router.post("/turnos/{turno_id}/corregir-cobro", response_class=HTMLResponse)
def corregir_cobro(
    request: Request,
    sesion: SesionBD,
    administrador: Admin,
    turno_id: int,
    valor: Annotated[str, Form()] = "",
    medio_pago: Annotated[str, Form()] = "",
    motivo: Annotated[str, Form()] = "",
) -> Response:
    formulario = {"valor": leer_pesos(valor), "medio_pago": medio_pago, "motivo": motivo}
    try:
        if formulario["valor"] is None:
            raise ValorInvalido("Escribe el valor que se cobró (0 si no se cobró nada).")
        correccion = casos.corregir_cobro(
            sesion,
            turno_id,
            precio_cobrado=formulario["valor"],
            medio_pago=medio_pago or None,
            motivo=motivo,
            administrador_id=administrador.id,
        )
        sesion.commit()
    except ReglaDelNegocio as error:
        sesion.rollback()
        return _no_se_corrigio(
            request,
            sesion,
            error,
            lambda: _cobro_para_corregir(sesion, turno_id),
            formulario,
            "corregir",
        )
    aviso = f"Listo: el cobro de {correccion.placa} quedó en {_como_quedo(correccion)}."
    return caja_de_la_pantalla(CAJA, request, sesion, cerrar_dialogo=True, aviso=aviso)


@router.post("/turnos/{turno_id}/deshacer-cobro", response_class=HTMLResponse)
def deshacer_cobro(
    request: Request,
    sesion: SesionBD,
    administrador: Admin,
    turno_id: int,
    motivo: Annotated[str, Form()] = "",
) -> Response:
    try:
        correccion = casos.deshacer_cobro(
            sesion, turno_id, motivo=motivo, administrador_id=administrador.id
        )
        sesion.commit()
    except ReglaDelNegocio as error:
        sesion.rollback()
        return _no_se_corrigio(
            request,
            sesion,
            error,
            lambda: _cobro_para_corregir(sesion, turno_id),
            {"motivo": motivo},
            "deshacer",
        )
    salen = (
        f" Salen {formato.pesos(correccion.valor_antes)} "
        f"{como_se_pago(correccion.medio_antes)} de la caja."
        if correccion.valor_antes
        else ""
    )
    aviso = f"Listo: el cobro de {correccion.placa} se deshizo y el turno quedó por cobrar.{salen}"
    return caja_de_la_pantalla(CAJA, request, sesion, cerrar_dialogo=True, aviso=aviso)


def _como_quedo(correccion: CorreccionCobro) -> str:
    return f"{formato.pesos(correccion.valor_despues)} {como_se_pago(correccion.medio_despues)}"


# --- El recibo de un turno cobrado -----------------------------------------------------------


@dataclass(frozen=True)
class Recibo:
    pago: Pago
    numero: str  # "C-03"
    placa: str
    tipo: str  # "Automóvil"
    servicio: str
    atendio: str | None  # el usuario que cobró


@router.get("/turnos/{turno_id}/recibo", response_class=HTMLResponse)
def recibo(request: Request, sesion: SesionBD, _: Admin, turno_id: int) -> Response:
    """El recibo del cobro de un turno, para la impresora. 404 si el turno no tiene cobro."""
    fila = sesion.execute(
        select(Pago, Turno, Fila.prefijo, Vehiculo.placa, TipoVehiculo.nombre, Servicio.nombre)
        .join(Turno, Turno.id == Pago.turno_id)
        .join(Fila, Fila.id == Turno.fila_id)
        .join(Vehiculo, Vehiculo.id == Turno.vehiculo_id)
        .join(TipoVehiculo, TipoVehiculo.id == Vehiculo.tipo_vehiculo_id)
        .outerjoin(Servicio, Servicio.id == Turno.servicio_id)
        .where(Pago.turno_id == turno_id)
    ).one_or_none()
    if fila is None:
        raise HTTPException(status_code=404, detail="Ese turno no tiene un cobro.")
    pago, turno, prefijo, placa, tipo, servicio = fila
    atendio = sesion.get(Administrador, pago.administrador_id) if pago.administrador_id else None
    contexto = {
        "r": Recibo(
            pago=pago,
            numero=reglas.numero_de_turno(prefijo, turno.consecutivo),
            placa=placa or "—",
            tipo=tipo,
            servicio=servicio or "—",
            atendio=atendio.usuario if atendio else None,
        )
    }
    return ticket(request, "tickets/recibo_lavadero.html", contexto, datos_del_ticket(sesion))
