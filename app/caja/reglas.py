"""Las reglas de la caja, sin base de datos: cómo se nombra cada medio de pago, cómo se suman
los cobros y hasta cuándo se puede corregir una caja cerrada."""

from collections.abc import Iterable
from datetime import datetime, timedelta

from app import formato
from app.caja.errores import ValorInvalido
from app.errores import ReglaDelNegocio
from app.turnos.estados import MedioPago

# Una caja cerrada todavía se puede corregir (un cobro, la base o el conteo) hasta estos días
# después del cierre. Después, sus cuentas ya se entregaron y no se cambian.
DIAS_PARA_CORREGIR_UNA_CAJA_CERRADA = 30

# Topes contra un cero de más, en los dos negocios. Un cobro (un lavado, una salida, una
# mensualidad, una tarifa) no pasa de VALOR_MAXIMO_DE_UN_COBRO, el mismo tope de un precio del
# menú del lavadero (app.catalogo.configuracion.PRECIO_MAXIMO); lo que hay en una caja (la base,
# lo contado al cerrar), de VALOR_MAXIMO_EN_LA_CAJA. Más que eso es un error al escribir que,
# además, no cabría en la base de datos: se avisa en vez de guardarlo.
VALOR_MAXIMO_DE_UN_COBRO = 5_000_000
VALOR_MAXIMO_EN_LA_CAJA = 100_000_000


def sin_ceros_de_mas(valor: int, tope: int, error: type[ReglaDelNegocio] = ValorInvalido) -> int:
    """El valor, si no pasa del tope. Si pasa, el aviso (con el error de quien pregunta, para
    que salga donde sale el resto de sus avisos)."""
    if valor > tope:
        raise error(
            f"Ese valor es demasiado alto: revisa los ceros (el máximo es {formato.pesos(tope)})."
        )
    return valor


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
