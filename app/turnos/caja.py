"""La caja del lavadero: se abre con una base, entra el cobro de cada turno y se cierra contando
el efectivo. Es aparte de la del parqueadero (la plata no se mezcla).

Lo que es igual en los dos negocios (abrir, cerrar, los candados, corregir la base o el
conteo) vive en app/caja; aquí está lo del lavadero: qué entra a su caja y su cuadre.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.caja import casos_de_uso as cajas
from app.caja.estados import Negocio
from app.caja.reglas import sumar_por_medio
from app.caja.tablas import Caja, CorreccionDeCaja
from app.turnos.estados import MedioPago
from app.turnos.tablas import Pago

NEGOCIO = Negocio.LAVADERO


@dataclass(frozen=True)
class Cuadre:
    """Lo que debería haber en la caja del lavadero: la base más lo que entró en efectivo. Lo
    que entró por Nequi, Daviplata o tarjeta se muestra aparte: no está en el cajón."""

    base: int
    cobros: Mapping[MedioPago, int]

    def por_medio(self, medio: MedioPago) -> int:
        return self.cobros.get(medio, 0)

    @property
    def total(self) -> int:
        """Todo lo que entró en el turno de caja, por cualquier medio (sin la base)."""
        return sum(self.cobros.values())

    @property
    def efectivo_esperado(self) -> int:
        return self.base + self.por_medio(MedioPago.EFECTIVO)


def cuadre(base: int, cobros: Iterable[tuple[MedioPago, int]]) -> Cuadre:
    return Cuadre(base=base, cobros=sumar_por_medio(cobros))


def caja_abierta(sesion: Session) -> Caja | None:
    return cajas.caja_abierta(sesion, NEGOCIO)


def caja_del_lavadero(sesion: Session, caja_id: int) -> Caja | None:
    """La caja con ese número, si es del lavadero: la del parqueadero no se ve desde aquí."""
    caja = sesion.get(Caja, caja_id)
    return caja if caja is not None and caja.negocio == NEGOCIO else None


def abrir_caja(
    sesion: Session,
    *,
    base: int,
    nota: str | None = None,
    administrador_id: int | None = None,
    ahora: datetime | None = None,
) -> Caja:
    """Abre la caja del lavadero con la plata que queda en el cajón para dar vueltos."""
    return cajas.abrir_caja(
        sesion, NEGOCIO, base=base, nota=nota, administrador_id=administrador_id, ahora=ahora
    )


def cuadre_de_caja(sesion: Session, caja: Caja) -> Cuadre:
    """Lo que entró a la caja por cada medio: el cobro de cada turno."""
    return cuadre(
        caja.base,
        (
            (medio, int(total))
            for medio, total in sesion.execute(
                select(Pago.medio_pago, func.sum(Pago.precio_cobrado))
                .where(Pago.caja_id == caja.id, Pago.precio_cobrado > 0)
                .group_by(Pago.medio_pago)
            )
        ),
    )


def cerrar_caja(
    sesion: Session,
    caja_id: int,
    *,
    efectivo_contado: int,
    nota: str | None = None,
    administrador_id: int | None = None,
    ahora: datetime | None = None,
) -> Caja:
    """Cierra la caja del lavadero con lo que se contó en el cajón. Guarda lo que esperaba el
    sistema (base + efectivo de los turnos cobrados) para que la diferencia quede escrita."""
    return cajas.cerrar_caja(
        sesion,
        NEGOCIO,
        caja_id,
        efectivo_esperado=lambda sesion, caja: cuadre_de_caja(sesion, caja).efectivo_esperado,
        efectivo_contado=efectivo_contado,
        nota=nota,
        administrador_id=administrador_id,
        ahora=ahora,
    )


def caja_para_cobrar(sesion: Session) -> Caja:
    """La caja abierta del lavadero, para que un cobro entre a ella (y nadie la cierre mientras
    tanto). Sin caja abierta no se cobra: la plata no tendría dónde contarse."""
    return cajas.caja_para_cobrar(
        sesion, NEGOCIO, "La caja del lavadero está cerrada: ábrela para poder cobrar."
    )


def caja_del_cobro(sesion: Session, caja_id: int | None, ahora: datetime) -> Caja:
    return cajas.caja_del_cobro(sesion, NEGOCIO, caja_id, ahora)


def corregir_base(
    sesion: Session,
    caja_id: int,
    *,
    base: int,
    motivo: str,
    administrador_id: int | None = None,
    ahora: datetime | None = None,
) -> CorreccionDeCaja:
    """Cambia la base con que se abrió una caja del lavadero (app.caja: corregir_base)."""
    return cajas.corregir_base(
        sesion,
        NEGOCIO,
        caja_id,
        base=base,
        motivo=motivo,
        administrador_id=administrador_id,
        ahora=ahora,
    )


def corregir_conteo(
    sesion: Session,
    caja_id: int,
    *,
    efectivo_contado: int,
    motivo: str,
    administrador_id: int | None = None,
    ahora: datetime | None = None,
) -> CorreccionDeCaja:
    """Cambia el efectivo que se contó al cerrar una caja del lavadero (app.caja)."""
    return cajas.corregir_conteo(
        sesion,
        NEGOCIO,
        caja_id,
        efectivo_contado=efectivo_contado,
        motivo=motivo,
        administrador_id=administrador_id,
        ahora=ahora,
    )
