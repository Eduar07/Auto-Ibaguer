"""La pantalla de configuración: negocio y horario, bot, puestos, menú y precios, y el aviso
de datos personales.

Todo se guarda junto con un solo botón: si algo no sirve, no se guarda nada y cada campo
dice qué le falta. Las reglas viven en app.catalogo.configuracion (parámetros, precios y
servicios) y en app.turnos.casos_de_uso (puestos, porque mueven la fila).
"""

from dataclasses import dataclass
from datetime import timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.datastructures import FormData

from app import formato, tiempo
from app.catalogo import configuracion
from app.catalogo.parametros import leer_parametros
from app.catalogo.tablas import Fila, Puesto, Servicio, ServicioPrecio, TipoVehiculo
from app.errores import ReglaDelNegocio
from app.turnos import casos_de_uso as casos
from app.turnos import consultas
from app.web.comun import (
    Admin,
    SesionBD,
    con_eventos,
    entero,
    html,
    leer_pesos,
    pagina,
    verificar_origen,
)

router = APIRouter(dependencies=[Depends(verificar_origen)])

# Con cuántos días de lavados se calcula la duración real de cada servicio.
DIAS_DE_DURACION_REAL = 30


@dataclass(frozen=True)
class ServicioEnPantalla:
    id: int
    nombre: str
    descripcion: str
    precios: dict[int, int]  # tipo de vehículo -> precio vigente
    duracion_min: int | None
    real: tuple[int, int] | None  # (minutos en promedio, cuántos lavados)


@dataclass(frozen=True)
class FilaEnConfiguracion:
    id: int
    nombre: str
    tipos: list[TipoVehiculo]
    servicios: list[ServicioEnPantalla]
    puestos: list[Puesto]


def _orden_natural(puesto: Puesto) -> tuple[int, str]:
    return (len(puesto.nombre), puesto.nombre)  # C2 antes que C10


def _filas(sesion: Session) -> list[FilaEnConfiguracion]:
    reales = consultas.duracion_real_por_servicio(
        sesion, desde=tiempo.ahora() - timedelta(days=DIAS_DE_DURACION_REAL)
    )
    vigentes: dict[int, dict[int, ServicioPrecio]] = {}
    for precio in sesion.scalars(select(ServicioPrecio).where(ServicioPrecio.activo)):
        vigentes.setdefault(precio.servicio_id, {})[precio.tipo_vehiculo_id] = precio
    filas = []
    for fila in sesion.scalars(select(Fila).order_by(Fila.id)):
        tipos = list(
            sesion.scalars(
                select(TipoVehiculo)
                .where(TipoVehiculo.fila_id == fila.id)
                .order_by(TipoVehiculo.id)
            )
        )
        servicios = []
        for servicio in sesion.scalars(
            select(Servicio)
            .where(Servicio.fila_id == fila.id, Servicio.activo)
            .order_by(Servicio.orden, Servicio.id)
        ):
            precios = vigentes.get(servicio.id, {})
            duraciones = [precios[t.id].duracion_min for t in tipos if t.id in precios]
            servicios.append(
                ServicioEnPantalla(
                    id=servicio.id,
                    nombre=servicio.nombre,
                    descripcion=servicio.descripcion,
                    precios={tipo_id: p.precio for tipo_id, p in precios.items()},
                    duracion_min=max(duraciones) if duraciones else None,
                    real=reales.get(servicio.id),
                )
            )
        puestos = sorted(
            sesion.scalars(select(Puesto).where(Puesto.fila_id == fila.id)), key=_orden_natural
        )
        filas.append(FilaEnConfiguracion(fila.id, fila.nombre, tipos, servicios, puestos))
    return filas


def _telefono_para_mostrar(telefono: str) -> str:
    """ "573227556850" -> "322 755 6850" """
    local = telefono.removeprefix("57") if len(telefono) == 12 else telefono
    return f"{local[:3]} {local[3:6]} {local[6:]}" if len(local) == 10 else telefono


def _valores_guardados(sesion: Session, filas: list[FilaEnConfiguracion]) -> dict[str, Any]:
    """Lo que muestra cada campo, tal como está guardado."""
    parametros = leer_parametros(sesion)
    valores: dict[str, Any] = {
        "nombre_negocio": parametros.nombre_negocio,
        "telefono_contacto": _telefono_para_mostrar(parametros.telefono_contacto),
        "hora_apertura": parametros.hora_apertura.strftime("%H:%M"),
        "hora_cierre": parametros.hora_cierre.strftime("%H:%M"),
        "dias_atencion": sorted(parametros.dias_atencion),
        "bot_activo": parametros.bot_activo,
        "gracia_minutos": str(int(parametros.gracia.total_seconds() // 60)),
        "avisar_cuando_falten": str(parametros.avisar_cuando_falten),
        "sesion_bot_minutos": str(int(parametros.sesion_bot.total_seconds() // 60)),
        "aviso_datos": parametros.aviso_datos,
    }
    for fila in filas:
        for puesto in fila.puestos:
            valores[f"puesto-{puesto.id}"] = puesto.activo
        for servicio in fila.servicios:
            for tipo_id, precio in servicio.precios.items():
                valores[f"precio-{servicio.id}-{tipo_id}"] = formato.miles(precio)
            valores[f"duracion-{servicio.id}"] = str(servicio.duracion_min or "")
    return valores


def _valores_escritos(formulario: FormData) -> dict[str, Any]:
    """Lo que el administrador escribió, para devolvérselo si algo no se pudo guardar."""
    valores: dict[str, Any] = {clave: valor for clave, valor in formulario.items()}
    valores["dias_atencion"] = [
        int(dia) for dia in formulario.getlist("dias_atencion") if dia.isdigit()
    ]
    valores["bot_activo"] = formulario.get("bot_activo") == "si"
    for clave in list(valores):
        if clave.startswith("puesto-"):
            valores[clave] = valores[clave] == "si"
    return valores


def _formulario(
    request: Request,
    sesion: Session,
    *,
    valores: dict[str, Any] | None = None,
    errores: dict[str, str] | None = None,
) -> HTMLResponse:
    filas = _filas(sesion)
    contexto = {
        "filas": filas,
        "valores": valores if valores is not None else _valores_guardados(sesion, filas),
        "errores": errores or {},
        "dias_de_duracion_real": DIAS_DE_DURACION_REAL,
    }
    return html(request, "_formulario_configuracion.html", contexto)


@router.get("/configuracion", response_class=HTMLResponse)
def pagina_configuracion(request: Request, sesion: SesionBD, _: Admin) -> Response:
    filas = _filas(sesion)
    contexto = {
        "filas": filas,
        "valores": _valores_guardados(sesion, filas),
        "errores": {},
        "dias_de_duracion_real": DIAS_DE_DURACION_REAL,
    }
    return pagina(request, sesion, "configuracion.html", contexto, seccion="configuracion")


@router.post("/configuracion", response_class=HTMLResponse)
async def guardar(request: Request, sesion: SesionBD, _: Admin) -> Response:
    formulario = await request.form()
    errores: dict[str, str] = {}

    parametros = {
        clave: formulario.get(clave, "")
        for clave in (
            "nombre_negocio",
            "telefono_contacto",
            "hora_apertura",
            "hora_cierre",
            "gracia_minutos",
            "avisar_cuando_falten",
            "sesion_bot_minutos",
            "aviso_datos",
        )
    }
    parametros["dias_atencion"] = formulario.getlist("dias_atencion")
    parametros["bot_activo"] = formulario.get("bot_activo") == "si"
    try:
        configuracion.guardar_parametros(sesion, parametros)
    except configuracion.ConfiguracionInvalida as error:
        errores.update(error.errores)

    for fila in _filas(sesion):
        for servicio in fila.servicios:
            duracion = entero(str(formulario.get(f"duracion-{servicio.id}", "")).strip())
            for tipo in fila.tipos:
                campo = f"precio-{servicio.id}-{tipo.id}"
                if campo not in formulario:
                    continue
                try:
                    configuracion.cambiar_precio(
                        sesion,
                        servicio.id,
                        tipo.id,
                        precio=leer_pesos(str(formulario[campo])),
                        duracion_min=duracion,
                    )
                except configuracion.ConfiguracionInvalida as error:
                    if "duracion" in error.errores:
                        errores[f"duracion-{servicio.id}"] = error.errores["duracion"]
                    else:
                        errores[campo] = error.errores.get("precio", str(error))
        # Solo los puestos que estaban en la pantalla: si alguien agregó uno en otro
        # celular mientras tanto, este formulario no lo apaga.
        en_pantalla = set(formulario.getlist("puestos_en_pantalla"))
        for puesto in fila.puestos:
            if str(puesto.id) not in en_pantalla:
                continue
            prendido = formulario.get(f"puesto-{puesto.id}") == "si"
            try:
                casos.cambiar_puesto(sesion, puesto.id, activo=prendido)
            except ReglaDelNegocio as error:
                errores[f"puesto-{puesto.id}"] = str(error)

    if errores:
        sesion.rollback()
        respuesta = _formulario(
            request, sesion, valores=_valores_escritos(formulario), errores=errores
        )
        return con_eventos(respuesta, error="No se guardó nada: revisa los campos marcados.")
    sesion.commit()
    respuesta = _formulario(request, sesion)
    return con_eventos(respuesta, aviso="Configuración guardada. El bot y la fila ya la usan.")


# --- Agregar puestos y servicios ------------------------------------------------------------


@router.post("/configuracion/puestos", response_class=HTMLResponse)
def agregar_puesto(
    request: Request, sesion: SesionBD, _: Admin, fila_id: Annotated[int, Form()]
) -> Response:
    if sesion.get(Fila, fila_id) is None:
        raise HTTPException(status_code=404, detail="Esa fila no existe.")
    puesto = casos.agregar_puesto(sesion, fila_id)
    nombre = puesto.nombre
    sesion.commit()
    respuesta = html(
        request,
        "_puesto_en_configuracion.html",
        {"puesto": puesto, "valores": {f"puesto-{puesto.id}": True}, "errores": {}},
    )
    respuesta.headers["HX-Retarget"] = f"#puestos-{fila_id}"
    respuesta.headers["HX-Reswap"] = "beforeend"
    return con_eventos(
        respuesta, aviso=f"Puesto {nombre} agregado y prendido. Ya cuenta en la fila."
    )


def _fila_o_404(sesion: Session, fila_id: int) -> FilaEnConfiguracion:
    fila = next((f for f in _filas(sesion) if f.id == fila_id), None)
    if fila is None:
        raise HTTPException(status_code=404, detail="Esa fila no existe.")
    return fila


@router.get("/configuracion/servicios/nuevo", response_class=HTMLResponse)
def dialogo_servicio(request: Request, sesion: SesionBD, _: Admin, fila_id: int) -> Response:
    contexto = {
        "fila": _fila_o_404(sesion, fila_id),
        "formulario": {"duracion": "60"},
        "errores": {},
    }
    return html(request, "dialogos/agregar_servicio.html", contexto)


@router.post("/configuracion/servicios", response_class=HTMLResponse)
async def agregar_servicio(request: Request, sesion: SesionBD, _: Admin) -> Response:
    formulario = await request.form()
    fila = _fila_o_404(sesion, entero(str(formulario.get("fila_id", ""))) or 0)
    precios = {
        tipo.id: leer_pesos(str(formulario.get(f"precio-{tipo.id}", ""))) for tipo in fila.tipos
    }
    try:
        servicio = configuracion.agregar_servicio(
            sesion,
            fila_id=fila.id,
            nombre=str(formulario.get("nombre", "")),
            descripcion=str(formulario.get("descripcion", "")),
            precios=precios,
            duracion_min=entero(str(formulario.get("duracion", "")).strip()),
        )
        nombre, servicio_id = servicio.nombre, servicio.id
        sesion.commit()
    except configuracion.ConfiguracionInvalida as error:
        sesion.rollback()
        contexto = {"fila": fila, "formulario": dict(formulario), "errores": error.errores}
        return html(request, "dialogos/agregar_servicio.html", contexto)
    fila = _fila_o_404(sesion, fila.id)
    nuevo = next(s for s in fila.servicios if s.id == servicio_id)
    valores = {f"precio-{nuevo.id}-{t}": formato.miles(p) for t, p in nuevo.precios.items()}
    valores[f"duracion-{nuevo.id}"] = str(nuevo.duracion_min)
    respuesta = html(
        request,
        "_servicio_en_configuracion.html",
        {"fila": fila, "servicio": nuevo, "valores": valores, "errores": {}},
    )
    respuesta.headers["HX-Retarget"] = f"#servicios-{fila.id}"
    respuesta.headers["HX-Reswap"] = "beforeend"
    return con_eventos(
        respuesta,
        cerrar_dialogo=True,
        aviso=f"{nombre} ya está en el menú de {fila.nombre.lower()}.",
    )
