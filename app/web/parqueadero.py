"""Las pantallas del parqueadero: entrada y salida, caja, mensualidades, tarifas y tickets.

Las reglas no viven aquí (están en app.parqueadero): aquí se leen los formularios, se llama
el caso de uso, se confirma (commit) y se le dice al navegador qué actualizar o imprimir.

Imprimir: el servidor manda el evento "imprimir" con la dirección del ticket y el navegador
lo carga en un marco escondido y abre la ventana de impresión (dashboard.js). Los tickets
son páginas aparte, con el tamaño del papel de la impresora térmica (58 u 80 mm).
"""

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date, timedelta
from typing import Annotated, Any
from urllib.parse import urlencode

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session

from app import formato, tiempo
from app.config import obtener_entorno
from app.db.sesion import AbrirSesion, abrir_sesion
from app.errores import ReglaDelNegocio
from app.integraciones.correo import CorreoNoEnviado
from app.parqueadero import casos_de_uso as casos
from app.parqueadero import consultas, graficas, reglas, reporte
from app.parqueadero.balance import (
    DIAS_MAXIMOS_DEL_RANGO,
    TIPOS_DE_PERIODO,
    Periodo,
    rango,
    total_recogido,
)
from app.parqueadero.balance import balance as calcular_balance
from app.parqueadero.balance import periodo as periodo_de
from app.parqueadero.errores import CobroNoCorregible, ValorInvalido
from app.parqueadero.estados import NOMBRE_DEL_TIPO, EstadoParqueo, TipoParqueo
from app.parqueadero.tablas import (
    Caja,
    CorreccionCobro,
    Mensualidad,
    PagoMensualidad,
    Parqueo,
)
from app.parqueadero.tarifas import ANCHOS_DE_TICKET, guardar_ajustes, leer_ajustes
from app.turnos.estados import MedioPago
from app.web.caja import (
    CajaDeUnNegocio,
    CobroParaCorregir,
    RutasDeCaja,
    caja_abierta_pintada,
    caja_de_la_pantalla,
    rutas_de_caja,
)
from app.web.comun import (
    Admin,
    SesionBD,
    con_eventos,
    entero,
    html,
    leer_pesos,
    pagina,
    plantillas,
    sin_cambios,
    verificar_origen,
)

router = APIRouter(prefix="/parqueadero", dependencies=[Depends(verificar_origen)])


plantillas.env.filters["dia_corto"] = formato.dia_corto
plantillas.env.globals.update(
    TipoParqueo=TipoParqueo, nombre_del_tipo=NOMBRE_DEL_TIPO, como_se_pago=reglas.como_se_pago
)

MEDIOS = list(MedioPago)


def _medio(texto: str) -> MedioPago | None:
    return MedioPago(texto) if texto in {medio.value for medio in MedioPago} else None


def _con_retarget(respuesta: Response, destino: str) -> Response:
    respuesta.headers["HX-Retarget"] = destino
    respuesta.headers["HX-Reswap"] = "innerHTML"
    return respuesta


def _hoy() -> date:
    return tiempo.hora_local(tiempo.ahora()).date()


def _caja_de_otro_dia(caja: Caja | None) -> bool:
    """La caja abierta viene de un día anterior: alguien olvidó cerrarla al final del día."""
    return caja is not None and tiempo.hora_local(caja.abierta_en).date() < _hoy()


# --- La pantalla principal: entrada, salida y quién está adentro -------------------------


def _contexto_adentro(sesion: Session) -> dict[str, Any]:
    vehiculos = consultas.adentro(sesion)
    return {"vehiculos": vehiculos, "conteo": consultas.contar_por_tipo(vehiculos)}


def _lista_adentro(request: Request, sesion: Session, **eventos: Any) -> Response:
    respuesta = html(request, "_parqueadero_adentro.html", _contexto_adentro(sesion))
    return con_eventos(respuesta, **eventos) if eventos else respuesta


def _por_cobrar(sesion: Session) -> list[consultas.FilaMensualidad]:
    """Las mensualidades vencidas o sin pagar, para tenerlas a la vista donde se atiende."""
    return [fila for fila in consultas.mensualidades(sesion) if fila.por_cobrar]


@router.get("", response_class=HTMLResponse)
def pagina_parqueadero(request: Request, sesion: SesionBD, _: Admin) -> Response:
    caja = casos.caja_abierta(sesion)
    contexto = {
        **_contexto_adentro(sesion),
        "caja": caja,
        "caja_de_otro_dia": _caja_de_otro_dia(caja),
        "por_cobrar": _por_cobrar(sesion),
        "ajustes": leer_ajustes(sesion),
    }
    return pagina(request, sesion, "parqueadero.html", contexto, seccion="parqueadero/entradas")


@router.get("/adentro", response_class=HTMLResponse)
def fragmento_adentro(request: Request, sesion: SesionBD, _: Admin) -> Response:
    return _lista_adentro(request, sesion)


@router.get("/por-cobrar", response_class=HTMLResponse)
def fragmento_por_cobrar(request: Request, sesion: SesionBD, _: Admin) -> Response:
    return html(request, "_parqueadero_por_cobrar.html", {"por_cobrar": _por_cobrar(sesion)})


@router.post("/entradas", response_class=HTMLResponse)
def registrar_entrada(
    request: Request,
    sesion: SesionBD,
    administrador: Admin,
    placa: Annotated[str, Form()] = "",
    tipo: Annotated[str, Form()] = "",
    observaciones: Annotated[str, Form()] = "",
) -> Response:
    try:
        if tipo not in {t.value for t in TipoParqueo}:
            raise ReglaDelNegocio("Elige si es carro o moto.")
        parqueo = casos.registrar_entrada(
            sesion,
            placa=placa,
            tipo=TipoParqueo(tipo),
            observaciones=observaciones,
            administrador_id=administrador.id,
        )
        liquidacion = casos.liquidar(sesion, parqueo)
        sesion.commit()
    except ReglaDelNegocio as error:
        sesion.rollback()
        return sin_cambios(error=str(error))
    if liquidacion.es_mensualista_al_dia:
        hasta = formato.fecha(liquidacion.al_dia_hasta)
        return _lista_adentro(
            request,
            sesion,
            aviso=f"{parqueo.placa} entró. Es mensualista al día hasta el {hasta}.",
            limpiar_entrada=True,
        )
    aviso = f"{parqueo.placa} entró con el ticket {parqueo.id}."
    if liquidacion.debe_la_mensualidad:
        aviso += " Su mensualidad está pendiente de pago: cóbrale el mes."
    return _lista_adentro(
        request,
        sesion,
        aviso=aviso,
        imprimir=f"/parqueadero/tickets/{parqueo.id}",
        limpiar_entrada=True,
    )


def _parqueo_adentro(sesion: Session, parqueo_id: int) -> Parqueo:
    parqueo = sesion.get(Parqueo, parqueo_id)
    if parqueo is None or parqueo.estado is not EstadoParqueo.ADENTRO:
        raise HTTPException(status_code=404, detail="Ese vehículo ya no está adentro.")
    return parqueo


def _contexto_salida(
    sesion: Session, parqueo: Parqueo, formulario: dict[str, Any], error: str | None = None
) -> dict[str, Any]:
    liquidacion = casos.liquidar(sesion, parqueo)
    debe_desde = None
    if liquidacion.debe_la_mensualidad:
        ultimo = casos.pagada_hasta(sesion, liquidacion.mensualidad.id)
        if ultimo is not None and ultimo < _hoy():
            debe_desde = ultimo + timedelta(days=1)
    return {
        "parqueo": parqueo,
        "liquidacion": liquidacion,
        "debe_desde": debe_desde,
        "caja": casos.caja_abierta(sesion),
        "formulario": formulario,
        "medios": MEDIOS,
        "error": error,
    }


def _formulario_salida_inicial() -> dict[str, Any]:
    return {
        "medio_pago": MedioPago.EFECTIVO.value,
        "valor_cobrado": None,
        "motivo": "",
        "recibido": None,
    }


@router.get("/salida", response_class=HTMLResponse)
def buscar_salida(request: Request, sesion: SesionBD, _: Admin, q: str = "") -> Response:
    """Lo que se escribe en "Salida": una placa o un número de ticket."""
    if not q.strip():
        return sin_cambios(error="Escribe la placa o el número del ticket.")
    parqueo = casos.buscar_adentro(sesion, q)
    if parqueo is None:
        return sin_cambios(error=f"No hay ningún vehículo adentro con {q.strip().upper()}.")
    contexto = _contexto_salida(sesion, parqueo, _formulario_salida_inicial())
    return html(request, "dialogos/parqueadero_salida.html", contexto)


@router.get("/{parqueo_id}/salida", response_class=HTMLResponse)
def dialogo_salida(request: Request, sesion: SesionBD, _: Admin, parqueo_id: int) -> Response:
    parqueo = _parqueo_adentro(sesion, parqueo_id)
    contexto = _contexto_salida(sesion, parqueo, _formulario_salida_inicial())
    return html(request, "dialogos/parqueadero_salida.html", contexto)


@router.post("/{parqueo_id}/salida", response_class=HTMLResponse)
def cobrar_salida(
    request: Request,
    sesion: SesionBD,
    administrador: Admin,
    parqueo_id: int,
    medio_pago: Annotated[str, Form()] = "",
    valor_cobrado: Annotated[str, Form()] = "",
    motivo: Annotated[str, Form()] = "",
    recibido: Annotated[str, Form()] = "",
) -> Response:
    formulario = {
        "medio_pago": medio_pago,
        "valor_cobrado": leer_pesos(valor_cobrado),
        "motivo": motivo,
        "recibido": leer_pesos(recibido),
    }
    try:
        parqueo = casos.cobrar_salida(
            sesion,
            parqueo_id,
            medio_pago=_medio(medio_pago),
            valor_cobrado=formulario["valor_cobrado"],
            motivo=motivo,
            recibido=formulario["recibido"],
            administrador_id=administrador.id,
        )
        sesion.commit()
    except ReglaDelNegocio as error:
        sesion.rollback()
        actual = sesion.get(Parqueo, parqueo_id)
        if actual is None or actual.estado is not EstadoParqueo.ADENTRO:
            # Ya salió (por ejemplo, otro celular lo cobró): se cierra y se avisa.
            return _con_retarget(
                _lista_adentro(request, sesion, cerrar_dialogo=True, error=str(error)), "#adentro"
            )
        contexto = _contexto_salida(sesion, actual, formulario, error=str(error))
        return html(request, "dialogos/parqueadero_salida.html", contexto)
    valor = parqueo.valor_cobrado or 0
    if valor == 0:
        mensaje = f"{parqueo.placa} salió sin pagar" + (
            ": mensualista al día." if parqueo.mensualidad_id else "."
        )
        eventos: dict[str, Any] = {"aviso": mensaje}
    else:
        mensaje = f"{parqueo.placa} salió: {formato.pesos(valor)} en {parqueo.medio_pago.value}."
        if parqueo.recibido and parqueo.recibido > valor:
            mensaje += f" Vuelto: {formato.pesos(parqueo.recibido - valor)}."
        eventos = {"aviso": mensaje, "imprimir": f"/parqueadero/tickets/{parqueo.id}/salida"}
    return _con_retarget(
        _lista_adentro(request, sesion, cerrar_dialogo=True, **eventos), "#adentro"
    )


@router.get("/{parqueo_id}/anular", response_class=HTMLResponse)
def dialogo_anular(request: Request, sesion: SesionBD, _: Admin, parqueo_id: int) -> Response:
    parqueo = _parqueo_adentro(sesion, parqueo_id)
    return html(request, "dialogos/parqueadero_anular.html", {"parqueo": parqueo, "motivo": ""})


@router.post("/{parqueo_id}/anular", response_class=HTMLResponse)
def anular_entrada(
    request: Request,
    sesion: SesionBD,
    administrador: Admin,
    parqueo_id: int,
    motivo: Annotated[str, Form()] = "",
) -> Response:
    try:
        parqueo = casos.anular_entrada(
            sesion, parqueo_id, motivo=motivo, administrador_id=administrador.id
        )
        sesion.commit()
    except ReglaDelNegocio as error:
        sesion.rollback()
        parqueo = sesion.get(Parqueo, parqueo_id)
        contexto = {"parqueo": parqueo, "motivo": motivo, "error": str(error)}
        return html(request, "dialogos/parqueadero_anular.html", contexto)
    aviso = f"Se anuló la entrada de {parqueo.placa} (ticket {parqueo.id})."
    return _con_retarget(
        _lista_adentro(request, sesion, cerrar_dialogo=True, aviso=aviso), "#adentro"
    )


# --- Tickets para imprimir ---------------------------------------------------------------


def _ticket(
    request: Request, sesion: Session, plantilla: str, contexto: dict[str, Any]
) -> Response:
    ajustes = leer_ajustes(sesion)
    respuesta = html(request, plantilla, {**contexto, "ajustes": ajustes})
    # El dashboard carga el ticket en un marco escondido para imprimirlo: solo él puede.
    respuesta.headers["X-Frame-Options"] = "SAMEORIGIN"
    respuesta.headers["Content-Security-Policy"] = "frame-ancestors 'self'"
    return respuesta


@router.get("/tickets/{parqueo_id}", response_class=HTMLResponse)
def ticket_de_entrada(request: Request, sesion: SesionBD, _: Admin, parqueo_id: int) -> Response:
    parqueo = sesion.get(Parqueo, parqueo_id)
    if parqueo is None:
        raise HTTPException(status_code=404, detail="Ese ticket no existe.")
    return _ticket(request, sesion, "tickets/entrada.html", {"parqueo": parqueo})


@router.get("/tickets/{parqueo_id}/salida", response_class=HTMLResponse)
def ticket_de_salida(request: Request, sesion: SesionBD, _: Admin, parqueo_id: int) -> Response:
    parqueo = sesion.get(Parqueo, parqueo_id)
    if parqueo is None or parqueo.estado is not EstadoParqueo.SALIO:
        raise HTTPException(status_code=404, detail="Ese vehículo no ha salido.")
    return _ticket(request, sesion, "tickets/salida.html", {"parqueo": parqueo})


# --- Caja (app/web/caja.py: las mismas pantallas que la del lavadero) ----------------------


def obtener_abrir_sesion() -> AbrirSesion:
    """Con qué abrir una sesión para lo que corre después de contestar (el correo con el
    reporte al cerrar la caja). Las pruebas la cambian por una dentro de su transacción."""
    return abrir_sesion


def _al_cerrar(tareas: BackgroundTasks, abrir: AbrirSesion, dia_de_la_caja: date) -> None:
    """Después de contestar el cierre: el reporte de ese día a los jefes, si hay correo."""
    tareas.add_task(reporte.enviar_al_cerrar, abrir, dia_de_la_caja)


CAJA = CajaDeUnNegocio(
    negocio=casos.NEGOCIO,
    prefijo="/parqueadero",
    rutas=RutasDeCaja(
        caja="/parqueadero/caja",
        exportar="/parqueadero/exportar",
        exportar_cierres="/parqueadero/exportar-cierres",
    ),
    seccion="parqueadero/caja",
    plantilla="parqueadero_caja.html",
    que_se_cobra="salidas y mensualidades",
    nombre_del_archivo="parqueadero",
    caja_abierta=casos.caja_abierta,
    caja_por_numero=casos.caja_del_parqueadero,
    abrir=casos.abrir_caja,
    cerrar=casos.cerrar_caja,
    corregir_base=casos.corregir_base,
    corregir_conteo=casos.corregir_conteo,
    resumen=consultas.resumen_de_caja,
    movimientos=consultas.movimientos_de_caja,
    cierres=consultas.cierres,
    todos_los_cierres=consultas.todos_los_cierres,
    quienes_cerraron=consultas.quienes_cerraron,
    lineas_para_excel=consultas.lineas_para_excel,
    ajustes_del_ticket=leer_ajustes,
    obtener_abrir_sesion=obtener_abrir_sesion,
    al_cerrar=_al_cerrar,
)
router_de_la_caja = rutas_de_caja(CAJA)


# --- Balance y reporte del día --------------------------------------------------------------


def _enlace_balance(
    p: Periodo, ver: str = "plata", tipo: str = "todos", fecha: date | None = None
) -> str:
    """La dirección del Balance de un periodo, con lo que se está viendo en las gráficas. Con
    `fecha`, la semana o el mes van con ese día (al cambiar de día a mes, se queda en él)."""
    consulta = {"periodo": p.tipo}
    if p.tipo == "rango":
        consulta |= {"desde": p.desde.isoformat(), "hasta": p.hasta.isoformat()}
    else:
        consulta["fecha"] = (fecha or p.desde).isoformat()
    if ver != "plata":
        consulta["ver"] = ver
    if tipo != "todos":
        consulta["tipo"] = tipo
    return f"/parqueadero/balance?{urlencode(consulta)}"


def _ultimos(dias: int, hoy: date) -> Periodo:
    return rango(hoy - timedelta(days=dias - 1), hoy)


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
    tipo: str = "todos",
) -> Response:
    """Lo que entró y lo que pasó en un día, una semana, un mes o los días que se escojan
    (hasta 92). El del día es también el reporte para los jefes: se imprime, se manda por
    WhatsApp o por correo. La semana, el mes y el rango traen las gráficas, que se pueden ver
    en plata o en vehículos, de todos o solo de carros o de motos."""
    hoy = _hoy()
    ver = ver if ver in graficas.QUE_SE_VE else "plata"
    tipo = tipo if tipo in graficas.DE_QUIEN else "todos"
    # Del futuro no hay nada que ver: una fecha que no ha llegado muestra hoy.
    if periodo == "rango":
        p = rango(
            min(_fecha(desde) or hoy - timedelta(days=29), hoy), min(_fecha(hasta) or hoy, hoy)
        )
    else:
        tipo_de_periodo = periodo if periodo in TIPOS_DE_PERIODO else "dia"
        p = periodo_de(tipo_de_periodo, min(_fecha(fecha) or hoy, hoy))
    b = calcular_balance(sesion, p)

    def ir(otro: Periodo) -> str:
        return _enlace_balance(otro, ver, tipo)

    referencia = hoy if p.contiene(hoy) else p.desde
    pestanas = [
        (
            nombre,
            _enlace_balance(periodo_de(clave, referencia), ver, tipo, referencia),
            clave == p.tipo,
        )
        for clave, nombre in (("dia", "Día"), ("semana", "Semana"), ("mes", "Mes"))
    ]
    pestanas.append(("Rango", ir(p if p.tipo == "rango" else _ultimos(30, hoy)), p.tipo == "rango"))
    contexto: dict[str, Any] = {
        "b": b,
        "p": p,
        "hoy": hoy,
        "anterior": total_recogido(sesion, p.anterior()),
        "nombre_del_anterior": p.nombre_del_anterior(hoy),
        "nombre_del_medio": reporte.NOMBRE_DEL_MEDIO,
        "pestanas": pestanas,
        "ir_anterior": ir(p.anterior()),
        "ir_siguiente": ir(p.siguiente()) if p.hasta < hoy else None,
        "ir_a_hoy": (
            ir(_ultimos(p.cuantos_dias, hoy) if p.tipo == "rango" else periodo_de(p.tipo, hoy))
            if not p.contiene(hoy)
            else None
        ),
        "ultimos_30": _enlace_balance(_ultimos(30, hoy), ver, tipo),
        "ultimos_90": _enlace_balance(_ultimos(90, hoy), ver, tipo),
        "dias_maximos": DIAS_MAXIMOS_DEL_RANGO,
        "ver": ver,
        "tipo": tipo,
        "vehiculo": None,
    }
    if p.tipo != "dia":
        vehiculo = None if tipo == "todos" else TipoParqueo(tipo)
        contexto |= {
            "vehiculo": vehiculo,
            "opciones_ver": [
                (nombre, _enlace_balance(p, clave, tipo), clave == ver)
                for clave, nombre in graficas.QUE_SE_VE.items()
            ],
            "opciones_tipo": [
                (nombre, _enlace_balance(p, ver, clave), clave == tipo)
                for clave, nombre in graficas.DE_QUIEN.items()
            ],
            "dia_a_dia": graficas.dia_por_dia(
                b, hoy, ver, vehiculo, lambda dia: ir(periodo_de("dia", dia))
            ),
            "por_dia_de_la_semana": graficas.por_dia_de_la_semana(b, hoy, ver, vehiculo),
        }
    if p.tipo == "dia":
        informe = reporte.armar(sesion, p.desde, balance_del_dia=b)
        contexto |= {
            "whatsapp": reporte.enlace_de_whatsapp(informe),
            "texto_del_reporte": reporte.texto(informe, negrilla=False),
            "correo_configurado": obtener_entorno().correo_configurado,
            "destinos": leer_ajustes(sesion).destinos_del_reporte,
            "ver_el_mes": ir(periodo_de("mes", p.desde)),
        }
    return pagina(
        request, sesion, "parqueadero_balance.html", contexto, seccion="parqueadero/balance"
    )


@router.get("/balance/ticket", response_class=HTMLResponse)
def ticket_del_reporte(
    request: Request, sesion: SesionBD, _: Admin, fecha: date | None = None
) -> Response:
    informe = reporte.armar(sesion, min(fecha or _hoy(), _hoy()))
    contexto = {"reporte": informe, "nombre_del_medio": reporte.NOMBRE_DEL_MEDIO}
    return _ticket(request, sesion, "tickets/reporte.html", contexto)


@router.post("/balance/enviar", response_class=HTMLResponse)
def enviar_reporte(sesion: SesionBD, _: Admin, fecha: Annotated[str, Form()] = "") -> Response:
    """Manda ya el reporte de un día por correo (por ejemplo, para probar el correo)."""
    try:
        cuantos = reporte.enviar_por_correo(sesion, min(_fecha(fecha) or _hoy(), _hoy()))
    except CorreoNoEnviado as error:
        return sin_cambios(error=str(error))
    return sin_cambios(aviso=f"Reporte enviado a {cuantos} correo{'s' if cuantos != 1 else ''}.")


# --- Mensualidades ------------------------------------------------------------------------


def _contexto_mensualidades(sesion: Session) -> dict[str, Any]:
    filas = consultas.mensualidades(sesion)
    return {
        "filas": filas,
        "vencidas": sum(1 for fila in filas if fila.estado in ("vencida", "sin_pagos")),
    }


def _lista_mensualidades(request: Request, sesion: Session, **eventos: Any) -> Response:
    respuesta = html(request, "_parqueadero_mensualidades.html", _contexto_mensualidades(sesion))
    return con_eventos(_con_retarget(respuesta, "#lista-mensualidades"), **eventos)


@router.get("/mensualidades", response_class=HTMLResponse)
def pagina_mensualidades(request: Request, sesion: SesionBD, _: Admin) -> Response:
    return pagina(
        request,
        sesion,
        "parqueadero_mensualidades.html",
        _contexto_mensualidades(sesion),
        seccion="parqueadero/mensualidades",
    )


def _mensualidad_o_404(sesion: Session, mensualidad_id: int) -> Mensualidad:
    mensualidad = sesion.get(Mensualidad, mensualidad_id)
    if mensualidad is None:
        raise HTTPException(status_code=404, detail="Esa mensualidad no existe.")
    return mensualidad


def _formulario_mensualidad(mensualidad: Mensualidad | None = None) -> dict[str, Any]:
    if mensualidad is None:
        return {
            "placa": "",
            "tipo": TipoParqueo.CARRO.value,
            "titular": "",
            "telefono": "",
            "valor_mensual": None,
            "notas": "",
            "autorizo": False,
            "pagada_hasta": None,
        }
    return {
        "placa": mensualidad.placa,
        "tipo": mensualidad.tipo.value,
        "titular": mensualidad.titular,
        "telefono": formato.telefono(mensualidad.telefono) if mensualidad.telefono else "",
        "valor_mensual": mensualidad.valor_mensual,
        "notas": mensualidad.notas or "",
        "autorizo": True,
    }


def _dialogo_mensualidad(
    request: Request,
    mensualidad: Mensualidad | None,
    formulario: dict[str, Any],
    error: str | None = None,
) -> Response:
    contexto = {"mensualidad": mensualidad, "formulario": formulario, "error": error}
    return html(request, "dialogos/parqueadero_mensualidad.html", contexto)


@router.get("/mensualidades/nueva", response_class=HTMLResponse)
def dialogo_nueva_mensualidad(request: Request, _: Admin) -> Response:
    return _dialogo_mensualidad(request, None, _formulario_mensualidad())


def _guardar(
    request: Request,
    sesion: Session,
    hacer: Callable[[], str],
    al_fallar: Callable[[str], Response],
    extra: dict[str, Any] | None = None,
) -> Response:
    """Corre `hacer`, confirma y devuelve la lista de mensualidades actualizada. `extra` son
    eventos que `hacer` llena por el camino (por ejemplo, el recibo que hay que imprimir)."""
    try:
        mensaje = hacer()
        sesion.commit()
    except ReglaDelNegocio as error:
        sesion.rollback()
        return al_fallar(str(error))
    return _lista_mensualidades(
        request, sesion, cerrar_dialogo=True, aviso=mensaje, **(extra or {})
    )


@router.post("/mensualidades", response_class=HTMLResponse)
def crear_mensualidad(
    request: Request,
    sesion: SesionBD,
    administrador: Admin,
    placa: Annotated[str, Form()] = "",
    tipo: Annotated[str, Form()] = TipoParqueo.CARRO.value,
    titular: Annotated[str, Form()] = "",
    telefono: Annotated[str, Form()] = "",
    valor_mensual: Annotated[str, Form()] = "",
    notas: Annotated[str, Form()] = "",
    autorizo: Annotated[str, Form()] = "",
    pagada_hasta: Annotated[str, Form()] = "",
) -> Response:
    formulario = {
        "placa": placa,
        "tipo": tipo,
        "titular": titular,
        "telefono": telefono,
        "valor_mensual": leer_pesos(valor_mensual),
        "notas": notas,
        "autorizo": bool(autorizo),
        "pagada_hasta": _fecha(pagada_hasta),
    }

    def hacer() -> str:
        if tipo not in {t.value for t in TipoParqueo}:
            raise ReglaDelNegocio("Elige si es carro o moto.")
        if pagada_hasta.strip() and formulario["pagada_hasta"] is None:
            raise ReglaDelNegocio("Revisa hasta qué día ya trae pagado.")
        mensualidad = casos.crear_mensualidad(
            sesion,
            placa=placa,
            tipo=TipoParqueo(tipo),
            titular=titular,
            telefono=telefono,
            valor_mensual=formulario["valor_mensual"],
            notas=notas,
            autorizo_datos=bool(autorizo),
            pagada_hasta=formulario["pagada_hasta"],
            administrador_id=administrador.id,
        )
        if formulario["pagada_hasta"] is not None:
            return (
                f"Mensualidad de {mensualidad.placa} creada: al día hasta el "
                f"{formato.fecha(formulario['pagada_hasta'])}, sin entrar plata a la caja."
            )
        return f"Mensualidad de {mensualidad.placa} creada. Ya puedes cobrar el primer mes."

    return _guardar(
        request,
        sesion,
        hacer,
        lambda error: _dialogo_mensualidad(request, None, formulario, error),
    )


@router.get("/mensualidades/{mensualidad_id}/editar", response_class=HTMLResponse)
def dialogo_editar_mensualidad(
    request: Request, sesion: SesionBD, _: Admin, mensualidad_id: int
) -> Response:
    mensualidad = _mensualidad_o_404(sesion, mensualidad_id)
    return _dialogo_mensualidad(request, mensualidad, _formulario_mensualidad(mensualidad))


@router.post("/mensualidades/{mensualidad_id}", response_class=HTMLResponse)
def editar_mensualidad(
    request: Request,
    sesion: SesionBD,
    _: Admin,
    mensualidad_id: int,
    tipo: Annotated[str, Form()] = TipoParqueo.CARRO.value,
    titular: Annotated[str, Form()] = "",
    telefono: Annotated[str, Form()] = "",
    valor_mensual: Annotated[str, Form()] = "",
    notas: Annotated[str, Form()] = "",
) -> Response:
    mensualidad = _mensualidad_o_404(sesion, mensualidad_id)
    formulario = {
        **_formulario_mensualidad(mensualidad),
        "tipo": tipo,
        "titular": titular,
        "telefono": telefono,
        "valor_mensual": leer_pesos(valor_mensual),
        "notas": notas,
    }

    def hacer() -> str:
        if tipo not in {t.value for t in TipoParqueo}:
            raise ReglaDelNegocio("Elige si es carro o moto.")
        editada = casos.editar_mensualidad(
            sesion,
            mensualidad_id,
            tipo=TipoParqueo(tipo),
            titular=titular,
            telefono=telefono,
            valor_mensual=formulario["valor_mensual"],
            notas=notas,
        )
        return f"Mensualidad de {editada.placa} actualizada."

    return _guardar(
        request,
        sesion,
        hacer,
        lambda error: _dialogo_mensualidad(request, mensualidad, formulario, error),
    )


@router.get("/mensualidades/{mensualidad_id}/baja", response_class=HTMLResponse)
def dialogo_baja(request: Request, sesion: SesionBD, _: Admin, mensualidad_id: int) -> Response:
    mensualidad = _mensualidad_o_404(sesion, mensualidad_id)
    return html(request, "dialogos/parqueadero_baja.html", {"mensualidad": mensualidad})


@router.post("/mensualidades/{mensualidad_id}/baja", response_class=HTMLResponse)
def dar_de_baja(
    request: Request,
    sesion: SesionBD,
    _: Admin,
    mensualidad_id: int,
    borrar_datos: Annotated[str, Form()] = "",
) -> Response:
    mensualidad = _mensualidad_o_404(sesion, mensualidad_id)

    def hacer() -> str:
        casos.dar_de_baja(sesion, mensualidad_id, borrar_datos=bool(borrar_datos))
        mensaje = f"{mensualidad.placa} ya no es mensualista: desde ahora paga por horas."
        if borrar_datos:
            mensaje += " Se borraron el nombre, el celular y las notas del titular."
        return mensaje

    return _guardar(request, sesion, hacer, lambda error: sin_cambios(error=error))


def _contexto_pago(
    sesion: Session,
    mensualidad: Mensualidad,
    formulario: dict[str, Any],
    error: str | None = None,
    salida: int | None = None,
) -> dict[str, Any]:
    hoy = _hoy()
    al_dia = casos.cubierta_hasta(sesion, mensualidad.id, hoy)
    # Ya está al día por más de 3 días: este pago adelantaría un mes, y se pide confirmarlo.
    adelanto = not reglas.se_puede_cobrar(al_dia, hoy)
    return {
        "mensualidad": mensualidad,
        "formulario": formulario,
        "pagos": consultas.pagos_de_mensualidad(sesion, mensualidad.id)[:6],
        "caja": casos.caja_abierta(sesion),
        "medios": MEDIOS,
        "error": error,
        # Se abrió desde el cobro de una salida: al pagar, vuelve a esa salida.
        "salida": salida,
        "adelanto": adelanto,
        "al_dia_hasta": al_dia,
        "cobrar_desde": reglas.cobrar_desde(al_dia) if adelanto and al_dia else None,
    }


def _salida_adentro(sesion: Session, parqueo_id: int | None) -> Parqueo | None:
    parqueo = sesion.get(Parqueo, parqueo_id) if parqueo_id is not None else None
    return parqueo if parqueo is not None and parqueo.estado is EstadoParqueo.ADENTRO else None


@router.get("/mensualidades/{mensualidad_id}/pagar", response_class=HTMLResponse)
def dialogo_pagar(
    request: Request,
    sesion: SesionBD,
    _: Admin,
    mensualidad_id: int,
    salida: int | None = None,
) -> Response:
    mensualidad = _mensualidad_o_404(sesion, mensualidad_id)
    desde, hasta = casos.periodo_sugerido(sesion, mensualidad, _hoy())
    formulario = {
        "desde": desde,
        "hasta": hasta,
        "valor": mensualidad.valor_mensual,
        "medio_pago": MedioPago.EFECTIVO.value,
        "adelantado": False,
    }
    parqueo = _salida_adentro(sesion, salida)
    contexto = _contexto_pago(
        sesion, mensualidad, formulario, salida=parqueo.id if parqueo else None
    )
    return html(request, "dialogos/parqueadero_pagar_mensualidad.html", contexto)


def _fecha(texto: str | None) -> date | None:
    """Una fecha escrita como 2026-11-13; si viene vacía o no es una fecha, como si no
    viniera."""
    try:
        return date.fromisoformat(texto) if texto else None
    except ValueError:
        return None


@router.post("/mensualidades/{mensualidad_id}/pagar", response_class=HTMLResponse)
def pagar_mensualidad(
    request: Request,
    sesion: SesionBD,
    administrador: Admin,
    mensualidad_id: int,
    desde: Annotated[str, Form()] = "",
    hasta: Annotated[str, Form()] = "",
    valor: Annotated[str, Form()] = "",
    medio_pago: Annotated[str, Form()] = "",
    salida: Annotated[str, Form()] = "",
    adelantado: Annotated[str, Form()] = "",
) -> Response:
    mensualidad = _mensualidad_o_404(sesion, mensualidad_id)
    formulario = {
        "desde": _fecha(desde),
        "hasta": _fecha(hasta),
        "valor": leer_pesos(valor),
        "medio_pago": medio_pago,
        "adelantado": bool(adelantado),
    }
    salida_id = entero(salida)
    recibo: dict[str, Any] = {}

    def hacer() -> str:
        if formulario["desde"] is None or formulario["hasta"] is None:
            raise ReglaDelNegocio("Escribe desde y hasta qué día cubre el pago.")
        medio = _medio(medio_pago)
        if medio is None:
            raise ReglaDelNegocio("Elige el medio de pago.")
        pago = casos.pagar_mensualidad(
            sesion,
            mensualidad_id,
            desde=formulario["desde"],
            hasta=formulario["hasta"],
            valor=formulario["valor"],
            medio_pago=medio,
            adelantado=formulario["adelantado"],
            administrador_id=administrador.id,
        )
        recibo["imprimir"] = f"/parqueadero/mensualidades/pagos/{pago.id}/ticket"
        recibo["mensualidad_pagada"] = True  # la lista de "por cobrar" se actualiza sola
        return (
            f"Mensualidad de {mensualidad.placa} pagada hasta el {formato.fecha(pago.hasta)}: "
            f"{formato.pesos(pago.valor)} en {pago.medio_pago.value}."
        )

    def al_fallar(error: str) -> Response:
        contexto = _contexto_pago(sesion, mensualidad, formulario, error, salida=salida_id)
        return html(request, "dialogos/parqueadero_pagar_mensualidad.html", contexto)

    if salida_id is None:
        return _guardar(request, sesion, hacer, al_fallar, extra=recibo)

    # Se cobró desde la salida de su vehículo: vuelve a esa salida, que ya lo ve al día.
    try:
        mensaje = hacer()
        sesion.commit()
    except ReglaDelNegocio as error:
        sesion.rollback()
        return al_fallar(str(error))
    parqueo = _salida_adentro(sesion, salida_id)
    if parqueo is None:  # mientras tanto lo sacaron desde otro celular
        return sin_cambios(cerrar_dialogo=True, aviso=mensaje, **recibo)
    contexto = _contexto_salida(sesion, parqueo, _formulario_salida_inicial())
    respuesta = html(request, "dialogos/parqueadero_salida.html", contexto)
    return con_eventos(respuesta, aviso=mensaje, **recibo)


@router.get("/mensualidades/pagos/{pago_id}/ticket", response_class=HTMLResponse)
def ticket_de_mensualidad(request: Request, sesion: SesionBD, _: Admin, pago_id: int) -> Response:
    pago = sesion.get(PagoMensualidad, pago_id)
    # Los días que ya traía pagados no tienen recibo: esa plata no se recibió aquí.
    if pago is None or pago.caja_id is None:
        raise HTTPException(status_code=404, detail="Ese pago no existe.")
    mensualidad = sesion.get(Mensualidad, pago.mensualidad_id)
    return _ticket(
        request, sesion, "tickets/mensualidad.html", {"pago": pago, "mensualidad": mensualidad}
    )


# --- Corregir un cobro ---------------------------------------------------------------------
#
# Desde Caja > Cobros de esta caja, mientras la caja sigue abierta: otro valor u otro medio de
# pago, deshacer la salida del carro que no era o anular un mes cobrado por error. Las reglas
# (y el porqué de cada una) están en app.parqueadero.casos_de_uso, en "Correcciones".


_CobroParaCorregir = CobroParaCorregir


def _sale_de_la_caja(valor: int, medio: MedioPago | None) -> str:
    return (
        f" y los {formato.pesos(valor)} {reglas.como_se_pago(medio)} salen de la caja"
        if valor
        else ""
    )


def _cobro_de_la_salida(parqueo: Parqueo) -> _CobroParaCorregir:
    valor = parqueo.valor_cobrado or 0
    salio = tiempo.hora_local(parqueo.salida_en) if parqueo.salida_en else None
    return _CobroParaCorregir(
        placa=parqueo.placa,
        titulo=(f"Salió a {formato.hora_con_articulo(salio)}" if salio else "Salida")
        + f" · ticket {parqueo.id}",
        valor=valor,
        medio=parqueo.medio_pago,
        tarifa=parqueo.valor_calculado,
        caja_id=parqueo.caja_id,
        para_corregir=f"/parqueadero/{parqueo.id}/corregir",
        para_deshacer=f"/parqueadero/{parqueo.id}/deshacer-salida",
        nombre_deshacer="Deshacer la salida",
        explicacion_deshacer=(
            f"Para cuando se le dio salida al carro que no era: {parqueo.placa} vuelve a quedar "
            "adentro con su ticket y su hora de entrada"
            f"{_sale_de_la_caja(valor, parqueo.medio_pago)}."
        ),
        ejemplo_deshacer="Era otro carro: este sigue adentro",
    )


def _cobro_del_pago(pago: PagoMensualidad, mensualidad: Mensualidad) -> _CobroParaCorregir:
    return _CobroParaCorregir(
        placa=mensualidad.placa,
        titulo=(
            f"Mensualidad de {mensualidad.titular}, del {formato.fecha(pago.desde)} al "
            f"{formato.fecha(pago.hasta)}"
        ),
        valor=pago.valor,
        medio=pago.medio_pago,
        tarifa=None,
        caja_id=pago.caja_id,
        para_corregir=f"/parqueadero/mensualidades/pagos/{pago.id}/corregir",
        para_deshacer=f"/parqueadero/mensualidades/pagos/{pago.id}/anular",
        nombre_deshacer="Anular el pago",
        explicacion_deshacer=(
            "Para un mes cobrado dos veces o a la mensualidad equivocada: esos días vuelven a "
            f"deberse{_sale_de_la_caja(pago.valor, pago.medio_pago)}."
        ),
        ejemplo_deshacer="Se cobró dos veces el mismo mes",
    )


def _dialogo_corregir(
    request: Request,
    sesion: Session,
    cobro: _CobroParaCorregir,
    *,
    formulario: dict[str, Any] | None = None,
    accion: str = "",
    error: str | None = None,
) -> Response:
    caja = casos.caja_del_parqueadero(sesion, cobro.caja_id) if cobro.caja_id else None
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


def _caja_abierta(request: Request, sesion: Session, **eventos: Any) -> Response:
    return caja_abierta_pintada(CAJA, request, sesion, **eventos)


def _caja_de_la_pantalla(request: Request, sesion: Session, **eventos: Any) -> Response:
    return caja_de_la_pantalla(CAJA, request, sesion, **eventos)


def _no_se_corrigio(
    request: Request,
    sesion: Session,
    error: ReglaDelNegocio,
    cobro: Callable[[], _CobroParaCorregir],
    formulario: dict[str, Any],
    accion: str,
) -> Response:
    """Si el cobro ya no se puede corregir (ya se deshizo, o su caja se cerró hace más de 30
    días), el diálogo no sirve: se cierra, la caja se pinta como está y se avisa. Si no, el
    diálogo vuelve con el error y lo que se escribió."""
    if isinstance(error, CobroNoCorregible):
        return _caja_de_la_pantalla(request, sesion, cerrar_dialogo=True, error=str(error))
    return _dialogo_corregir(
        request, sesion, cobro(), formulario=formulario, accion=accion, error=str(error)
    )


def _como_quedo(correccion: CorreccionCobro) -> str:
    valor, medio = correccion.valor_despues, correccion.medio_despues
    return f"{formato.pesos(valor)} {reglas.como_se_pago(medio)}"


def _salen(correccion: CorreccionCobro) -> str:
    if not correccion.valor_antes:
        return ""
    return (
        f" Salen {formato.pesos(correccion.valor_antes)} "
        f"{reglas.como_se_pago(correccion.medio_antes)} de la caja."
    )


def _salida_para_corregir(sesion: Session, parqueo_id: int) -> Parqueo:
    parqueo = sesion.get(Parqueo, parqueo_id)
    if parqueo is None:
        raise HTTPException(status_code=404, detail="Ese ticket no existe.")
    return parqueo


@router.get("/{parqueo_id}/corregir", response_class=HTMLResponse)
def dialogo_corregir_salida(
    request: Request, sesion: SesionBD, _: Admin, parqueo_id: int
) -> Response:
    parqueo = _salida_para_corregir(sesion, parqueo_id)
    if parqueo.estado is not EstadoParqueo.SALIO:
        # Por ejemplo, ya se deshizo desde otro celular: la caja se pinta como está.
        return _caja_abierta(
            request, sesion, error=f"La salida de {parqueo.placa} ya no está cobrada."
        )
    return _dialogo_corregir(request, sesion, _cobro_de_la_salida(parqueo))


@router.post("/{parqueo_id}/corregir", response_class=HTMLResponse)
def corregir_salida(
    request: Request,
    sesion: SesionBD,
    administrador: Admin,
    parqueo_id: int,
    valor: Annotated[str, Form()] = "",
    medio_pago: Annotated[str, Form()] = "",
    motivo: Annotated[str, Form()] = "",
) -> Response:
    formulario = {"valor": leer_pesos(valor), "medio_pago": medio_pago, "motivo": motivo}
    try:
        if formulario["valor"] is None:
            raise ValorInvalido("Escribe el valor que se cobró (0 si no se cobró nada).")
        correccion = casos.corregir_salida(
            sesion,
            parqueo_id,
            valor_cobrado=formulario["valor"],
            medio_pago=medio_pago or None,
            motivo=motivo,
            administrador_id=administrador.id,
        )
        sesion.commit()
    except ReglaDelNegocio as error:
        sesion.rollback()
        parqueo = _salida_para_corregir(sesion, parqueo_id)
        return _no_se_corrigio(
            request, sesion, error, lambda: _cobro_de_la_salida(parqueo), formulario, "corregir"
        )
    aviso = f"Listo: el cobro de {correccion.placa} quedó en {_como_quedo(correccion)}."
    return _caja_de_la_pantalla(request, sesion, cerrar_dialogo=True, aviso=aviso)


@router.post("/{parqueo_id}/deshacer-salida", response_class=HTMLResponse)
def deshacer_salida(
    request: Request,
    sesion: SesionBD,
    administrador: Admin,
    parqueo_id: int,
    motivo: Annotated[str, Form()] = "",
) -> Response:
    try:
        correccion = casos.deshacer_salida(
            sesion, parqueo_id, motivo=motivo, administrador_id=administrador.id
        )
        sesion.commit()
    except ReglaDelNegocio as error:
        sesion.rollback()
        parqueo = _salida_para_corregir(sesion, parqueo_id)
        cobro = _cobro_de_la_salida(parqueo)
        formulario = {
            "valor": cobro.valor,
            "medio_pago": cobro.medio.value if cobro.medio else "",
            "motivo": motivo,
        }
        return _no_se_corrigio(request, sesion, error, lambda: cobro, formulario, "deshacer")
    aviso = (
        f"{correccion.placa} volvió a quedar adentro (ticket {correccion.parqueo_id})."
        + _salen(correccion)
    )
    return _caja_de_la_pantalla(request, sesion, cerrar_dialogo=True, aviso=aviso)


def _pago_para_corregir(sesion: Session, pago_id: int) -> tuple[PagoMensualidad, Mensualidad]:
    pago = sesion.get(PagoMensualidad, pago_id)
    if pago is None or pago.caja_id is None:
        raise HTTPException(status_code=404, detail="Ese pago no existe.")
    return pago, sesion.get(Mensualidad, pago.mensualidad_id)


@router.get("/mensualidades/pagos/{pago_id}/corregir", response_class=HTMLResponse)
def dialogo_corregir_pago(request: Request, sesion: SesionBD, _: Admin, pago_id: int) -> Response:
    if sesion.get(PagoMensualidad, pago_id) is None:
        # Ya se anuló (por ejemplo, desde otro celular): la caja se pinta como está.
        return _caja_de_la_pantalla(request, sesion, error="Ese pago ya no existe: ya se anuló.")
    return _dialogo_corregir(
        request, sesion, _cobro_del_pago(*_pago_para_corregir(sesion, pago_id))
    )


def _pago_o_caja(
    request: Request,
    sesion: Session,
    error: ReglaDelNegocio,
    pago_id: int,
    formulario: dict[str, Any],
    accion: str,
) -> Response:
    """El error de corregir o anular un pago. Si el pago ya no está, no hay diálogo que
    volver a mostrar: se pinta la caja con el aviso."""
    if sesion.get(PagoMensualidad, pago_id) is None:
        return _caja_de_la_pantalla(request, sesion, cerrar_dialogo=True, error=str(error))
    cobro = _cobro_del_pago(*_pago_para_corregir(sesion, pago_id))
    if accion == "deshacer":
        formulario = {
            "valor": cobro.valor,
            "medio_pago": cobro.medio.value if cobro.medio else "",
            "motivo": formulario["motivo"],
        }
    return _no_se_corrigio(request, sesion, error, lambda: cobro, formulario, accion)


@router.post("/mensualidades/pagos/{pago_id}/corregir", response_class=HTMLResponse)
def corregir_pago(
    request: Request,
    sesion: SesionBD,
    administrador: Admin,
    pago_id: int,
    valor: Annotated[str, Form()] = "",
    medio_pago: Annotated[str, Form()] = "",
    motivo: Annotated[str, Form()] = "",
) -> Response:
    formulario = {"valor": leer_pesos(valor), "medio_pago": medio_pago, "motivo": motivo}
    try:
        correccion = casos.corregir_pago(
            sesion,
            pago_id,
            valor=formulario["valor"] or 0,
            medio_pago=medio_pago or None,
            motivo=motivo,
            administrador_id=administrador.id,
        )
        sesion.commit()
    except ReglaDelNegocio as error:
        sesion.rollback()
        return _pago_o_caja(request, sesion, error, pago_id, formulario, "corregir")
    aviso = (
        f"Listo: el pago de la mensualidad de {correccion.placa} quedó en "
        f"{_como_quedo(correccion)}."
    )
    return _caja_de_la_pantalla(request, sesion, cerrar_dialogo=True, aviso=aviso)


@router.post("/mensualidades/pagos/{pago_id}/anular", response_class=HTMLResponse)
def anular_pago(
    request: Request,
    sesion: SesionBD,
    administrador: Admin,
    pago_id: int,
    motivo: Annotated[str, Form()] = "",
) -> Response:
    try:
        correccion = casos.anular_pago(
            sesion, pago_id, motivo=motivo, administrador_id=administrador.id
        )
        sesion.commit()
    except ReglaDelNegocio as error:
        sesion.rollback()
        return _pago_o_caja(request, sesion, error, pago_id, {"motivo": motivo}, "deshacer")
    aviso = (
        f"Se anuló el pago de la mensualidad de {correccion.placa}: esos días vuelven a "
        "deberse." + _salen(correccion)
    )
    return _caja_de_la_pantalla(request, sesion, cerrar_dialogo=True, aviso=aviso)


# --- Tarifas y ticket ---------------------------------------------------------------------


def _valores_de_ajustes(ajustes: Any) -> dict[str, Any]:
    return {
        "hora_carro": ajustes.hora_carro,
        "hora_moto": ajustes.hora_moto,
        "fraccion_carro": ajustes.fraccion_carro,
        "fraccion_moto": ajustes.fraccion_moto,
        "gracia_minutos": ajustes.gracia_minutos,
        "nombre": ajustes.nombre,
        "nit": ajustes.nit,
        "direccion": ajustes.direccion,
        "telefono": ajustes.telefono,
        "pie_ticket": ajustes.pie_ticket,
        "ancho_ticket": ajustes.ancho_ticket,
        "correos_reporte": ajustes.correos_reporte,
        "dia_carro": ajustes.dia_carro,
        "dia_moto": ajustes.dia_moto,
        "dia_siguiente_carro": ajustes.dia_siguiente_carro,
        "dia_siguiente_moto": ajustes.dia_siguiente_moto,
    }


# Para cotizar en el mostrador: "¿cuánto me cobra por 4 días?"
DIAS_PARA_COTIZAR = (1, 2, 3, 4, 7, 15)


@dataclass(frozen=True)
class Cotizacion:
    """Cuánto pagan por varios días seguidos: una fila por cantidad de días y una columna por
    tipo. Así, en el celular, "4 días" se lee de una sin correr la tabla de lado."""

    tipos: list[str]
    filas: list[tuple[int, list[int]]]


def _cotizacion(sesion: Session) -> Cotizacion | None:
    """Con las tarifas guardadas y solo los tipos que tienen cobro por días; si ninguno lo
    tiene, no hay nada que cotizar."""
    ajustes = leer_ajustes(sesion)
    tarifas = [(NOMBRE_DEL_TIPO[tipo], ajustes.tarifa(tipo)) for tipo in TipoParqueo]
    tarifas = [(nombre, tarifa) for nombre, tarifa in tarifas if tarifa.valor_dia is not None]
    if not tarifas:
        return None
    filas = [
        (
            dias,
            [
                reglas.calcular_cobro(dias * reglas.HORAS_DEL_DIA * 60, tarifa).valor
                for _, tarifa in tarifas
            ],
        )
        for dias in DIAS_PARA_COTIZAR
    ]
    return Cotizacion([nombre for nombre, _ in tarifas], filas)


def _contexto_tarifas(
    sesion: Session, valores: dict[str, Any], error: str | None = None
) -> dict[str, Any]:
    return {
        "valores": valores,
        "anchos": ANCHOS_DE_TICKET,
        "error": error,
        "correo_configurado": obtener_entorno().correo_configurado,
        "cotizacion": _cotizacion(sesion),
    }


def _formulario_tarifas(
    request: Request, sesion: Session, valores: dict[str, Any], error: str | None = None
) -> Response:
    contexto = _contexto_tarifas(sesion, valores, error)
    return html(request, "_parqueadero_tarifas.html", contexto)


@router.get("/tarifas", response_class=HTMLResponse)
def pagina_tarifas(request: Request, sesion: SesionBD, _: Admin) -> Response:
    contexto = _contexto_tarifas(sesion, _valores_de_ajustes(leer_ajustes(sesion)))
    return pagina(
        request, sesion, "parqueadero_tarifas.html", contexto, seccion="parqueadero/tarifas"
    )


@router.post("/tarifas", response_class=HTMLResponse)
def guardar_tarifas(
    request: Request,
    sesion: SesionBD,
    _: Admin,
    hora_carro: Annotated[str, Form()] = "",
    hora_moto: Annotated[str, Form()] = "",
    fraccion_carro: Annotated[str, Form()] = "",
    fraccion_moto: Annotated[str, Form()] = "",
    gracia_minutos: Annotated[str, Form()] = "",
    nombre: Annotated[str, Form()] = "",
    nit: Annotated[str, Form()] = "",
    direccion: Annotated[str, Form()] = "",
    telefono: Annotated[str, Form()] = "",
    pie_ticket: Annotated[str, Form()] = "",
    ancho_ticket: Annotated[str, Form()] = "",
    correos_reporte: Annotated[str, Form()] = "",
    dia_carro: Annotated[str, Form()] = "",
    dia_moto: Annotated[str, Form()] = "",
    dia_siguiente_carro: Annotated[str, Form()] = "",
    dia_siguiente_moto: Annotated[str, Form()] = "",
) -> Response:
    valores = {
        "hora_carro": leer_pesos(hora_carro),
        "hora_moto": leer_pesos(hora_moto),
        "fraccion_carro": leer_pesos(fraccion_carro),
        "fraccion_moto": leer_pesos(fraccion_moto),
        "gracia_minutos": entero(gracia_minutos.strip()),
        "nombre": nombre,
        "nit": nit,
        "direccion": direccion,
        "telefono": telefono,
        "pie_ticket": pie_ticket,
        "ancho_ticket": entero(ancho_ticket.strip()),
        "correos_reporte": correos_reporte,
        "dia_carro": leer_pesos(dia_carro),
        "dia_moto": leer_pesos(dia_moto),
        "dia_siguiente_carro": leer_pesos(dia_siguiente_carro),
        "dia_siguiente_moto": leer_pesos(dia_siguiente_moto),
    }
    try:
        for clave, texto in (
            ("hora_carro", "el valor de la hora de carro"),
            ("hora_moto", "el valor de la hora de moto"),
            ("gracia_minutos", "los minutos de gracia"),
            ("ancho_ticket", "el ancho del ticket"),
        ):
            if valores[clave] is None:
                raise ReglaDelNegocio(f"Escribe {texto}.")
        guardar_ajustes(sesion, replace(leer_ajustes(sesion), **valores))
        sesion.commit()
    except ReglaDelNegocio as error:
        sesion.rollback()
        return _formulario_tarifas(request, sesion, valores, str(error))
    guardados = _valores_de_ajustes(leer_ajustes(sesion))
    aviso = "Tarifas guardadas. Los vehículos que ya están adentro pagan la de cuando entraron."
    return con_eventos(_formulario_tarifas(request, sesion, guardados), aviso=aviso)


def cobro_de(parqueo: Parqueo) -> reglas.Cobro:
    """Cómo se llegó al valor de una salida (para el recibo): con la tarifa con que entró."""
    return reglas.calcular_cobro(parqueo.minutos or 0, casos.tarifa_con_que_entro(parqueo))


plantillas.env.globals["cobro_de"] = cobro_de
plantillas.env.globals["dias_para_corregir"] = reglas.DIAS_PARA_CORREGIR_UNA_CAJA_CERRADA
