"""Los dos negocios juntos: el resultado del mes de cada uno y el total, los gastos pagados por
fuera de la caja y el cierre del mes para el contador (app.resultados)."""

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import formato, resultados, tiempo
from app.caja import casos_de_uso as cajas
from app.caja.consultas import nombres
from app.caja.estados import NOMBRE_DE_LA_CATEGORIA, NOMBRE_DEL_NEGOCIO, Negocio
from app.caja.reglas import NOMBRE_DEL_MEDIO
from app.caja.tablas import GastoFueraDeCaja
from app.errores import ReglaDelNegocio
from app.web.caja import CATEGORIAS, archivo_para_excel, fecha, hoy
from app.web.comun import Admin, SesionBD, con_eventos, html, leer_pesos, pagina, verificar_origen

router = APIRouter(dependencies=[Depends(verificar_origen)])


@dataclass(frozen=True)
class FilaGasto:
    gasto: GastoFueraDeCaja
    registro: str
    anulo: str | None

    @property
    def negocio(self) -> str:
        return NOMBRE_DEL_NEGOCIO[self.gasto.negocio]

    @property
    def categoria(self) -> str:
        return NOMBRE_DE_LA_CATEGORIA[self.gasto.categoria]


def _mes(texto: str | None) -> date:
    """ "2026-10" → el 1 de octubre de 2026; sin mes (o uno raro), el de hoy. Nunca uno que
    no ha llegado."""
    este = hoy().replace(day=1)
    elegido = fecha(f"{texto}-01") if texto else None
    return min(elegido or este, este)


def _otro_mes(mes: date, cuantos: int) -> date:
    numero = mes.year * 12 + mes.month - 1 + cuantos
    return date(numero // 12, numero % 12 + 1, 1)


def _gastos_del_mes(sesion: Session, mes: date) -> list[FilaGasto]:
    gastos = list(
        sesion.scalars(
            select(GastoFueraDeCaja)
            .where(
                GastoFueraDeCaja.fecha >= mes,
                GastoFueraDeCaja.fecha < _otro_mes(mes, 1),
            )
            .order_by(GastoFueraDeCaja.fecha, GastoFueraDeCaja.id)
        )
    )
    quienes = nombres(sesion, {g.registrado_por for g in gastos} | {g.anulado_por for g in gastos})
    return [
        FilaGasto(
            g,
            quienes.get(g.registrado_por, "—"),
            quienes.get(g.anulado_por, "—") if g.anulado else None,
        )
        for g in gastos
    ]


def _contexto(sesion: Session, mes: date) -> dict[str, Any]:
    los_dos = [resultados.resultado_del_mes(sesion, n, mes) for n in resultados.NEGOCIOS]
    de_hoy = hoy()
    return {
        "mes": mes,
        "nombre_del_mes": formato.mes_y_año(mes),
        "resultados": [(r, resultados.se_puede_cerrar(r, de_hoy)) for r in los_dos],
        "total": {
            "ingresos": sum(r.ingresos for r in los_dos),
            "gastos": sum(r.total_gastos for r in los_dos),
            "resultado": sum(r.resultado for r in los_dos),
        },
        "gastos": _gastos_del_mes(sesion, mes),
        "nombre_del_medio": NOMBRE_DEL_MEDIO,
        "anterior": _otro_mes(mes, -1).strftime("%Y-%m"),
        "siguiente": _otro_mes(mes, 1).strftime("%Y-%m") if mes < de_hoy.replace(day=1) else None,
        "clave_del_mes": mes.strftime("%Y-%m"),
        "nombres_de_negocio": NOMBRE_DEL_NEGOCIO,
        "cuando_cerro": lambda cierre: formato.fecha(tiempo.hora_local(cierre.cerrado_en).date()),
        "quien_cerro": lambda cierre: nombres(sesion, {cierre.cerrado_por}).get(
            cierre.cerrado_por, "—"
        ),
    }


def _pantalla(request: Request, sesion: Session, mes: date, **eventos: Any) -> Response:
    respuesta = html(request, "_resultados.html", _contexto(sesion, mes))
    respuesta.headers["HX-Retarget"] = "#resultados"
    respuesta.headers["HX-Reswap"] = "outerHTML"
    return con_eventos(respuesta, cerrar_dialogo=True, **eventos)


@router.get("/resultados", response_class=HTMLResponse)
def pagina_resultados(
    request: Request, sesion: SesionBD, _: Admin, mes: str | None = None
) -> Response:
    return pagina(
        request, sesion, "resultados.html", _contexto(sesion, _mes(mes)), seccion="resultados"
    )


@router.get("/resultados/exportar")
def exportar(sesion: SesionBD, _: Admin, mes: str | None = None) -> Response:
    elegido = _mes(mes)
    los_dos = [resultados.resultado_del_mes(sesion, n, elegido) for n in resultados.NEGOCIOS]
    return archivo_para_excel(
        resultados.lineas_para_el_contador(los_dos), f"resultado-{elegido.strftime('%Y-%m')}.csv"
    )


# --- Cerrar el mes --------------------------------------------------------------------------


def _negocio(texto: str) -> Negocio:
    try:
        return Negocio(texto)
    except ValueError:
        raise HTTPException(status_code=404, detail="Ese negocio no existe.") from None


@router.get("/resultados/cerrar", response_class=HTMLResponse)
def dialogo_cerrar(
    request: Request, sesion: SesionBD, _: Admin, negocio: str, mes: str
) -> Response:
    elegido = _mes(mes)
    r = resultados.resultado_del_mes(sesion, _negocio(negocio), elegido)
    contexto = {
        "r": r,
        "nombre_del_mes": formato.mes_y_año(elegido),
        "clave_del_mes": elegido.strftime("%Y-%m"),
        "por_que_no": resultados.se_puede_cerrar(r, hoy()),
    }
    return html(request, "dialogos/cerrar_mes.html", contexto)


@router.post("/resultados/cerrar", response_class=HTMLResponse)
def cerrar(
    request: Request,
    sesion: SesionBD,
    administrador: Admin,
    negocio: Annotated[str, Form()],
    mes: Annotated[str, Form()],
) -> Response:
    elegido = _mes(mes)
    cual = _negocio(negocio)
    try:
        resultados.cerrar_mes(sesion, cual, elegido, administrador_id=administrador.id)
        sesion.commit()
    except ReglaDelNegocio as error:
        sesion.rollback()
        return _pantalla(request, sesion, elegido, error=str(error))
    return _pantalla(
        request,
        sesion,
        elegido,
        aviso=(
            f"{NOMBRE_DEL_NEGOCIO[cual]}: {formato.mes_y_año(elegido)} quedó cerrado para el "
            "contador."
        ),
    )


# --- Gastos por fuera de la caja ----------------------------------------------------------------


def _dialogo_gasto(
    request: Request, mes: date, formulario: dict | None = None, error: str | None = None
) -> Response:
    contexto = {
        "negocios": [(n.value, NOMBRE_DEL_NEGOCIO[n]) for n in resultados.NEGOCIOS],
        "categorias": CATEGORIAS,
        # Por defecto, hoy si es el mes que se mira; si no, el último día de ese mes.
        "formulario": formulario
        or {"fecha": min(hoy(), _otro_mes(mes, 1) - timedelta(days=1)).isoformat()},
        "clave_del_mes": mes.strftime("%Y-%m"),
        "hoy": hoy().isoformat(),
        "error": error,
    }
    return html(request, "dialogos/gasto_fuera_de_caja.html", contexto)


@router.get("/resultados/gastos/nuevo", response_class=HTMLResponse)
def nuevo_gasto(request: Request, _: Admin, mes: str | None = None) -> Response:
    return _dialogo_gasto(request, _mes(mes))


@router.post("/resultados/gastos", response_class=HTMLResponse)
def registrar_gasto(
    request: Request,
    sesion: SesionBD,
    administrador: Admin,
    negocio: Annotated[str, Form()] = "",
    fecha_del_gasto: Annotated[str, Form(alias="fecha")] = "",
    categoria: Annotated[str, Form()] = "",
    descripcion: Annotated[str, Form()] = "",
    valor: Annotated[str, Form()] = "",
    mes: Annotated[str, Form()] = "",
) -> Response:
    formulario = {
        "negocio": negocio,
        "fecha": fecha_del_gasto,
        "categoria": categoria,
        "descripcion": descripcion,
        "valor": valor,
    }
    elegido = _mes(mes)
    try:
        cual = Negocio(negocio)
    except ValueError:
        return _dialogo_gasto(request, elegido, formulario, "Elige de qué negocio es el gasto.")
    try:
        gasto = cajas.registrar_gasto_fuera_de_caja(
            sesion,
            cual,
            fecha=fecha(fecha_del_gasto),
            categoria=categoria or None,
            descripcion=descripcion,
            valor=leer_pesos(valor),
            administrador_id=administrador.id,
        )
        sesion.commit()
    except ReglaDelNegocio as error:
        sesion.rollback()
        return _dialogo_gasto(request, elegido, formulario, str(error))
    return _pantalla(
        request,
        sesion,
        gasto.fecha.replace(day=1),
        aviso=(
            f"Gasto anotado: {formato.pesos(gasto.valor)} en "
            f"{NOMBRE_DE_LA_CATEGORIA[gasto.categoria].lower()}."
        ),
    )


@router.get("/resultados/gastos/{gasto_id}/anular", response_class=HTMLResponse)
def dialogo_anular_gasto(request: Request, sesion: SesionBD, _: Admin, gasto_id: int) -> Response:
    gasto = sesion.get(GastoFueraDeCaja, gasto_id)
    if gasto is None:
        raise HTTPException(status_code=404, detail="Ese gasto no existe.")
    fila = FilaGasto(
        gasto, nombres(sesion, {gasto.registrado_por}).get(gasto.registrado_por, "—"), None
    )
    return html(request, "dialogos/anular_gasto.html", {"fila": fila})


@router.post("/resultados/gastos/{gasto_id}/anular", response_class=HTMLResponse)
def anular_gasto(
    request: Request,
    sesion: SesionBD,
    administrador: Admin,
    gasto_id: int,
    motivo: Annotated[str, Form()] = "",
) -> Response:
    gasto = sesion.get(GastoFueraDeCaja, gasto_id)
    if gasto is None:
        raise HTTPException(status_code=404, detail="Ese gasto no existe.")
    try:
        cajas.anular_gasto_fuera_de_caja(
            sesion, gasto_id, motivo=motivo, administrador_id=administrador.id
        )
        sesion.commit()
    except ReglaDelNegocio as error:
        sesion.rollback()
        fila = FilaGasto(
            gasto, nombres(sesion, {gasto.registrado_por}).get(gasto.registrado_por, "—"), None
        )
        return html(
            request,
            "dialogos/anular_gasto.html",
            {"fila": fila, "error": str(error), "motivo": motivo},
        )
    return _pantalla(
        request,
        sesion,
        gasto.fecha.replace(day=1),
        aviso=f"Gasto anulado: {formato.pesos(gasto.valor)}.",
    )
