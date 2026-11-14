"""Lo que el reporte del día de cualquier negocio dice de su caja: cómo cerró cada una (o lo
que debería tener, si sigue abierta), sus correcciones, y lo que se corrigió ese día en cajas
de días anteriores. Lo usan el reporte del parqueadero y el del lavadero.
"""

from collections.abc import Sequence
from datetime import date
from typing import Protocol

from app import formato, tiempo
from app.caja.consultas import Correccion, SalidasDelPeriodo
from app.caja.tablas import Caja

# En el texto (WhatsApp y correo) van hasta tantas líneas de detalle por lista; el resto se
# cuenta ("y 4 más") y se ve completo en el Balance. Un mensaje eterno no se lee.
DETALLE_EN_EL_TEXTO = 10


class _ConEfectivoEsperado(Protocol):
    @property
    def efectivo_esperado(self) -> int: ...

    @property
    def salidas_de_caja(self) -> int: ...


class ResumenDeUnaCaja(Protocol):
    """Lo que el reporte necesita del resumen de una caja (el de cada negocio lo tiene)."""

    @property
    def caja(self) -> Caja: ...

    @property
    def cuadre(self) -> _ConEfectivoEsperado: ...

    @property
    def cerro(self) -> str | None: ...

    @property
    def correcciones(self) -> list[Correccion]: ...

    @property
    def corregida_despues_del_cierre(self) -> bool: ...

    @property
    def diferencia_al_cerrar(self) -> int | None: ...


def y_mas(cuantos: int) -> list[str]:
    """La línea que cuenta lo que no cupo en el texto."""
    return [f"- y {cuantos} más (ver el Balance)"] if cuantos > 0 else []


def diferencia(valor: int) -> str:
    if valor == 0:
        return "Cuadró exacto."
    if valor > 0:
        return f"Sobraron {formato.pesos(valor)}."
    return f"Faltaron {formato.pesos(-valor)}."


def lineas_de_la_caja(resumen: ResumenDeUnaCaja, dia: date) -> list[str]:
    """Cómo cerró la caja (o lo que debería tener, si sigue abierta) y sus correcciones."""
    pesos = formato.pesos
    caja = resumen.caja
    salidas = resumen.cuadre.salidas_de_caja
    if caja.cerrada_en is None:
        lineas = [
            f"Sigue abierta: debería haber {pesos(resumen.cuadre.efectivo_esperado)} en "
            + (
                f"efectivo (con la base, y sin las salidas de {pesos(salidas)})."
                if salidas
                else "efectivo (con la base)."
            )
        ]
    else:
        cerro = tiempo.hora_local(caja.cerrada_en)
        cuando = f"a {formato.hora_con_articulo(cerro)}"
        if cerro.date() != dia:  # se cerró pasada la medianoche
            cuando = f"el {formato.dia_y_mes(cerro.date())} {cuando}"
        quien = f" {resumen.cerro}" if resumen.cerro not in (None, "—") else ""
        lineas = [
            f"Cerró{quien} {cuando}",
            f"Base {pesos(caja.base)}"
            + (f" · Salidas {pesos(salidas)}" if salidas else "")
            + f" · Debía haber {pesos(caja.efectivo_esperado or 0)}"
            + f" · Contaron {pesos(caja.efectivo_contado or 0)}",
            diferencia(caja.diferencia or 0),
        ]
        if resumen.corregida_despues_del_cierre:
            al_cerrar = diferencia(resumen.diferencia_al_cerrar or 0)
            lineas.append(
                f"Se corrigió después del cierre. Al cerrar: {al_cerrar[0].lower()}{al_cerrar[1:]}"
            )
        if caja.nota_cierre:
            lineas.append(f"Nota: {caja.nota_cierre}")
    if resumen.correcciones:
        # Lo que se corrigió después de cobrar: es lo primero que un jefe debe revisar.
        lineas.append(f"Correcciones: {len(resumen.correcciones)}")
        lineas += [f"- {fila.en_una_linea}" for fila in resumen.correcciones[:DETALLE_EN_EL_TEXTO]]
        lineas += y_mas(len(resumen.correcciones) - DETALLE_EN_EL_TEXTO)
    return lineas


def lineas_de_correcciones_de_antes(correcciones: Sequence[Correccion]) -> list[str]:
    """Lo que se corrigió este día en cajas de días anteriores, con el día de cada caja."""
    lineas = [
        f"- Caja del {formato.dia_y_mes(fila.dia_de_la_caja)}: {fila.en_una_linea}"
        if fila.dia_de_la_caja
        else f"- {fila.en_una_linea}"
        for fila in correcciones[:DETALLE_EN_EL_TEXTO]
    ]
    return lineas + y_mas(len(correcciones) - DETALLE_EN_EL_TEXTO)


def lineas_de_las_salidas(salidas: SalidasDelPeriodo) -> list[str]:
    """Lo que salió de las cajas: los gastos por categoría, los retiros y las consignaciones."""
    pesos = formato.pesos
    lineas = []
    if salidas.gastos:
        detalle = ", ".join(f"{nombre} {pesos(total)}" for nombre, total, _ in salidas.gastos)
        lineas.append(f"Gastos: {pesos(salidas.total_gastos)} ({detalle})")
    if salidas.retiros:
        lineas.append(f"Retiros: {pesos(salidas.retiros)}")
    if salidas.consignaciones:
        lineas.append(f"Consignaciones: {pesos(salidas.consignaciones)}")
    return lineas
