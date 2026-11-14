"""El resultado del mes de cada negocio y de los dos juntos, y su cierre para el contador.

- Ingresos: lo cobrado en el mes, por medio de pago (el Balance de cada negocio).
- Gastos: los que salieron del cajón (salidas de tipo gasto) y los que se pagaron por fuera
  de la caja, sumados por categoría.
- Resultado: ingresos − gastos.
- Los retiros y las consignaciones se muestran aparte: son plata que se movió, no un gasto.

Cerrar el mes lo entrega al contador: sus cifras quedan guardadas tal como se entregaron, y
desde ese momento sus cajas, salidas y gastos ya no se cambian.
"""

from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import formato, tiempo
from app.caja import casos_de_uso as cajas
from app.caja.errores import ValorInvalido
from app.caja.estados import NOMBRE_DE_LA_CATEGORIA, NOMBRE_DEL_NEGOCIO, Negocio
from app.caja.periodos import limites, periodo
from app.caja.reglas import NOMBRE_DEL_MEDIO
from app.caja.tablas import Caja, CierreDeMes, GastoFueraDeCaja
from app.parqueadero import balance as balance_parqueadero
from app.turnos import balance as balance_lavadero
from app.turnos.estados import MedioPago

NEGOCIOS = (Negocio.LAVADERO, Negocio.PARQUEADERO)


@dataclass(frozen=True)
class ResultadoDelMes:
    negocio: Negocio
    mes: date  # el primer día
    por_medio: dict[MedioPago, int]
    gastos: list[tuple[str, int]]  # (categoría, total), de mayor a menor
    gastos_de_caja: int
    gastos_fuera_de_caja: int
    retiros: int
    consignaciones: int
    cajas_abiertas: int  # cajas del mes que siguen abiertas: el mes no se puede cerrar
    cierre: CierreDeMes | None

    @property
    def nombre(self) -> str:
        return NOMBRE_DEL_NEGOCIO[self.negocio]

    @property
    def ingresos(self) -> int:
        return sum(self.por_medio.values())

    @property
    def total_gastos(self) -> int:
        return self.gastos_de_caja + self.gastos_fuera_de_caja

    @property
    def resultado(self) -> int:
        return self.ingresos - self.total_gastos

    def cifras(self) -> dict:
        """Lo que se guarda al cerrar el mes, para que siempre diga lo que se entregó."""
        return {
            "ingresos": self.ingresos,
            "por_medio": {NOMBRE_DEL_MEDIO[m]: v for m, v in self.por_medio.items() if v},
            "gastos": [[nombre, total] for nombre, total in self.gastos],
            "gastos_de_caja": self.gastos_de_caja,
            "gastos_fuera_de_caja": self.gastos_fuera_de_caja,
            "retiros": self.retiros,
            "consignaciones": self.consignaciones,
            "resultado": self.resultado,
        }


def primer_dia(dia: date) -> date:
    return dia.replace(day=1)


def resultado_del_mes(sesion: Session, negocio: Negocio, mes: date) -> ResultadoDelMes:
    mes = primer_dia(mes)
    p = periodo("mes", mes)
    inicio, fin = limites(p)
    if negocio is Negocio.LAVADERO:
        b = balance_lavadero.balance(sesion, p)
    else:
        b = balance_parqueadero.balance(sesion, p)
    salidas = b.salidas_de_caja
    por_categoria: dict[str, int] = {nombre: total for nombre, total, _ in salidas.gastos}
    fuera = sesion.execute(
        select(GastoFueraDeCaja.categoria, func.sum(GastoFueraDeCaja.valor))
        .where(
            GastoFueraDeCaja.negocio == negocio,
            GastoFueraDeCaja.anulado_en.is_(None),
            GastoFueraDeCaja.fecha >= p.desde,
            GastoFueraDeCaja.fecha <= p.hasta,
        )
        .group_by(GastoFueraDeCaja.categoria)
    ).all()
    for categoria, total in fuera:
        nombre = NOMBRE_DE_LA_CATEGORIA[categoria]
        por_categoria[nombre] = por_categoria.get(nombre, 0) + int(total)
    abiertas = sesion.scalar(
        select(func.count())
        .select_from(Caja)
        .where(
            Caja.negocio == negocio,
            Caja.abierta,
            Caja.abierta_en >= inicio,
            Caja.abierta_en < fin,
        )
    )
    return ResultadoDelMes(
        negocio=negocio,
        mes=mes,
        por_medio={m: v for m, v in b.por_medio.items() if v},
        gastos=sorted(por_categoria.items(), key=lambda g: -g[1]),
        gastos_de_caja=salidas.total_gastos,
        gastos_fuera_de_caja=sum(int(total) for _, total in fuera),
        retiros=salidas.retiros,
        consignaciones=salidas.consignaciones,
        cajas_abiertas=abiertas or 0,
        cierre=cajas.mes_cerrado(sesion, negocio, mes),
    )


def se_puede_cerrar(resultado: ResultadoDelMes, hoy: date) -> str | None:
    """None si el mes se puede cerrar; si no, por qué."""
    if resultado.cierre is not None:
        return "Este mes ya se cerró."
    if resultado.mes >= primer_dia(hoy):
        return "El mes todavía no ha terminado: se cierra desde el primer día del mes siguiente."
    if resultado.cajas_abiertas:
        return (
            f"Hay {resultado.cajas_abiertas} caja(s) de ese mes todavía abierta(s): ciérralas "
            "primero."
        )
    return None


def cerrar_mes(
    sesion: Session,
    negocio: Negocio,
    mes: date,
    *,
    administrador_id: int | None = None,
    ahora: datetime | None = None,
) -> CierreDeMes:
    """Entrega el mes del negocio al contador: guarda sus cifras y desde ahora no se cambia."""
    ahora = ahora or tiempo.ahora()
    cajas.candado(sesion, f"{negocio}:cierre-de-mes")
    resultado = resultado_del_mes(sesion, negocio, mes)
    por_que_no = se_puede_cerrar(resultado, tiempo.hora_local(ahora).date())
    if por_que_no:
        raise ValorInvalido(por_que_no)
    cierre = CierreDeMes(
        negocio=negocio,
        mes=resultado.mes,
        cerrado_en=ahora,
        cerrado_por=administrador_id,
        cifras=resultado.cifras(),
    )
    sesion.add(cierre)
    sesion.flush()
    return cierre


def lineas_para_el_contador(resultados: list[ResultadoDelMes]) -> list[tuple[str, ...]]:
    """El resultado del mes de cada negocio, en filas para Excel."""
    lineas: list[tuple[str, ...]] = [("Negocio", "Mes", "Concepto", "Detalle", "Valor")]
    for r in resultados:
        mes = formato.mes_y_año(r.mes)
        for medio, valor in r.por_medio.items():
            lineas.append((r.nombre, mes, "Ingresos", NOMBRE_DEL_MEDIO[medio], str(valor)))
        lineas.append((r.nombre, mes, "Total ingresos", "", str(r.ingresos)))
        for nombre, total in r.gastos:
            lineas.append((r.nombre, mes, "Gastos", nombre, str(total)))
        lineas.append((r.nombre, mes, "Total gastos", "", str(r.total_gastos)))
        lineas.append((r.nombre, mes, "Resultado", "", str(r.resultado)))
        lineas.append((r.nombre, mes, "Retiros (no son gasto)", "", str(r.retiros)))
        lineas.append((r.nombre, mes, "Consignaciones (no son gasto)", "", str(r.consignaciones)))
        cerrado = (
            f"Cerrado el {formato.fecha(tiempo.hora_local(r.cierre.cerrado_en).date())}"
            if r.cierre
            else "Abierto"
        )
        lineas.append((r.nombre, mes, "Estado del mes", cerrado, ""))
    return lineas
