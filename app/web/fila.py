"""La fila en vivo y los botones de cada turno.

Las páginas se arman en el servidor (plantillas Jinja2) y HTMX las actualiza por partes:
un botón le pide una acción al servidor y el servidor devuelve la fila ya actualizada.
Las reglas no viven aquí: aquí solo se llaman los casos de uso de app.turnos.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.sse import EventSourceResponse, ServerSentEvent
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import formato, tiempo
from app.catalogo.tablas import Fila, Puesto, Servicio, ServicioPrecio, TipoVehiculo
from app.clientes import servicio as clientes
from app.clientes.tablas import Vehiculo
from app.config import obtener_entorno
from app.db.sesion import obtener_motor
from app.errores import ReglaDelNegocio
from app.turnos import caja as caja_del_lavadero
from app.turnos import casos_de_uso as casos
from app.turnos import consultas, datos, reglas
from app.turnos.estados import Actor, MedioPago, OrigenTurno
from app.turnos.tablas import Turno
from app.web import en_vivo
from app.web.comun import (
    Admin,
    NecesitaEntrar,
    SesionBD,
    administrador_de_la_cookie,
    con_eventos,
    entero,
    html,
    leer_pesos,
    pagina,
    pagina_actual,
    sin_cambios,
    verificar_origen,
)
from app.web.tablero import TurnoEnPantalla, armar_tablero

router = APIRouter(dependencies=[Depends(verificar_origen)])

# --- Respuestas de la fila ------------------------------------------------------------


def _tablero(request: Request, sesion: Session, **eventos: Any) -> Response:
    respuesta = html(request, "_tablero.html", {"tablero": armar_tablero(sesion)})
    # Aunque la acción venga de un diálogo, lo que se actualiza es la fila.
    respuesta.headers["HX-Retarget"] = "#tablero"
    respuesta.headers["HX-Reswap"] = "innerHTML"
    return con_eventos(respuesta, **eventos) if eventos else respuesta


def _accion(
    request: Request,
    sesion: Session,
    hacer: Callable[[], str],
    *,
    dialogo: str | None = None,
    contexto: Callable[[], dict[str, Any]] | None = None,
) -> Response:
    """Corre un caso de uso y confirma (commit). Devuelve la fila actualizada.

    `hacer` devuelve el mensaje de éxito. Si una regla no lo permite, se deshace todo y
    se explica: dentro del diálogo, si la acción venía de uno, o como aviso en la fila.
    """
    try:
        mensaje = hacer()
        sesion.commit()
    except ReglaDelNegocio as error:
        sesion.rollback()
        if dialogo and contexto:
            return html(request, dialogo, {**contexto(), "error": str(error)})
        return _tablero(request, sesion, error=str(error))
    if pagina_actual(request) not in ("", "/fila"):
        # Por ejemplo, "Dar turno" desde la ficha de un cliente: allá no hay fila que
        # actualizar; la pantalla se entera con "datos-cambiados".
        return sin_cambios(cerrar_dialogo=True, aviso=mensaje, datos_cambiados=True)
    return _tablero(request, sesion, cerrar_dialogo=True, aviso=mensaje)


def _numero(sesion: Session, turno_id: int) -> str:
    turno = sesion.get(Turno, turno_id)
    if turno is None:
        raise HTTPException(status_code=404, detail="Ese turno no existe.")
    return reglas.numero_de_turno(sesion.get(Fila, turno.fila_id).prefijo, turno.consecutivo)


def _turno_en_pantalla(sesion: Session, turno_id: int) -> TurnoEnPantalla:
    tablero = armar_tablero(sesion)
    activos = [
        *tablero.por_cobrar,
        *(t for fila in tablero.filas for t in fila.espera),
        *(p.turno for fila in tablero.filas for p in fila.puestos if p.turno),
    ]
    for turno in activos:
        if turno.id == turno_id:
            return turno
    raise HTTPException(status_code=404, detail="Ese turno ya no está activo.")


@dataclass(frozen=True)
class OpcionDeServicio:
    id: int
    nombre: str
    precio: int


def _menu(sesion: Session) -> dict[int, list[OpcionDeServicio]]:
    """Servicios activos con su precio, por tipo de vehículo, en el orden del menú."""
    consulta = (
        select(ServicioPrecio.tipo_vehiculo_id, Servicio.id, Servicio.nombre, ServicioPrecio.precio)
        .join(Servicio, Servicio.id == ServicioPrecio.servicio_id)
        .where(ServicioPrecio.activo, Servicio.activo)
        .order_by(Servicio.orden, Servicio.id)
    )
    menu: dict[int, list[OpcionDeServicio]] = {}
    for tipo_id, servicio_id, nombre, precio in sesion.execute(consulta):
        menu.setdefault(tipo_id, []).append(OpcionDeServicio(servicio_id, nombre, precio))
    return menu


def _tipos_de_vehiculo(sesion: Session, fila_id: int | None = None) -> list[TipoVehiculo]:
    """Moto, Automóvil y Camioneta, en el orden del diseño."""
    consulta = select(TipoVehiculo).order_by(TipoVehiculo.fila_id.desc(), TipoVehiculo.id)
    if fila_id is not None:
        consulta = consulta.where(TipoVehiculo.fila_id == fila_id)
    return list(sesion.scalars(consulta))


# --- La fila en vivo -------------------------------------------------------------------


@router.get("/fila", response_class=HTMLResponse)
def pagina_fila(request: Request, sesion: SesionBD, _: Admin) -> Response:
    return pagina(
        request, sesion, "fila.html", {"tablero": armar_tablero(sesion)}, seccion="lavadero/fila"
    )


@router.get("/fila/tablero", response_class=HTMLResponse)
def fragmento_tablero(request: Request, sesion: SesionBD, _: Admin) -> Response:
    return html(request, "_tablero.html", {"tablero": armar_tablero(sesion)})


def _administrador_para_eventos(request: Request) -> int:
    """Revisa la sesión con una conexión que se suelta de una vez: los eventos duran
    minutos y no deben dejar ocupada una conexión a la base de datos."""
    with Session(obtener_motor()) as sesion:
        administrador = administrador_de_la_cookie(request, sesion)
        if administrador is None:
            raise NecesitaEntrar()
        return administrador.id


@router.get("/eventos", response_class=EventSourceResponse)
async def eventos(_: Annotated[int, Depends(_administrador_para_eventos)]):
    """Le avisa al navegador cada vez que una fila cambia, para que se actualice."""
    async for fila_id in en_vivo.cambios(obtener_entorno().database_url):
        yield ServerSentEvent(event="cambio", raw_data=str(fila_id))


# --- Nuevo turno en el local -----------------------------------------------------------


@dataclass(frozen=True)
class VistaPrevia:
    """El número que recibiría el turno nuevo y cuánto esperaría, para decírselo antes."""

    numero: str
    fila: str
    resumen: consultas.ResumenDeLaFila


def _vista_previa(sesion: Session, tipo: TipoVehiculo) -> VistaPrevia:
    fila = sesion.get(Fila, tipo.fila_id)
    hoy = tiempo.hora_local(tiempo.ahora()).date()
    ultimo = sesion.scalar(
        select(func.max(Turno.consecutivo)).where(Turno.fila_id == fila.id, Turno.fecha == hoy)
    )
    return VistaPrevia(
        numero=reglas.numero_de_turno(fila.prefijo, (ultimo or 0) + 1),
        fila=fila.nombre.lower(),
        resumen=consultas.resumen_de_la_fila(sesion, fila.id),
    )


def _contexto_nuevo_turno(sesion: Session, formulario: dict[str, Any]) -> dict[str, Any]:
    tipos = _tipos_de_vehiculo(sesion)
    elegido = next((t for t in tipos if t.id == formulario.get("tipo_vehiculo_id")), None)
    if elegido is None:
        # Lo más común es un automóvil: queda elegido para ahorrar un toque.
        elegido = next((t for t in tipos if t.nombre == "Automóvil"), tipos[0])
        formulario["tipo_vehiculo_id"] = elegido.id
    return {
        "tipos": tipos,
        "menu": _menu(sesion),
        "formulario": formulario,
        "vista": _vista_previa(sesion, elegido),
    }


def _formulario_para_cliente(sesion: Session, cliente_id: int) -> dict[str, Any]:
    """ "Dar turno" desde la ficha: el cliente ya existe; se propone su último vehículo."""
    cliente = clientes.cliente_vigente(sesion, cliente_id)
    vehiculo = sesion.scalars(
        select(Vehiculo)
        .where(Vehiculo.cliente_id == cliente.id)
        .order_by(Vehiculo.placa.is_(None), Vehiculo.id.desc())
    ).first()
    return {
        "cliente_id": cliente.id,
        "nombre": cliente.nombre,
        "celular": cliente.telefono or "",
        "tipo_vehiculo_id": vehiculo.tipo_vehiculo_id if vehiculo else None,
        "placa": vehiculo.placa or "" if vehiculo else "",
        "servicio_id": "basico",
        "acepta_datos": True,
    }


@router.get("/turnos/nuevo", response_class=HTMLResponse)
def dialogo_nuevo_turno(
    request: Request, sesion: SesionBD, _: Admin, cliente_id: int | None = None
) -> Response:
    if cliente_id is not None:
        try:
            formulario = _formulario_para_cliente(sesion, cliente_id)
        except ReglaDelNegocio as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
    else:
        # "basico": el diseño deja elegido el lavado Básico del tipo de vehículo.
        formulario = {"servicio_id": "basico"}
    return html(request, "dialogos/nuevo_turno.html", _contexto_nuevo_turno(sesion, formulario))


@router.get("/turnos/nuevo/vista-previa", response_class=HTMLResponse)
def vista_previa_del_turno(
    request: Request, sesion: SesionBD, _: Admin, tipo_vehiculo_id: int
) -> Response:
    tipo = sesion.get(TipoVehiculo, tipo_vehiculo_id)
    if tipo is None:
        raise HTTPException(status_code=404, detail="Ese tipo de vehículo no existe.")
    return html(request, "dialogos/vista_previa.html", {"vista": _vista_previa(sesion, tipo)})


@router.post("/turnos", response_class=HTMLResponse)
def crear_turno_en_el_local(
    request: Request,
    sesion: SesionBD,
    administrador: Admin,
    nombre: Annotated[str, Form()] = "",
    celular: Annotated[str, Form()] = "",
    tipo_vehiculo_id: Annotated[str, Form()] = "",
    placa: Annotated[str, Form()] = "",
    servicio_id: Annotated[str, Form()] = "",
    acepta_datos: Annotated[str, Form()] = "",
    cliente_id: Annotated[str, Form()] = "",
) -> Response:
    formulario = {
        "cliente_id": entero(cliente_id),
        "nombre": nombre,
        "celular": celular,
        "tipo_vehiculo_id": entero(tipo_vehiculo_id),
        "placa": placa,
        "servicio_id": servicio_id,
        "acepta_datos": bool(acepta_datos),
    }

    def hacer() -> str:
        if formulario["tipo_vehiculo_id"] is None:
            raise ReglaDelNegocio("Elige el tipo de vehículo.")
        if formulario["cliente_id"] is not None:
            cliente = clientes.cliente_vigente(sesion, formulario["cliente_id"])
        else:
            cliente = clientes.registrar_cliente(
                sesion,
                nombre=nombre,
                telefono=celular.strip() or None,
                acepta_datos=bool(acepta_datos),
            )
        vehiculo = clientes.registrar_vehiculo(
            sesion,
            cliente_id=cliente.id,
            tipo_vehiculo_id=formulario["tipo_vehiculo_id"],
            placa=placa.strip() or None,
        )
        nuevo = casos.pedir_turno(
            sesion,
            vehiculo_id=vehiculo.id,
            origen=OrigenTurno.DASHBOARD,
            servicio_id=entero(servicio_id),
            administrador_id=administrador.id,
        )
        mensaje = f"Turno {nuevo.numero} para {cliente.nombre.rstrip('.')}."
        if cliente.telefono:
            mensaje += " Pídele que escanee el código QR para recibir los avisos."
        return mensaje

    return _accion(
        request,
        sesion,
        hacer,
        dialogo="dialogos/nuevo_turno.html",
        contexto=lambda: _contexto_nuevo_turno(sesion, formulario),
    )


# --- Acciones sobre un turno -----------------------------------------------------------


@router.post("/turnos/{turno_id}/llegada", response_class=HTMLResponse)
def marcar_llegada(
    request: Request, sesion: SesionBD, administrador: Admin, turno_id: int
) -> Response:
    numero = _numero(sesion, turno_id)

    def hacer() -> str:
        casos.marcar_llegada(
            sesion, turno_id, actor=Actor.ADMINISTRADOR, administrador_id=administrador.id
        )
        return f"{numero} ya está en el local."

    return _accion(request, sesion, hacer)


def _contexto_puesto(sesion: Session, turno_id: int) -> dict[str, Any]:
    turno = _turno_en_pantalla(sesion, turno_id)
    libres = [
        puesto
        for puesto in datos.puestos_activos(sesion, turno.fila_id)
        if datos.turno_en_el_puesto(sesion, puesto.id) is None
    ]
    return {"turno": turno, "libres": libres}


@router.get("/turnos/{turno_id}/puesto", response_class=HTMLResponse)
def dialogo_puesto(request: Request, sesion: SesionBD, _: Admin, turno_id: int) -> Response:
    return html(request, "dialogos/puesto.html", _contexto_puesto(sesion, turno_id))


@router.post("/turnos/{turno_id}/puesto", response_class=HTMLResponse)
def pasar_a_puesto(
    request: Request,
    sesion: SesionBD,
    administrador: Admin,
    turno_id: int,
    puesto_id: Annotated[int, Form()],
    dialogo: Annotated[str, Form()] = "",
) -> Response:
    numero = _numero(sesion, turno_id)

    def hacer() -> str:
        casos.pasar_a_puesto(sesion, turno_id, puesto_id, administrador_id=administrador.id)
        return f"{numero} pasó al puesto {sesion.get(Puesto, puesto_id).nombre}."

    return _accion(
        request,
        sesion,
        hacer,
        dialogo="dialogos/puesto.html" if dialogo else None,
        contexto=lambda: _contexto_puesto(sesion, turno_id),
    )


@router.post("/turnos/{turno_id}/listo", response_class=HTMLResponse)
def marcar_listo(
    request: Request, sesion: SesionBD, administrador: Admin, turno_id: int
) -> Response:
    numero = _numero(sesion, turno_id)

    def hacer() -> str:
        casos.marcar_listo(sesion, turno_id, administrador_id=administrador.id)
        return f"{numero} quedó listo. Ya se puede cobrar."

    return _accion(request, sesion, hacer)


def _contexto_cobro(sesion: Session, turno_id: int, formulario: dict[str, Any]) -> dict[str, Any]:
    turno = _turno_en_pantalla(sesion, turno_id)
    return {
        "turno": turno,
        "servicios": _menu(sesion).get(turno.tipo_vehiculo_id, []),
        "formulario": formulario,
        "ahora": tiempo.ahora(),
        "caja_abierta": caja_del_lavadero.caja_abierta(sesion) is not None,
    }


@router.get("/turnos/{turno_id}/cobrar", response_class=HTMLResponse)
def dialogo_cobrar(request: Request, sesion: SesionBD, _: Admin, turno_id: int) -> Response:
    turno = _turno_en_pantalla(sesion, turno_id)
    formulario = {
        "servicio_id": turno.servicio_id,
        "precio_cobrado": turno.precio,
        "motivo": "",
        "medio_pago": MedioPago.EFECTIVO.value,
        "placa": "",
    }
    return html(request, "dialogos/cobrar.html", _contexto_cobro(sesion, turno_id, formulario))


@router.post("/turnos/{turno_id}/cobrar", response_class=HTMLResponse)
def cobrar(
    request: Request,
    sesion: SesionBD,
    administrador: Admin,
    turno_id: int,
    servicio_id: Annotated[str, Form()] = "",
    precio_cobrado: Annotated[str, Form()] = "",
    motivo: Annotated[str, Form()] = "",
    medio_pago: Annotated[str, Form()] = MedioPago.EFECTIVO.value,
    placa: Annotated[str, Form()] = "",
) -> Response:
    numero = _numero(sesion, turno_id)
    formulario = {
        "servicio_id": entero(servicio_id),
        "precio_cobrado": leer_pesos(precio_cobrado),
        "motivo": motivo,
        "medio_pago": medio_pago,
        "placa": placa,
    }

    def hacer() -> str:
        if formulario["servicio_id"] is None:
            raise ReglaDelNegocio("Elige el servicio que se hizo.")
        if medio_pago not in {medio.value for medio in MedioPago}:
            raise ReglaDelNegocio("Elige el medio de pago.")
        casos.corregir_turno(
            sesion,
            turno_id,
            servicio_id=formulario["servicio_id"],
            administrador_id=administrador.id,
        )
        pago = casos.cobrar(
            sesion,
            turno_id,
            medio_pago=MedioPago(medio_pago),
            precio_cobrado=formulario["precio_cobrado"],
            motivo=motivo,
            placa=placa.strip() or None,
            administrador_id=administrador.id,
        )
        medio = pago.medio_pago.value
        return f"{numero} cobrado: {formato.pesos(pago.precio_cobrado)} en {medio}."

    return _accion(
        request,
        sesion,
        hacer,
        dialogo="dialogos/cobrar.html",
        contexto=lambda: _contexto_cobro(sesion, turno_id, formulario),
    )


def _contexto_correccion(
    sesion: Session, turno_id: int, formulario: dict[str, Any]
) -> dict[str, Any]:
    turno = _turno_en_pantalla(sesion, turno_id)
    return {
        "turno": turno,
        "tipos": _tipos_de_vehiculo(sesion, turno.fila_id),
        "menu": _menu(sesion),
        "formulario": formulario,
    }


@router.get("/turnos/{turno_id}/corregir", response_class=HTMLResponse)
def dialogo_corregir(request: Request, sesion: SesionBD, _: Admin, turno_id: int) -> Response:
    turno = _turno_en_pantalla(sesion, turno_id)
    formulario = {
        "tipo_vehiculo_id": turno.tipo_vehiculo_id,
        "placa": turno.placa or "",
        "servicio_id": turno.servicio_id,
    }
    return html(
        request, "dialogos/corregir.html", _contexto_correccion(sesion, turno_id, formulario)
    )


@router.post("/turnos/{turno_id}/corregir", response_class=HTMLResponse)
def corregir(
    request: Request,
    sesion: SesionBD,
    administrador: Admin,
    turno_id: int,
    tipo_vehiculo_id: Annotated[str, Form()] = "",
    placa: Annotated[str, Form()] = "",
    servicio_id: Annotated[str, Form()] = "",
) -> Response:
    numero = _numero(sesion, turno_id)
    formulario = {
        "tipo_vehiculo_id": entero(tipo_vehiculo_id),
        "placa": placa,
        "servicio_id": entero(servicio_id),
    }

    def hacer() -> str:
        casos.corregir_turno(
            sesion,
            turno_id,
            tipo_vehiculo_id=formulario["tipo_vehiculo_id"],
            servicio_id=formulario["servicio_id"],
            placa=placa.strip() or None,
            administrador_id=administrador.id,
        )
        return f"{numero} corregido."

    return _accion(
        request,
        sesion,
        hacer,
        dialogo="dialogos/corregir.html",
        contexto=lambda: _contexto_correccion(sesion, turno_id, formulario),
    )


@router.get("/turnos/{turno_id}/cancelar", response_class=HTMLResponse)
def dialogo_cancelar(request: Request, sesion: SesionBD, _: Admin, turno_id: int) -> Response:
    return html(request, "dialogos/cancelar.html", {"turno": _turno_en_pantalla(sesion, turno_id)})


@router.post("/turnos/{turno_id}/cancelar", response_class=HTMLResponse)
def cancelar(
    request: Request,
    sesion: SesionBD,
    administrador: Admin,
    turno_id: int,
    motivo: Annotated[str, Form()] = "",
) -> Response:
    numero = _numero(sesion, turno_id)

    def hacer() -> str:
        casos.cancelar(
            sesion,
            turno_id,
            actor=Actor.ADMINISTRADOR,
            administrador_id=administrador.id,
            detalle=motivo.strip() or None,
        )
        return f"{numero} cancelado."

    return _accion(
        request,
        sesion,
        hacer,
        dialogo="dialogos/cancelar.html",
        contexto=lambda: {"turno": _turno_en_pantalla(sesion, turno_id), "motivo": motivo},
    )
