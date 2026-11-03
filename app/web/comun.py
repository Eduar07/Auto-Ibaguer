"""Lo que comparten todas las pantallas del dashboard.

- Las plantillas (Jinja2) y cómo se escriben en ellas los pesos, horas y fechas.
- La sesión del administrador: quién entró y la defensa contra formularios de otros sitios.
- Las respuestas: una página o un pedazo de página, con avisos para el navegador.
"""

import hashlib
import json
import re
import time
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlsplit

from fastapi import Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app import formato, tiempo
from app.catalogo.parametros import leer_parametros
from app.config import obtener_entorno
from app.db.sesion import obtener_sesion
from app.turnos.estados import NOMBRE_DEL_ESTADO, EstadoTurno, MedioPago
from app.web import sesiones
from app.web.tablas import Administrador

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
    fecha=formato.fecha,
)


def _texto_volver(request: Request) -> str:
    """El diálogo de nuevo turno se abre desde cualquier pantalla: "volver" a la que sea."""
    return "Volver a la fila" if pagina_actual(request) in ("", "/fila") else "Volver"


def _dia_relativo(dia: date) -> str:
    return formato.dia_relativo(dia, tiempo.hora_local(tiempo.ahora()).date())


plantillas.env.globals.update(
    texto_volver=_texto_volver,
    dia_relativo=_dia_relativo,
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


def valor_de_cookie(administrador: Administrador) -> str:
    entorno = obtener_entorno()
    datos_de_sesion = sesiones.DatosDeSesion(
        administrador_id=administrador.id,
        huella=sesiones.huella_de_la_clave(administrador.clave_cifrada),
        vence=int(time.time()) + entorno.sesion_horas * 3600,
    )
    return sesiones.crear_valor(datos_de_sesion, entorno.clave_secreta)


def poner_cookie(respuesta: Response, valor: str) -> None:
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


def administrador_de_la_cookie(request: Request, sesion: Session) -> Administrador | None:
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
    administrador = administrador_de_la_cookie(request, sesion)
    if administrador is None:
        raise NecesitaEntrar()
    # Cada vez que usa el dashboard, la sesión se renueva por otras SESION_HORAS.
    request.state.cookie_de_sesion = valor_de_cookie(administrador)
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


# --- Respuestas ------------------------------------------------------------------------


def html(
    request: Request, plantilla: str, contexto: dict[str, Any] | None = None, *, status: int = 200
) -> HTMLResponse:
    respuesta = plantillas.TemplateResponse(request, plantilla, contexto or {}, status_code=status)
    valor = getattr(request.state, "cookie_de_sesion", None)
    if valor:
        poner_cookie(respuesta, valor)
    return respuesta


def pagina(
    request: Request,
    sesion: Session,
    plantilla: str,
    contexto: dict[str, Any],
    *,
    seccion: str,
) -> HTMLResponse:
    """Una pantalla completa, con el encabezado: en qué sección está y si el bot está activo."""
    bot_activo = leer_parametros(sesion).bot_activo
    return html(request, plantilla, {**contexto, "seccion": seccion, "bot_activo": bot_activo})


def pagina_actual(request: Request) -> str:
    """La ruta de la pantalla donde está el navegador (HTMX la manda en HX-Current-URL)."""
    return urlsplit(request.headers.get("hx-current-url", "")).path


def sin_cambios(**eventos: Any) -> Response:
    """Una respuesta que no toca la página: solo le pide cosas al navegador (avisos...)."""
    return con_eventos(Response(status_code=200, headers={"HX-Reswap": "none"}), **eventos)


def _eventos(eventos: dict[str, Any]) -> str:
    return json.dumps({nombre.replace("_", "-"): valor for nombre, valor in eventos.items()})


def con_eventos(
    respuesta: Response, *, despues: dict[str, Any] | None = None, **eventos: Any
) -> Response:
    """Eventos para el JavaScript de la página: mostrar un aviso, cerrar el diálogo...

    Los de `despues` llegan cuando el navegador ya puso el pedazo nuevo en la página.
    """
    if eventos:
        respuesta.headers["HX-Trigger"] = _eventos(eventos)
    if despues:
        respuesta.headers["HX-Trigger-After-Swap"] = _eventos(despues)
    return respuesta


def entero(texto: str) -> int | None:
    return int(texto) if texto.isdigit() else None


def leer_pesos(texto: str) -> int | None:
    """ "$40.000" o "40000" -> 40000; vacío -> None (se cobra el precio del menú)."""
    digitos = re.sub(r"\D", "", texto)
    return int(digitos) if digitos else None
