"""Lo que se hace con la caja de cualquier negocio: abrirla, cerrarla contando el efectivo,
cobrar en ella y corregirla (su base, su conteo o la caja de un cobro equivocado).

Cada negocio llama a estas funciones con su `negocio` y dice qué entró a su caja (ver
`cerrar_caja`). Como en el resto del sistema, cada función trabaja dentro de la transacción
de quien la llama, y antes de cambiar algo que dos personas pueden tocar al mismo tiempo se
pide un candado en PostgreSQL: el segundo espera a que termine el primero y ve lo que hizo.
"""

from collections.abc import Callable, Sequence
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import formato, tiempo
from app.caja import reglas
from app.caja.errores import (
    CajaNoCorregible,
    CajaYaAbierta,
    CobroNoCorregible,
    SinCajaAbierta,
    ValorInvalido,
)
from app.caja.estados import CategoriaDeGasto, DatoDeCaja, Negocio, TipoDeSalida
from app.caja.tablas import Caja, CorreccionCobro, CorreccionDeCaja, SalidaDeCaja
from app.errores import ReglaDelNegocio

# Lo que debía haber en el cajón al cerrar (la base más lo que entró en efectivo). Lo dice
# cada negocio, porque cada uno sabe qué cobros entran a su caja.
EfectivoEsperado = Callable[[Session, Caja], int]


def candado(sesion: Session, nombre: str) -> None:
    """Un candado de PostgreSQL con nombre, que se suelta solo al terminar la transacción."""
    sesion.execute(select(func.pg_advisory_xact_lock(func.hashtext(nombre))))


def _hora(momento: datetime) -> str:
    return formato.hora(tiempo.hora_local(momento))


# --- Abrir y cerrar ------------------------------------------------------------------------


def caja_abierta(sesion: Session, negocio: Negocio) -> Caja | None:
    return sesion.scalars(select(Caja).where(Caja.negocio == negocio, Caja.abierta)).one_or_none()


def abrir_caja(
    sesion: Session,
    negocio: Negocio,
    *,
    base: int,
    nota: str | None = None,
    administrador_id: int | None = None,
    ahora: datetime | None = None,
) -> Caja:
    """Abre la caja del negocio con la plata que queda en el cajón para dar vueltos (la base)."""
    ahora = ahora or tiempo.ahora()
    if base < 0:
        raise ValorInvalido("La base no puede ser negativa.")
    reglas.sin_ceros_de_mas(base, reglas.VALOR_MAXIMO_EN_LA_CAJA)
    candado(sesion, f"{negocio}:caja")
    abierta = caja_abierta(sesion, negocio)
    if abierta is not None:
        raise CajaYaAbierta(f"Ya hay una caja abierta desde las {_hora(abierta.abierta_en)}.")
    caja = Caja(
        negocio=negocio,
        base=base,
        abierta_en=ahora,
        abierta_por=administrador_id,
        nota_apertura=(nota or "").strip() or None,
    )
    sesion.add(caja)
    sesion.flush()
    return caja


def bloquear_caja(sesion: Session, negocio: Negocio, caja_id: int) -> Caja | None:
    """La caja de ese negocio, con candado exclusivo hasta el final de la transacción. None si
    no existe o es del otro negocio: la caja de uno nunca se toca desde las pantallas del otro."""
    return sesion.execute(
        select(Caja)
        .where(Caja.id == caja_id, Caja.negocio == negocio)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()


def cerrar_caja(
    sesion: Session,
    negocio: Negocio,
    caja_id: int,
    *,
    efectivo_esperado: EfectivoEsperado,
    efectivo_contado: int,
    nota: str | None = None,
    administrador_id: int | None = None,
    ahora: datetime | None = None,
) -> Caja:
    """Cierra la caja con lo que se contó en el cajón. Guarda lo que esperaba el sistema
    (base + efectivo, según `efectivo_esperado`) para que la diferencia quede escrita."""
    ahora = ahora or tiempo.ahora()
    if efectivo_contado < 0:
        raise ValorInvalido("El efectivo contado no puede ser negativo.")
    reglas.sin_ceros_de_mas(efectivo_contado, reglas.VALOR_MAXIMO_EN_LA_CAJA)
    candado(sesion, f"{negocio}:caja")
    caja = bloquear_caja(sesion, negocio, caja_id)
    if caja is None or not caja.abierta:
        raise SinCajaAbierta("Esa caja ya está cerrada.")
    caja.efectivo_esperado = efectivo_esperado(sesion, caja)
    caja.efectivo_contado = efectivo_contado
    caja.nota_cierre = (nota or "").strip() or None
    caja.cerrada_en = ahora
    caja.cerrada_por = administrador_id
    caja.abierta = False
    sesion.flush()
    return caja


# --- Cobrar en la caja ---------------------------------------------------------------------


def caja_abierta_bloqueada(sesion: Session, negocio: Negocio) -> Caja | None:
    """La caja abierta del negocio, con un candado compartido hasta el final de la transacción.

    Mientras alguien cobra o corrige en ella, nadie la puede cerrar: el cierre espera y cuenta
    también ese cobro. Y si el cierre llegó primero, quien cobra espera y ve la caja cerrada
    (sin el candado, el cobro podía quedar en una caja ya contada, por fuera de su cuadre).
    """
    return sesion.execute(
        select(Caja)
        .where(Caja.negocio == negocio, Caja.abierta)
        .with_for_update(read=True)
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()


def caja_para_cobrar(
    sesion: Session, negocio: Negocio, mensaje: str = "Abre la caja para poder cobrar."
) -> Caja:
    caja = caja_abierta_bloqueada(sesion, negocio)
    if caja is None:
        raise SinCajaAbierta(mensaje)
    return caja


# --- Correcciones ------------------------------------------------------------------------
#
# Un cobro equivocado (otro medio de pago, otro valor, el cobro que no era) se corrige en su
# caja, abierta o cerrada hace hasta 30 días: como en contabilidad, no se borra nada, se hace
# un ajuste con su motivo. En la caja cerrada, lo que debía haber en el cajón se pone al día y
# la diferencia del cierre cambia con la corrección, que queda marcada como hecha después del
# cierre. Lo mismo con la base o el conteo de la caja misma.


def motivo_de_la_correccion(motivo: str | None) -> str:
    limpio = " ".join((motivo or "").split())
    if not limpio:
        raise ValorInvalido(
            "Escribe por qué se corrige: queda anotado en la caja y en el reporte a los jefes."
        )
    return limpio[:200]


def que_no_este_vencida(caja: Caja, ahora: datetime, error: type[ReglaDelNegocio]) -> None:
    if not reglas.caja_corregible(caja.cerrada_en, ahora):
        raise error(
            f"La caja {caja.id} se cerró hace más de {reglas.DIAS_PARA_CORREGIR_UNA_CAJA_CERRADA} "
            "días: sus cuentas ya se entregaron y no se corrigen. Si hay que devolver plata, "
            "sácala de la caja de hoy y escríbelo en la nota del cierre."
        )


def caja_del_cobro(sesion: Session, negocio: Negocio, caja_id: int | None, ahora: datetime) -> Caja:
    """La caja de un cobro que se va a corregir, bloqueada hasta terminar la corrección.

    Si es la abierta, con un candado compartido: nadie la cierra a mitad (el cierre espera y
    cuenta el cobro ya corregido). Si ya se cerró (hasta 30 días antes), con uno exclusivo: lo
    que debía haber en el cajón cambia con la corrección, y dos correcciones al mismo tiempo
    no se pisan esa cuenta.
    """
    if caja_id is None:
        raise CobroNoCorregible("Ese cobro no entró a ninguna caja: no hay plata que corregir.")
    abierta = caja_abierta_bloqueada(sesion, negocio)
    if abierta is not None and abierta.id == caja_id:
        return abierta
    caja = bloquear_caja(sesion, negocio, caja_id)
    if caja is None:  # la llave foránea no deja un cobro sin caja; por si acaso
        raise CobroNoCorregible("La caja de ese cobro no existe.")
    que_no_este_vencida(caja, ahora, CobroNoCorregible)
    return caja


def anotar_en_la_caja(correccion: CorreccionCobro | CorreccionDeCaja, caja: Caja) -> None:
    """Si la caja ya se había cerrado, la corrección queda marcada como hecha después del
    cierre y lo que debía haber en el cajón se pone al día: la diferencia cambia con ella.
    En la abierta no hay nada que poner al día: el cierre cuenta lo que haya."""
    correccion.despues_del_cierre = not caja.abierta
    if not caja.abierta:
        caja.efectivo_esperado = (caja.efectivo_esperado or 0) + correccion.cambio_en_efectivo


def _caja_para_corregir(sesion: Session, negocio: Negocio, caja_id: int, ahora: datetime) -> Caja:
    """La caja, con candado exclusivo hasta terminar: ni se cierra ni se cobra en ella a mitad
    de la corrección (el cobro que llega espera y después cuenta con la base nueva)."""
    caja = bloquear_caja(sesion, negocio, caja_id)
    if caja is None:
        raise CajaNoCorregible("Esa caja no existe.")
    que_no_este_vencida(caja, ahora, CajaNoCorregible)
    return caja


def corregir_base(
    sesion: Session,
    negocio: Negocio,
    caja_id: int,
    *,
    base: int,
    motivo: str,
    administrador_id: int | None = None,
    ahora: datetime | None = None,
) -> CorreccionDeCaja:
    """Cambia la base con que se abrió la caja (se escribió $50.000 y eran $5.000). Si ya se
    cerró, cambia también lo que debía haber en el cajón y, con eso, la diferencia."""
    ahora = ahora or tiempo.ahora()
    limpio = motivo_de_la_correccion(motivo)
    if base < 0:
        raise ValorInvalido("La base no puede ser negativa.")
    reglas.sin_ceros_de_mas(base, reglas.VALOR_MAXIMO_EN_LA_CAJA)
    caja = _caja_para_corregir(sesion, negocio, caja_id, ahora)
    if base == caja.base:
        raise ValorInvalido(f"No hay nada que cambiar: la base ya es {formato.pesos(base)}.")
    correccion = CorreccionDeCaja(
        dato=DatoDeCaja.BASE,
        caja_id=caja.id,
        valor_antes=caja.base,
        valor_despues=base,
        motivo=limpio,
        administrador_id=administrador_id,
        fecha_hora=ahora,
    )
    anotar_en_la_caja(correccion, caja)
    caja.base = base
    sesion.add(correccion)
    sesion.flush()
    return correccion


def corregir_conteo(
    sesion: Session,
    negocio: Negocio,
    caja_id: int,
    *,
    efectivo_contado: int,
    motivo: str,
    administrador_id: int | None = None,
    ahora: datetime | None = None,
) -> CorreccionDeCaja:
    """Cambia el efectivo que se contó al cerrar (se escribió mal, o se volvió a contar y era
    otro): la diferencia del cierre cambia con él."""
    ahora = ahora or tiempo.ahora()
    limpio = motivo_de_la_correccion(motivo)
    if efectivo_contado < 0:
        raise ValorInvalido("El efectivo contado no puede ser negativo.")
    reglas.sin_ceros_de_mas(efectivo_contado, reglas.VALOR_MAXIMO_EN_LA_CAJA)
    caja = _caja_para_corregir(sesion, negocio, caja_id, ahora)
    if caja.abierta:
        raise CajaNoCorregible("Esa caja sigue abierta: el efectivo se cuenta al cerrarla.")
    if efectivo_contado == caja.efectivo_contado:
        raise ValorInvalido(
            f"No hay nada que cambiar: el conteo ya es {formato.pesos(efectivo_contado)}."
        )
    correccion = CorreccionDeCaja(
        dato=DatoDeCaja.CONTEO,
        caja_id=caja.id,
        valor_antes=caja.efectivo_contado or 0,
        valor_despues=efectivo_contado,
        motivo=limpio,
        administrador_id=administrador_id,
        fecha_hora=ahora,
    )
    anotar_en_la_caja(correccion, caja)
    caja.efectivo_contado = efectivo_contado
    sesion.add(correccion)
    sesion.flush()
    return correccion


# --- La plata que sale del cajón -------------------------------------------------------------

LARGO_DE_LA_DESCRIPCION = 120


def _tipo_y_categoria(
    tipo: TipoDeSalida | str, categoria: CategoriaDeGasto | str | None
) -> tuple[TipoDeSalida, CategoriaDeGasto | None]:
    try:
        tipo = TipoDeSalida(tipo)
    except ValueError:
        raise ValorInvalido("Elige qué fue: un gasto, un retiro o una consignación.") from None
    if tipo is not TipoDeSalida.GASTO:
        return tipo, None
    try:
        return tipo, CategoriaDeGasto(categoria or "")
    except ValueError:
        raise ValorInvalido("Elige en qué se gastó: así el resultado del mes lo suma.") from None


def registrar_salida(
    sesion: Session,
    negocio: Negocio,
    *,
    tipo: TipoDeSalida | str,
    categoria: CategoriaDeGasto | str | None = None,
    descripcion: str,
    valor: int | None,
    efectivo_esperado: EfectivoEsperado,
    administrador_id: int | None = None,
    ahora: datetime | None = None,
) -> SalidaDeCaja:
    """Anota plata que sale del cajón de la caja abierta: un gasto (y en qué), un retiro o una
    consignación. No puede salir más de lo que debe haber en el cajón en ese momento.

    La caja se bloquea del todo mientras tanto: dos salidas al mismo tiempo no sacan entre las
    dos más de lo que hay, y nadie cierra la caja a mitad de la salida."""
    ahora = ahora or tiempo.ahora()
    tipo, categoria = _tipo_y_categoria(tipo, categoria)
    limpia = " ".join((descripcion or "").split())
    if not limpia:
        raise ValorInvalido("Escribe en qué se fue la plata: sale en el cierre y en el reporte.")
    if len(limpia) > LARGO_DE_LA_DESCRIPCION:
        raise ValorInvalido(
            f"La descripción es muy larga: máximo {LARGO_DE_LA_DESCRIPCION} letras."
        )
    if valor is None or valor <= 0:
        raise ValorInvalido("Escribe cuánta plata salió.")
    reglas.sin_ceros_de_mas(valor, reglas.VALOR_MAXIMO_EN_LA_CAJA)
    abierta = caja_abierta(sesion, negocio)
    caja = bloquear_caja(sesion, negocio, abierta.id) if abierta else None
    if caja is None or not caja.abierta:
        raise SinCajaAbierta("La caja está cerrada: ábrela para registrar una salida.")
    hay = efectivo_esperado(sesion, caja)
    if valor > hay:
        raise ValorInvalido(
            f"En el cajón debe haber {formato.pesos(hay)}: no pueden salir "
            f"{formato.pesos(valor)}. Revisa el valor."
        )
    salida = SalidaDeCaja(
        caja_id=caja.id,
        tipo=tipo,
        categoria=categoria,
        descripcion=limpia,
        valor=valor,
        registrada_en=ahora,
        registrada_por=administrador_id,
    )
    sesion.add(salida)
    sesion.flush()
    return salida


def anular_salida(
    sesion: Session,
    negocio: Negocio,
    salida_id: int,
    *,
    motivo: str,
    administrador_id: int | None = None,
    ahora: datetime | None = None,
) -> SalidaDeCaja:
    """Anula una salida que no fue (se registró dos veces, o no salió esa plata). No se borra:
    queda anulada, con motivo y quién, y como corrección de su caja. Si la caja ya se cerró
    (hasta 30 días antes), lo que debía haber en el cajón sube y la diferencia cambia."""
    ahora = ahora or tiempo.ahora()
    limpio = " ".join((motivo or "").split())
    if not limpio:
        raise ValorInvalido("Escribe por qué se anula: queda en la caja y en el reporte.")
    salida = sesion.execute(
        select(SalidaDeCaja)
        .join(Caja, Caja.id == SalidaDeCaja.caja_id)
        .where(SalidaDeCaja.id == salida_id, Caja.negocio == negocio)
    ).scalar_one_or_none()
    if salida is None:
        raise ValorInvalido("Esa salida no existe.")
    caja = bloquear_caja(sesion, negocio, salida.caja_id)
    sesion.refresh(salida)
    if salida.anulada:
        raise ValorInvalido("Esa salida ya estaba anulada.")
    if caja is None:  # la llave foránea no deja una salida sin caja; por si acaso
        raise CajaNoCorregible("La caja de esa salida no existe.")
    if not caja.abierta:
        que_no_este_vencida(caja, ahora, CajaNoCorregible)
    correccion = CorreccionDeCaja(
        dato=DatoDeCaja.SALIDA,
        caja_id=caja.id,
        valor_antes=salida.valor,
        valor_despues=0,
        detalle=reglas.describir_salida(salida.tipo, salida.categoria, salida.descripcion),
        motivo=limpio[:200],
        administrador_id=administrador_id,
        fecha_hora=ahora,
    )
    anotar_en_la_caja(correccion, caja)
    salida.anulada_en = ahora
    salida.anulada_por = administrador_id
    salida.motivo_anulacion = limpio[:200]
    sesion.add(correccion)
    sesion.flush()
    return salida


def salidas_por_caja(sesion: Session, caja_ids: Sequence[int]) -> dict[int, int]:
    """Cuánta plata salió de cada caja (sin las salidas anuladas)."""
    if not caja_ids:
        return {}
    filas = sesion.execute(
        select(SalidaDeCaja.caja_id, func.sum(SalidaDeCaja.valor))
        .where(SalidaDeCaja.caja_id.in_(caja_ids), SalidaDeCaja.anulada_en.is_(None))
        .group_by(SalidaDeCaja.caja_id)
    )
    return {caja_id: int(total) for caja_id, total in filas}
