"""Las pantallas del dashboard: entrar, la fila en vivo y las acciones de cada turno.

Las páginas se arman en el servidor (plantillas Jinja2) y HTMX las actualiza por partes:
un botón le pide una acción al servidor y el servidor devuelve la fila ya actualizada.
Las reglas no viven aquí: aquí solo se llaman los casos de uso del hito 3.
"""

import hashlib
import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.sse import EventSourceResponse, ServerSentEvent
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import formato, tiempo
from app.catalogo.tablas import Fila, Puesto, Servicio, ServicioPrecio, TipoVehiculo
from app.clientes import servicio as clientes
from app.config import obtener_entorno
from app.db.sesion import obtener_motor, obtener_sesion
from app.errores import ReglaDelNegocio
from app.turnos import casos_de_uso as casos
from app.turnos import consultas, datos, reglas
from app.turnos.estados import NOMBRE_DEL_ESTADO, Actor, EstadoTurno, MedioPago, OrigenTurno
from app.turnos.tablas import Turno
from app.web import en_vivo, sesiones
from app.web.claves import cifrar_clave, verificar_clave
from app.web.tablas import Administrador
from app.web.tablero import TurnoEnPantalla, armar_tablero

SesionBD = Annotated[Session, Depends(obtener_sesion)]

# --- Plantillas ------------------------------------------------------------------------

plantillas = Jinja2Templates(directory=Path(__file__).parent / "plantillas")
CARPETA_ESTATICOS = Path(__file__).parent / "estaticos"


def _hora_local(momento: datetime) -> str:
    return formato.hora(tiempo.hora_local(momento))


def _minutos_desde(momento: datetime | None, ahora: datetime) -> int:
    if momento is None:
        return 0
    return max(0, int((ahora - momento).total_seconds() // 60))


def _segundos_hasta(momento: datetime | None, ahora: datetime) -> int:
    if momento is None:
        return 0
    return max(0, int((momento - ahora).total_seconds()))


def _estatico(ruta: str) -> str:
    """La dirección de un archivo de /estaticos con una huella de su contenido.

    Si el archivo cambia, cambia la dirección y el navegador lo descarga de nuevo, en vez
    de seguir usando la copia vieja que tenía guardada.
    """
    archivo = CARPETA_ESTATICOS / ruta
    return f"/estaticos/{ruta}?v={_huella(archivo, archivo.stat().st_mtime_ns)}"


@lru_cache(maxsize=64)
def _huella(archivo: Path, _modificado: int) -> str:
    return hashlib.sha256(archivo.read_bytes()).hexdigest()[:10]


plantillas.env.filters.update(
    pesos=formato.pesos,
    miles=lambda valor: "" if valor is None else formato.miles(valor),
    hora=_hora_local,
    duracion=formato.duracion,
    telefono_oculto=formato.telefono_oculto,
)
plantillas.env.globals.update(
    EstadoTurno=EstadoTurno,
    MedioPago=MedioPago,
    nombre_del_estado=NOMBRE_DEL_ESTADO,
    minutos_desde=_minutos_desde,
    segundos_hasta=_segundos_hasta,
    fecha_larga=lambda momento: formato.fecha_larga(tiempo.hora_local(momento).date()),
    estatico=_estatico,
)

# --- Sesión del administrador ----------------------------------------------------------


class NecesitaEntrar(Exception):
    """La persona no ha entrado o su sesión venció."""


LIMITE_DE_INTENTOS = sesiones.LimiteDeIntentos()


@lru_cache
def _clave_cifrada_de_relleno() -> str:
    """Si el usuario no existe, se verifica contra esta para tardar lo mismo que con uno
    que sí existe: así nadie puede averiguar qué usuarios hay midiendo el tiempo."""
    return cifrar_clave("usuario-que-no-existe")


def _valor_de_cookie(administrador: Administrador) -> str:
    entorno = obtener_entorno()
    datos_de_sesion = sesiones.DatosDeSesion(
        administrador_id=administrador.id,
        huella=sesiones.huella_de_la_clave(administrador.clave_cifrada),
        vence=int(time.time()) + entorno.sesion_horas * 3600,
    )
    return sesiones.crear_valor(datos_de_sesion, entorno.clave_secreta)


def _poner_cookie(respuesta: Response, valor: str) -> None:
    entorno = obtener_entorno()
    respuesta.set_cookie(
        sesiones.NOMBRE_COOKIE,
        valor,
        max_age=entorno.sesion_horas * 3600,
        httponly=True,  # el JavaScript de la página no la puede leer
        samesite="lax",  # otros sitios no la pueden usar para enviar formularios
        secure=entorno.es_produccion,  # en la nube, solo por HTTPS
        path="/",
    )


def _administrador_de_la_cookie(request: Request, sesion: Session) -> Administrador | None:
    entorno = obtener_entorno()
    datos_de_sesion = sesiones.leer_valor(
        request.cookies.get(sesiones.NOMBRE_COOKIE), entorno.clave_secreta, time.time()
    )
    if datos_de_sesion is None:
        return None
    administrador = sesion.get(Administrador, datos_de_sesion.administrador_id)
    if (
        administrador is None
        or not administrador.activo
        or sesiones.huella_de_la_clave(administrador.clave_cifrada) != datos_de_sesion.huella
    ):
        return None
    return administrador


def administrador_actual(request: Request, sesion: SesionBD) -> Administrador:
    administrador = _administrador_de_la_cookie(request, sesion)
    if administrador is None:
        raise NecesitaEntrar()
    # Cada vez que usa el dashboard, la sesión se renueva por otras SESION_HORAS.
    request.state.cookie_de_sesion = _valor_de_cookie(administrador)
    return administrador


Admin = Annotated[Administrador, Depends(administrador_actual)]


def verificar_origen(request: Request) -> None:
    """Defensa contra CSRF: un formulario de otra página no puede actuar en el dashboard.

    El navegador dice de dónde viene cada envío (encabezado Origin). Si viene de otro
    sitio, se rechaza. La cookie con SameSite=Lax es la segunda defensa.
    """
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    origen = request.headers.get("origin")
    if origen and urlsplit(origen).netloc != request.headers.get("host"):
        raise HTTPException(status_code=403, detail="Origen no permitido.")


def responder_sin_sesion(request: Request, _: Exception) -> Response:
    if request.url.path == "/eventos":
        return Response(status_code=401)
    if request.headers.get("hx-request"):
        # HTMX no recarga la página completa por su cuenta: se le pide que vaya a /entrar.
        return Response(status_code=200, headers={"HX-Redirect": "/entrar"})
    return RedirectResponse("/entrar", status_code=303)


router = APIRouter(dependencies=[Depends(verificar_origen)])

# --- Respuestas ------------------------------------------------------------------------


def _html(
    request: Request, plantilla: str, contexto: dict[str, Any] | None = None, *, status: int = 200
) -> HTMLResponse:
    respuesta = plantillas.TemplateResponse(request, plantilla, contexto or {}, status_code=status)
    valor = getattr(request.state, "cookie_de_sesion", None)
    if valor:
        _poner_cookie(respuesta, valor)
    return respuesta


def _con_eventos(respuesta: Response, **eventos: Any) -> Response:
    """Eventos para el JavaScript de la página: mostrar un aviso, cerrar el diálogo..."""
    respuesta.headers["HX-Trigger"] = json.dumps(
        {nombre.replace("_", "-"): valor for nombre, valor in eventos.items()}
    )
    return respuesta


def _tablero(request: Request, sesion: Session, **eventos: Any) -> Response:
    respuesta = _html(request, "_tablero.html", {"tablero": armar_tablero(sesion)})
    # Aunque la acción venga de un diálogo, lo que se actualiza es la fila.
    respuesta.headers["HX-Retarget"] = "#tablero"
    respuesta.headers["HX-Reswap"] = "innerHTML"
    return _con_eventos(respuesta, **eventos) if eventos else respuesta


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
            return _html(request, dialogo, {**contexto(), "error": str(error)})
        return _tablero(request, sesion, error=str(error))
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


def _entero(texto: str) -> int | None:
    return int(texto) if texto.isdigit() else None


def _leer_pesos(texto: str) -> int | None:
    """ "$40.000" o "40000" -> 40000; vacío -> None (se cobra el precio del menú)."""
    digitos = re.sub(r"\D", "", texto)
    return int(digitos) if digitos else None


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


# --- Entrar y salir --------------------------------------------------------------------


@router.get("/", include_in_schema=False)
def inicio() -> RedirectResponse:
    return RedirectResponse("/fila", status_code=303)


@router.get("/entrar", response_class=HTMLResponse)
def pagina_entrar(request: Request, sesion: SesionBD) -> Response:
    if _administrador_de_la_cookie(request, sesion) is not None:
        return RedirectResponse("/fila", status_code=303)
    return _html(request, "entrar.html")


@router.post("/entrar", response_class=HTMLResponse)
def entrar(
    request: Request,
    sesion: SesionBD,
    usuario: Annotated[str, Form()] = "",
    clave: Annotated[str, Form()] = "",
) -> Response:
    usuario = usuario.strip().lower()
    ahora = time.time()
    if LIMITE_DE_INTENTOS.bloqueado(usuario, ahora):
        return _html(
            request,
            "entrar.html",
            {"usuario": usuario, "error": "Demasiados intentos fallidos. Espera 15 minutos."},
            status=429,
        )
    administrador = sesion.scalars(
        select(Administrador).where(Administrador.usuario == usuario)
    ).one_or_none()
    cifrada = administrador.clave_cifrada if administrador else _clave_cifrada_de_relleno()
    if not (verificar_clave(cifrada, clave) and administrador and administrador.activo):
        LIMITE_DE_INTENTOS.registrar_fallo(usuario, ahora)
        return _html(
            request,
            "entrar.html",
            {"usuario": usuario, "error": "Usuario o contraseña incorrectos."},
            status=401,
        )
    LIMITE_DE_INTENTOS.registrar_exito(usuario)
    respuesta = RedirectResponse("/fila", status_code=303)
    _poner_cookie(respuesta, _valor_de_cookie(administrador))
    return respuesta


@router.post("/salir")
def salir() -> RedirectResponse:
    respuesta = RedirectResponse("/entrar", status_code=303)
    respuesta.delete_cookie(sesiones.NOMBRE_COOKIE, path="/")
    return respuesta


# --- La fila en vivo -------------------------------------------------------------------


@router.get("/fila", response_class=HTMLResponse)
def pagina_fila(request: Request, sesion: SesionBD, administrador: Admin) -> Response:
    return _html(
        request, "fila.html", {"tablero": armar_tablero(sesion), "administrador": administrador}
    )


@router.get("/fila/tablero", response_class=HTMLResponse)
def fragmento_tablero(request: Request, sesion: SesionBD, _: Admin) -> Response:
    return _html(request, "_tablero.html", {"tablero": armar_tablero(sesion)})


def _administrador_para_eventos(request: Request) -> int:
    """Revisa la sesión con una conexión que se suelta de una vez: los eventos duran
    minutos y no deben dejar ocupada una conexión a la base de datos."""
    with Session(obtener_motor()) as sesion:
        administrador = _administrador_de_la_cookie(request, sesion)
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


@router.get("/turnos/nuevo", response_class=HTMLResponse)
def dialogo_nuevo_turno(request: Request, sesion: SesionBD, _: Admin) -> Response:
    # "basico": el diseño deja elegido el lavado Básico del tipo de vehículo.
    formulario = {"servicio_id": "basico"}
    return _html(request, "dialogos/nuevo_turno.html", _contexto_nuevo_turno(sesion, formulario))


@router.get("/turnos/nuevo/vista-previa", response_class=HTMLResponse)
def vista_previa_del_turno(
    request: Request, sesion: SesionBD, _: Admin, tipo_vehiculo_id: int
) -> Response:
    tipo = sesion.get(TipoVehiculo, tipo_vehiculo_id)
    if tipo is None:
        raise HTTPException(status_code=404, detail="Ese tipo de vehículo no existe.")
    return _html(request, "dialogos/vista_previa.html", {"vista": _vista_previa(sesion, tipo)})


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
) -> Response:
    formulario = {
        "nombre": nombre,
        "celular": celular,
        "tipo_vehiculo_id": _entero(tipo_vehiculo_id),
        "placa": placa,
        "servicio_id": servicio_id,
        "acepta_datos": bool(acepta_datos),
    }

    def hacer() -> str:
        if formulario["tipo_vehiculo_id"] is None:
            raise ReglaDelNegocio("Elige el tipo de vehículo.")
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
            servicio_id=_entero(servicio_id),
            administrador_id=administrador.id,
        )
        mensaje = f"Turno {nuevo.numero} para {cliente.nombre}."
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
    return _html(request, "dialogos/puesto.html", _contexto_puesto(sesion, turno_id))


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
    return _html(request, "dialogos/cobrar.html", _contexto_cobro(sesion, turno_id, formulario))


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
        "servicio_id": _entero(servicio_id),
        "precio_cobrado": _leer_pesos(precio_cobrado),
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
    return _html(
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
        "tipo_vehiculo_id": _entero(tipo_vehiculo_id),
        "placa": placa,
        "servicio_id": _entero(servicio_id),
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
    return _html(request, "dialogos/cancelar.html", {"turno": _turno_en_pantalla(sesion, turno_id)})


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
