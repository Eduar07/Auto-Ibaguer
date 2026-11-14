"""Las reglas de la caja, sin base de datos: cómo se nombra cada medio de pago, cómo se suman
los cobros y hasta cuándo se puede corregir una caja cerrada."""

from collections.abc import Iterable
from datetime import datetime, timedelta

from app.turnos.estados import MedioPago

# Una caja cerrada todavía se puede corregir (un cobro, la base o el conteo) hasta estos días
# después del cierre. Después, sus cuentas ya se entregaron y no se cambian.
DIAS_PARA_CORREGIR_UNA_CAJA_CERRADA = 30

NOMBRE_DEL_MEDIO = {
    MedioPago.EFECTIVO: "Efectivo",
    MedioPago.NEQUI: "Nequi",
    MedioPago.DAVIPLATA: "Daviplata",
    MedioPago.TARJETA: "Tarjeta",
}


def como_se_pago(medio: MedioPago | None) -> str:
    """Para una frase: "en efectivo", "por Nequi", "por Daviplata", "con tarjeta" o "sin pago"."""
    if medio is None:
        return "sin pago"
    if medio is MedioPago.EFECTIVO:
        return "en efectivo"
    if medio is MedioPago.TARJETA:
        return "con tarjeta"
    return f"por {NOMBRE_DEL_MEDIO[medio]}"


def sumar_por_medio(cobros: Iterable[tuple[MedioPago, int]]) -> dict[MedioPago, int]:
    totales: dict[MedioPago, int] = {}
    for medio, valor in cobros:
        totales[medio] = totales.get(medio, 0) + valor
    return totales


def caja_corregible(cerrada_en: datetime | None, ahora: datetime) -> bool:
    """¿Esa caja todavía se puede corregir? La abierta, siempre; la cerrada, hasta 30 días
    después del cierre."""
    return cerrada_en is None or ahora - cerrada_en <= timedelta(
        days=DIAS_PARA_CORREGIR_UNA_CAJA_CERRADA
    )
