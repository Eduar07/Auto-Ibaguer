"""Lo que se pregunta sobre las cajas de cualquier negocio, sin cambiar nada: los cierres
anteriores con sus filtros (desde y hasta qué día, cómo cerró la caja y quién la cerró) y sus
cuentas.

Cada negocio pone lo suyo: cuánto entró a sus cajas y cómo se resume cada una en su pantalla.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import Select, exists, func, or_, select
from sqlalchemy.orm import Session

from app import tiempo
from app.caja.estados import Negocio
from app.caja.tablas import Caja, CorreccionCobro, CorreccionDeCaja
from app.web.tablas import Administrador

# Cómo cerró la caja: cuadró, sobró o faltó plata, o se corrigió después del cierre.
RESULTADOS = {
    "todos": "Todos",
    "cuadro": "Cuadraron",
    "sobro": "Sobró",
    "falto": "Faltó",
    "corregida": "Corregidas",
}
CIERRES_POR_PAGINA = 30


@dataclass(frozen=True)
class FiltroDeCierres:
    """Qué cierres ver. Los días son los de apertura, como en el Balance: cada caja es del día
    en que se abrió, aunque se cierre pasada la medianoche."""

    desde: date | None = None
    hasta: date | None = None
    resultado: str = "todos"
    quien: int | None = None  # el administrador que cerró

    @property
    def filtra(self) -> bool:
        return bool(self.desde or self.hasta or self.quien) or self.resultado != "todos"


@dataclass(frozen=True)
class Cierres[Fila]:
    """Una página de cierres y las cuentas de todos los que cumplen el filtro (no solo de la
    página): cuántos son, lo que entró y lo que sobró y faltó, cada cosa aparte."""

    filas: list[Fila]
    cuantos: int
    entro: int
    cuadraron: int
    sobrantes: int
    cuantos_sobrantes: int
    faltantes: int
    cuantos_faltantes: int
    pagina: int
    paginas: int


# Cuánto entró a un grupo de cajas (por cualquier medio), dado como una consulta de sus ids.
LoQueEntro = Callable[[Session, Select], int]
# Cómo resume el negocio cada caja en su pantalla.
Resumir = Callable[[Session, Sequence[Caja]], list]


def cajas_del_filtro(negocio: Negocio, filtro: FiltroDeCierres) -> Select:
    """Las cajas cerradas del negocio que cumplen el filtro, como una consulta de sus ids para
    usar adentro de otras."""
    consulta = select(Caja.id).where(Caja.negocio == negocio, Caja.cerrada_en.is_not(None))
    zona = tiempo.zona_horaria()
    if filtro.desde:
        inicio = datetime.combine(filtro.desde, datetime.min.time(), zona)
        consulta = consulta.where(Caja.abierta_en >= inicio)
    if filtro.hasta:
        fin = datetime.combine(filtro.hasta + timedelta(days=1), datetime.min.time(), zona)
        consulta = consulta.where(Caja.abierta_en < fin)
    if filtro.quien:
        consulta = consulta.where(Caja.cerrada_por == filtro.quien)
    contado, esperado = Caja.efectivo_contado, Caja.efectivo_esperado
    if filtro.resultado == "cuadro":
        consulta = consulta.where(contado == esperado)
    elif filtro.resultado == "sobro":
        consulta = consulta.where(contado > esperado)
    elif filtro.resultado == "falto":
        consulta = consulta.where(contado < esperado)
    elif filtro.resultado == "corregida":
        consulta = consulta.where(
            or_(
                exists().where(
                    CorreccionCobro.caja_id == Caja.id,
                    CorreccionCobro.despues_del_cierre,
                ),
                exists().where(
                    CorreccionDeCaja.caja_id == Caja.id,
                    CorreccionDeCaja.despues_del_cierre,
                ),
            )
        )
    return consulta


def cierres(
    sesion: Session,
    negocio: Negocio,
    filtro: FiltroDeCierres,
    pagina: int,
    *,
    entro: LoQueEntro,
    resumir: Resumir,
) -> Cierres:
    """Los cierres del negocio que cumplen el filtro, del más reciente al más viejo, de a 30
    por página. Siempre las mismas pocas consultas, haya 10 cierres o mil."""
    ids = cajas_del_filtro(negocio, filtro).subquery()
    contado, esperado = Caja.efectivo_contado, Caja.efectivo_esperado
    cuentas = sesion.execute(
        select(
            func.count(),
            func.count().filter(contado == esperado),
            func.coalesce(func.sum(contado - esperado).filter(contado > esperado), 0),
            func.count().filter(contado > esperado),
            func.coalesce(func.sum(esperado - contado).filter(contado < esperado), 0),
            func.count().filter(contado < esperado),
        ).where(Caja.id.in_(select(ids.c.id)))
    ).one()
    cuantos = cuentas[0]
    paginas = max(1, -(-cuantos // CIERRES_POR_PAGINA))
    pagina = min(max(1, pagina), paginas)
    cajas = sesion.scalars(
        select(Caja)
        .where(Caja.id.in_(select(ids.c.id)))
        .order_by(Caja.cerrada_en.desc(), Caja.id.desc())
        .offset((pagina - 1) * CIERRES_POR_PAGINA)
        .limit(CIERRES_POR_PAGINA)
    ).all()
    return Cierres(
        filas=resumir(sesion, cajas),
        cuantos=cuantos,
        entro=entro(sesion, select(ids.c.id)),
        cuadraron=cuentas[1],
        sobrantes=int(cuentas[2]),
        cuantos_sobrantes=cuentas[3],
        faltantes=int(cuentas[4]),
        cuantos_faltantes=cuentas[5],
        pagina=pagina,
        paginas=paginas,
    )


def todos_los_cierres(
    sesion: Session, negocio: Negocio, filtro: FiltroDeCierres, *, resumir: Resumir
) -> list:
    """Todos los que cumplen el filtro (para el archivo de Excel), del más viejo al más nuevo."""
    cajas = sesion.scalars(
        select(Caja)
        .where(Caja.id.in_(cajas_del_filtro(negocio, filtro)))
        .order_by(Caja.abierta_en, Caja.id)
    ).all()
    return resumir(sesion, cajas)


def quienes_cerraron(sesion: Session, negocio: Negocio) -> list[tuple[int, str]]:
    """Los administradores que han cerrado alguna caja del negocio, para filtrar por quién."""
    return [
        (id_, usuario)
        for id_, usuario in sesion.execute(
            select(Administrador.id, Administrador.usuario)
            .where(
                exists().where(
                    Caja.negocio == negocio,
                    Caja.cerrada_por == Administrador.id,
                    Caja.cerrada_en.is_not(None),
                )
            )
            .order_by(Administrador.usuario)
        )
    ]


def cajas_abiertas_entre(
    sesion: Session, negocio: Negocio, inicio: datetime, fin: datetime
) -> list[Caja]:
    """Las cajas del negocio que se abrieron en esos días, de la más vieja a la más nueva. Cada
    caja es del día en que se abrió: la que se cierra pasada la medianoche sigue siendo la del
    día anterior, y así cada caja sale en un solo día, una sola semana y un solo mes."""
    return list(
        sesion.scalars(
            select(Caja)
            .where(Caja.negocio == negocio, Caja.abierta_en >= inicio, Caja.abierta_en < fin)
            .order_by(Caja.abierta_en)
        )
    )
