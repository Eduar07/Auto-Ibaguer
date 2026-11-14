"""Las pantallas de la caja, las mismas para los dos negocios: la caja abierta (o abrirla),
cerrarla contando el efectivo, una caja con su ticket, corregir su base o su conteo, los
cierres anteriores con filtros y los archivos para Excel.

Cada negocio dice lo suyo en un `CajaDeUnNegocio` (dónde quedan sus pantallas, qué entra a su
caja, cómo se resume y qué pasa al cerrarla) y recibe sus rutas con `rutas_de_caja`. Así la
caja del lavadero y la del parqueadero funcionan y se ven igual, y un arreglo sirve a las dos.
"""

import csv
import io
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Annotated, Any
from urllib.parse import urlencode

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session

from app import formato, tiempo
from app.caja import reglas
from app.caja.consultas import RESULTADOS, Cierres, FiltroDeCierres
from app.caja.errores import CajaNoCorregible, ValorInvalido
from app.caja.estados import Negocio
from app.caja.tablas import Caja, CorreccionDeCaja
from app.db.sesion import AbrirSesion, abrir_sesion
from app.errores import ReglaDelNegocio
from app.turnos.estados import MedioPago
from app.web.comun import (
    Admin,
    SesionBD,
    con_eventos,
    html,
    leer_pesos,
    pagina,
    pagina_actual,
    plantillas,
    sin_cambios,
    verificar_origen,
)

MEDIOS = list(MedioPago)


@dataclass(frozen=True)
class RutasDeCaja:
    """Dónde quedan las pantallas de la caja de un negocio (lo que usan las plantillas)."""

    caja: str  # la pantalla de la caja: "/parqueadero/caja" o "/caja"
    exportar: str  # el archivo para Excel con cada cobro
    exportar_cierres: str  # el de los cierres


@dataclass(frozen=True)
class CajaDeUnNegocio:
    """Lo que las pantallas de la caja necesitan saber de un negocio."""

    negocio: Negocio
    prefijo: str  # el de las rutas del negocio: "/parqueadero" o ""
    rutas: RutasDeCaja
    seccion: str  # la del menú: "parqueadero/caja" o "lavadero/caja"
    plantilla: str  # la página de la caja del negocio
    que_se_cobra: str  # lo que entra a su caja, para el archivo de Excel
    nombre_del_archivo: str  # "parqueadero" → parqueadero-2026-11-01-a-2026-11-13.csv
    # Sus casos de uso
    caja_abierta: Callable[[Session], Caja | None]
    caja_por_numero: Callable[[Session, int], Caja | None]
    abrir: Callable[..., Caja]
    cerrar: Callable[..., Caja]
    corregir_base: Callable[..., CorreccionDeCaja]
    corregir_conteo: Callable[..., CorreccionDeCaja]
    # Sus consultas
    resumen: Callable[[Session, Caja], Any]
    movimientos: Callable[[Session, int], list]
    cierres: Callable[[Session, FiltroDeCierres, int], Cierres]
    todos_los_cierres: Callable[[Session, FiltroDeCierres], list]
    quienes_cerraron: Callable[[Session], list[tuple[int, str]]]
    lineas_para_excel: Callable[[Session, date, date], Iterable[tuple[str, ...]]]
    # El nombre, el NIT y el ancho del papel que salen en el ticket.
    ajustes_del_ticket: Callable[[Session], Any]
    # Con qué abrir una sesión para lo que corre después de contestar el cierre, y qué corre
    # (el parqueadero manda el reporte del día a los jefes). Las pruebas cambian la sesión.
    obtener_abrir_sesion: Callable[[], AbrirSesion] = lambda: abrir_sesion  # noqa: E731
    al_cerrar: Callable[[BackgroundTasks, AbrirSesion, date], None] | None = None

    def relativa(self, ruta: str) -> str:
        """Una ruta de `rutas` sin el prefijo del negocio (como la registra su router)."""
        return ruta.removeprefix(self.prefijo)


@dataclass(frozen=True)
class CobroParaCorregir:
    """Lo que el diálogo de corrección de un cobro dice de él y a dónde manda cada formulario
    (el mismo diálogo para los dos negocios)."""

    placa: str
    titulo: str
    valor: int
    medio: MedioPago | None
    tarifa: int | None  # lo que daba la tarifa o el menú, si se sabe
    caja_id: int | None
    para_corregir: str
    para_deshacer: str
    nombre_deshacer: str
    explicacion_deshacer: str
    ejemplo_deshacer: str
    de_referencia: str = "La tarifa"  # "La tarifa daba $6.000", "El menú daba $40.000"


# --- Ayudantes ---------------------------------------------------------------------------


def hoy() -> date:
    return tiempo.hora_local(tiempo.ahora()).date()


def fecha(texto: str | None) -> date | None:
    """Una fecha escrita como 2026-11-13; si viene vacía o no es una fecha, como si no
    viniera."""
    try:
        return date.fromisoformat(texto) if texto else None
    except ValueError:
        return None


def fecha_y_hora(momento: datetime) -> str:
    """ "12/11/2026 8:05 a.m.", en Bogotá: corto, para el ticket y el Excel."""
    local = tiempo.hora_local(momento)
    return f"{local:%d/%m/%Y} {formato.hora(local)}"


plantillas.env.filters["fecha_y_hora"] = fecha_y_hora


def con_retarget(respuesta: Response, destino: str) -> Response:
    respuesta.headers["HX-Retarget"] = destino
    respuesta.headers["HX-Reswap"] = "innerHTML"
    return respuesta


def caja_de_otro_dia(caja: Caja | None) -> bool:
    """La caja abierta viene de un día anterior: alguien olvidó cerrarla al final del día."""
    return caja is not None and tiempo.hora_local(caja.abierta_en).date() < hoy()


def ticket(request: Request, plantilla: str, contexto: dict[str, Any], ajustes: Any) -> Response:
    """Un ticket para la impresora: el dashboard lo carga en un marco escondido para
    imprimirlo, y solo él puede."""
    respuesta = html(request, plantilla, {**contexto, "ajustes": ajustes})
    respuesta.headers["X-Frame-Options"] = "SAMEORIGIN"
    respuesta.headers["Content-Security-Policy"] = "frame-ancestors 'self'"
    return respuesta


def archivo_para_excel(lineas: Iterable[Iterable[Any]], nombre: str) -> Response:
    """Separado por punto y coma y con la marca UTF-8 al comienzo: así Excel en español lo
    abre con las columnas y las tildes bien, sin pasos extra."""
    salida = io.StringIO()
    escritor = csv.writer(salida, delimiter=";")
    for linea in lineas:
        escritor.writerow(linea)
    return Response(
        content="﻿" + salida.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{nombre}"'},
    )


def filtro_de_cierres(
    desde: str | None, hasta: str | None, resultado: str, quien: str | None
) -> FiltroDeCierres:
    """Lo que llega en la dirección, limpio: una fecha que no es fecha o un resultado que no
    existe se ignoran, y si las fechas vienen al revés se voltean."""
    inicio, fin = fecha(desde), fecha(hasta)
    if inicio and fin and fin < inicio:
        inicio, fin = fin, inicio
    return FiltroDeCierres(
        desde=inicio,
        hasta=fin,
        resultado=resultado if resultado in RESULTADOS else "todos",
        quien=int(quien) if quien and quien.isdigit() else None,
    )


def consulta_de_cierres(filtro: FiltroDeCierres, **mas: Any) -> str:
    """El filtro escrito en la dirección (sin lo que está vacío), para los enlaces."""
    datos = {
        "desde": filtro.desde.isoformat() if filtro.desde else None,
        "hasta": filtro.hasta.isoformat() if filtro.hasta else None,
        "resultado": filtro.resultado if filtro.resultado != "todos" else None,
        "quien": filtro.quien,
    } | mas
    return urlencode({clave: valor for clave, valor in datos.items() if valor})


# --- Lo que se pinta ---------------------------------------------------------------------


def contexto_de_la_caja(
    c: CajaDeUnNegocio,
    sesion: Session,
    caja: Caja | None,
    filtro: FiltroDeCierres | None = None,
    pagina_de_cierres: int = 1,
) -> dict[str, Any]:
    filtro = filtro or FiltroDeCierres()
    cierres = c.cierres(sesion, filtro, pagina_de_cierres)
    return {
        "rutas": c.rutas,
        "que_se_cobra": c.que_se_cobra,
        "caja": caja,
        "caja_de_otro_dia": caja_de_otro_dia(caja),
        "resumen": c.resumen(sesion, caja) if caja else None,
        "movimientos": c.movimientos(sesion, caja.id) if caja else [],
        "cierres": cierres,
        "filtro": filtro,
        "resultados": RESULTADOS,
        "quienes": c.quienes_cerraron(sesion),
        "pagina_anterior": (
            f"{c.rutas.caja}?" + consulta_de_cierres(filtro, pagina=cierres.pagina - 1)
            if cierres.pagina > 1
            else None
        ),
        "pagina_siguiente": (
            f"{c.rutas.caja}?" + consulta_de_cierres(filtro, pagina=cierres.pagina + 1)
            if cierres.pagina < cierres.paginas
            else None
        ),
        "exportar_cierres": f"{c.rutas.exportar_cierres}?" + consulta_de_cierres(filtro),
        "medios": MEDIOS,
        "exportar_desde": hoy().replace(day=1),
        "exportar_hasta": hoy(),
    }


def contexto_de_una_caja(c: CajaDeUnNegocio, sesion: Session, caja: Caja) -> dict[str, Any]:
    cerrada_en = caja.cerrada_en
    return {
        "rutas": c.rutas,
        "resumen": c.resumen(sesion, caja),
        "movimientos": c.movimientos(sesion, caja.id),
        "medios": MEDIOS,
        "se_puede_corregir": reglas.caja_corregible(cerrada_en, tiempo.ahora()),
        "corregible_hasta": (
            tiempo.hora_local(cerrada_en).date()
            + timedelta(days=reglas.DIAS_PARA_CORREGIR_UNA_CAJA_CERRADA)
            if cerrada_en
            else None
        ),
    }


def caja_abierta_pintada(
    c: CajaDeUnNegocio, request: Request, sesion: Session, **eventos: Any
) -> Response:
    """La caja abierta otra vez pintada (su cuadre, sus cobros y sus correcciones)."""
    caja = c.caja_abierta(sesion)
    contexto = {
        "rutas": c.rutas,
        "caja": caja,
        "resumen": c.resumen(sesion, caja) if caja else None,
        "movimientos": c.movimientos(sesion, caja.id) if caja else [],
    }
    respuesta = html(request, "_caja_abierta.html", contexto)
    return con_retarget(con_eventos(respuesta, **eventos), "#caja-abierta")


def caja_de_la_pantalla(
    c: CajaDeUnNegocio, request: Request, sesion: Session, **eventos: Any
) -> Response:
    """Después de corregir, la caja de la pantalla donde se corrigió, otra vez pintada: la de
    una caja (/caja/12, abierta o cerrada) o, si no, la caja del día. Dónde está la pantalla
    lo dice htmx en cada pedido."""
    numero = re.fullmatch(rf"{re.escape(c.rutas.caja)}/(\d+)", pagina_actual(request))
    caja = c.caja_por_numero(sesion, int(numero.group(1))) if numero else None
    if caja is None:
        return caja_abierta_pintada(c, request, sesion, **eventos)
    respuesta = html(request, "_una_caja.html", contexto_de_una_caja(c, sesion, caja))
    return con_retarget(con_eventos(respuesta, **eventos), "#una-caja")


# --- Las rutas ---------------------------------------------------------------------------


def rutas_de_caja(c: CajaDeUnNegocio) -> APIRouter:
    """Las pantallas de la caja del negocio. Las rutas fijas van antes que /caja/{número}."""
    router = APIRouter(prefix=c.prefijo, dependencies=[Depends(verificar_origen)])

    def caja_o_404(sesion: Session, caja_id: int) -> Caja:
        caja = c.caja_por_numero(sesion, caja_id)
        if caja is None:
            raise HTTPException(status_code=404, detail="Esa caja no existe.")
        return caja

    @router.get("/caja", response_class=HTMLResponse)
    def pagina_caja(
        request: Request,
        sesion: SesionBD,
        _: Admin,
        desde: str | None = None,
        hasta: str | None = None,
        resultado: str = "todos",
        quien: str | None = None,
        pagina_de_cierres: Annotated[int, Query(alias="pagina")] = 1,
    ) -> Response:
        """La caja abierta (o abrirla) y, abajo, los cierres anteriores con sus filtros: desde
        y hasta qué día, cómo cerró y quién la cerró."""
        filtro = filtro_de_cierres(desde, hasta, resultado, quien)
        contexto = contexto_de_la_caja(c, sesion, c.caja_abierta(sesion), filtro, pagina_de_cierres)
        return pagina(request, sesion, c.plantilla, contexto, seccion=c.seccion)

    @router.get(c.relativa(c.rutas.exportar))
    def exportar(
        sesion: SesionBD, _: Admin, desde: date | None = None, hasta: date | None = None
    ) -> Response:
        """Un archivo para Excel con cada cobro entre dos días."""
        hasta = hasta or hoy()
        desde = desde or hasta.replace(day=1)
        if hasta < desde:
            desde, hasta = hasta, desde
        return archivo_para_excel(
            c.lineas_para_excel(sesion, desde, hasta),
            f"{c.nombre_del_archivo}-{desde.isoformat()}-a-{hasta.isoformat()}.csv",
        )

    @router.get(c.relativa(c.rutas.exportar_cierres))
    def exportar_cierres(
        sesion: SesionBD,
        _: Admin,
        desde: str | None = None,
        hasta: str | None = None,
        resultado: str = "todos",
        quien: str | None = None,
    ) -> Response:
        """Los cierres que se están viendo (con su filtro, todas las páginas), uno por fila,
        para Excel: cuánto entró por cada medio, lo que debía haber, lo que se contó y la
        diferencia."""
        filtro = filtro_de_cierres(desde, hasta, resultado, quien)
        lineas: list[list[Any]] = [
            [
                "Caja",
                "Abrió",
                "Abrió por",
                "Cerró",
                "Cerró por",
                "Base",
                *(medio.value.capitalize() for medio in MedioPago),
                "Entró en total",
                "Efectivo esperado",
                "Contado",
                "Diferencia",
                "Corregida después del cierre",
                "Nota del cierre",
            ]
        ]
        for r in c.todos_los_cierres(sesion, filtro):
            caja = r.caja
            lineas.append(
                [
                    caja.id,
                    fecha_y_hora(caja.abierta_en),
                    r.abrio,
                    fecha_y_hora(caja.cerrada_en) if caja.cerrada_en else "",
                    r.cerro or "",
                    caja.base,
                    *(r.cuadre.por_medio(medio) for medio in MedioPago),
                    r.cuadre.total,
                    caja.efectivo_esperado,
                    caja.efectivo_contado,
                    caja.diferencia,
                    "Sí" if r.corregida_despues_del_cierre else "No",
                    caja.nota_cierre or "",
                ]
            )
        nombre = "cierres-de-caja.csv"
        if filtro.desde or filtro.hasta:
            nombre = (
                f"cierres-{(filtro.desde or filtro.hasta).isoformat()}"
                f"-a-{(filtro.hasta or filtro.desde).isoformat()}.csv"
            )
        return archivo_para_excel(lineas, nombre)

    @router.post("/caja/abrir", response_class=HTMLResponse)
    def abrir_caja(
        sesion: SesionBD,
        administrador: Admin,
        base: Annotated[str, Form()] = "",
        nota: Annotated[str, Form()] = "",
    ) -> Response:
        try:
            c.abrir(
                sesion, base=leer_pesos(base) or 0, nota=nota, administrador_id=administrador.id
            )
            sesion.commit()
        except ReglaDelNegocio as error:
            sesion.rollback()
            return sin_cambios(error=str(error))
        return Response(status_code=200, headers={"HX-Refresh": "true"})

    @router.get("/caja/cerrar", response_class=HTMLResponse)
    def dialogo_cerrar_caja(request: Request, sesion: SesionBD, _: Admin) -> Response:
        caja = c.caja_abierta(sesion)
        if caja is None:
            return sin_cambios(error="No hay una caja abierta.")
        contexto = {
            "rutas": c.rutas,
            "resumen": c.resumen(sesion, caja),
            "contado": None,
            "nota": "",
        }
        return html(request, "dialogos/cerrar_caja.html", contexto)

    @router.post("/caja/cerrar", response_class=HTMLResponse)
    def cerrar_caja(
        request: Request,
        sesion: SesionBD,
        administrador: Admin,
        tareas: BackgroundTasks,
        abrir: Annotated[AbrirSesion, Depends(c.obtener_abrir_sesion)],
        caja_id: Annotated[int, Form()],
        efectivo_contado: Annotated[str, Form()] = "",
        nota: Annotated[str, Form()] = "",
    ) -> Response:
        contado = leer_pesos(efectivo_contado)
        try:
            if contado is None:
                raise ReglaDelNegocio("Escribe cuánto efectivo contaste en el cajón.")
            caja = c.cerrar(
                sesion,
                caja_id,
                efectivo_contado=contado,
                nota=nota,
                administrador_id=administrador.id,
            )
            # El día de la caja es el día en que se abrió: si se cierra pasada la medianoche,
            # sigue siendo el de ese día (y no uno de hoy casi vacío).
            dia_de_la_caja = tiempo.hora_local(caja.abierta_en).date()
            sesion.commit()
        except ReglaDelNegocio as error:
            sesion.rollback()
            caja = c.caja_por_numero(sesion, caja_id)
            if caja is None or not caja.abierta:
                return sin_cambios(cerrar_dialogo=True, error=str(error))
            contexto = {
                "rutas": c.rutas,
                "resumen": c.resumen(sesion, caja),
                "contado": contado,
                "nota": nota,
                "error": str(error),
            }
            return html(request, "dialogos/cerrar_caja.html", contexto)
        if c.al_cerrar is not None:  # después de contestar (el reporte del día, por correo)
            c.al_cerrar(tareas, abrir, dia_de_la_caja)
        # La página del cierre se abre e imprime el resumen sola.
        return Response(
            status_code=200, headers={"HX-Redirect": f"{c.rutas.caja}/{caja_id}?imprimir=1"}
        )

    @router.get("/caja/{caja_id}", response_class=HTMLResponse)
    def pagina_de_una_caja(
        request: Request, sesion: SesionBD, _: Admin, caja_id: int, imprimir: int = 0
    ) -> Response:
        caja = caja_o_404(sesion, caja_id)
        contexto = contexto_de_una_caja(c, sesion, caja) | {
            "imprimir_al_abrir": f"{c.rutas.caja}/{caja.id}/ticket" if imprimir else None,
        }
        return pagina(request, sesion, "una_caja.html", contexto, seccion=c.seccion)

    @router.get("/caja/{caja_id}/ticket", response_class=HTMLResponse)
    def ticket_de_caja(request: Request, sesion: SesionBD, _: Admin, caja_id: int) -> Response:
        caja = caja_o_404(sesion, caja_id)
        contexto = {"resumen": c.resumen(sesion, caja), "medios": MEDIOS}
        return ticket(request, "tickets/caja.html", contexto, c.ajustes_del_ticket(sesion))

    # Corregir la caja misma: el conteo (ya cerrada) o la base.

    def dialogo_de_correccion(
        request: Request,
        sesion: Session,
        caja: Caja,
        *,
        formulario: dict[str, Any] | None = None,
        accion: str = "",
        error: str | None = None,
    ) -> Response:
        contexto = {
            "rutas": c.rutas,
            "resumen": c.resumen(sesion, caja),
            "formulario": formulario
            or {"efectivo_contado": caja.efectivo_contado, "base": caja.base, "motivo": ""},
            "accion": accion,
            "error": error,
        }
        return html(request, "dialogos/corregir_caja.html", contexto)

    @router.get("/caja/{caja_id}/corregir", response_class=HTMLResponse)
    def dialogo_corregir_caja(
        request: Request, sesion: SesionBD, _: Admin, caja_id: int
    ) -> Response:
        caja = caja_o_404(sesion, caja_id)
        if not reglas.caja_corregible(caja.cerrada_en, tiempo.ahora()):
            return sin_cambios(
                error=f"La caja {caja.id} se cerró hace más de "
                f"{reglas.DIAS_PARA_CORREGIR_UNA_CAJA_CERRADA} días: ya no se corrige."
            )
        return dialogo_de_correccion(request, sesion, caja)

    def no_se_corrigio(
        request: Request,
        sesion: Session,
        caja_id: int,
        error: ReglaDelNegocio,
        formulario: dict[str, Any],
        accion: str,
    ) -> Response:
        """Si la caja ya no se puede corregir así, el diálogo se cierra y se avisa; si no,
        vuelve con el error y lo que se escribió."""
        if isinstance(error, CajaNoCorregible):
            return caja_de_la_pantalla(c, request, sesion, cerrar_dialogo=True, error=str(error))
        caja = caja_o_404(sesion, caja_id)
        completo = {"efectivo_contado": caja.efectivo_contado, "base": caja.base} | formulario
        return dialogo_de_correccion(
            request, sesion, caja, formulario=completo, accion=accion, error=str(error)
        )

    @router.post("/caja/{caja_id}/corregir-conteo", response_class=HTMLResponse)
    def corregir_conteo(
        request: Request,
        sesion: SesionBD,
        administrador: Admin,
        caja_id: int,
        efectivo_contado: Annotated[str, Form()] = "",
        motivo: Annotated[str, Form()] = "",
    ) -> Response:
        contado = leer_pesos(efectivo_contado)
        try:
            if contado is None:
                raise ValorInvalido("Escribe cuánto efectivo se contó.")
            correccion = c.corregir_conteo(
                sesion,
                caja_id,
                efectivo_contado=contado,
                motivo=motivo,
                administrador_id=administrador.id,
            )
            sesion.commit()
        except ReglaDelNegocio as error:
            sesion.rollback()
            formulario = {"efectivo_contado": contado, "motivo": motivo}
            return no_se_corrigio(request, sesion, caja_id, error, formulario, "conteo")
        aviso = (
            f"Listo: el conteo de la caja {caja_id} quedó en "
            f"{formato.pesos(correccion.valor_despues)}."
        )
        return caja_de_la_pantalla(c, request, sesion, cerrar_dialogo=True, aviso=aviso)

    @router.post("/caja/{caja_id}/corregir-base", response_class=HTMLResponse)
    def corregir_base(
        request: Request,
        sesion: SesionBD,
        administrador: Admin,
        caja_id: int,
        base: Annotated[str, Form()] = "",
        motivo: Annotated[str, Form()] = "",
    ) -> Response:
        valor = leer_pesos(base)
        try:
            if valor is None:
                raise ValorInvalido("Escribe la base con que se abrió la caja (0 si no hubo).")
            correccion = c.corregir_base(
                sesion, caja_id, base=valor, motivo=motivo, administrador_id=administrador.id
            )
            sesion.commit()
        except ReglaDelNegocio as error:
            sesion.rollback()
            formulario = {"base": valor, "motivo": motivo}
            return no_se_corrigio(request, sesion, caja_id, error, formulario, "base")
        aviso = (
            f"Listo: la base de la caja {caja_id} quedó en "
            f"{formato.pesos(correccion.valor_despues)}."
        )
        return caja_de_la_pantalla(c, request, sesion, cerrar_dialogo=True, aviso=aviso)

    return router
