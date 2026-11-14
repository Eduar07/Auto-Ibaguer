"""Lo que se pregunta sobre las cajas de cualquier negocio, sin cambiar nada: los cierres
anteriores con sus filtros (desde y hasta qué día, cómo cerró la caja y quién la cerró) y sus
cuentas.

Cada negocio pone lo suyo: cuánto entró a sus cajas y cómo se resume cada una en su pantalla.
"""

from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import Select, exists, func, or_, select
from sqlalchemy.orm import Session

from app import formato, tiempo
from app.caja import reglas
from app.caja.estados import (
    NOMBRE_DE_LA_CATEGORIA,
    NOMBRE_DE_LA_SALIDA,
    DatoDeCaja,
    Negocio,
    TipoCorreccion,
    TipoDeSalida,
)
from app.caja.tablas import Caja, CorreccionCobro, CorreccionDeCaja, SalidaDeCaja
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


# --- Las correcciones de una caja, listas para mostrar ------------------------------------


def _despues_del_cierre(texto: str, despues_del_cierre: bool) -> str:
    return f"{texto}, después del cierre" if despues_del_cierre else texto


@dataclass(frozen=True)
class FilaCorreccion:
    """Una corrección de un cobro, lista para mostrar: qué cambió y quién lo hizo.

    Tiene lo mismo que FilaCorreccionDeCaja (momento, qué pasó, antes, después, motivo...):
    las listas de correcciones muestran las dos sin preguntar cuál es cuál."""

    correccion: CorreccionCobro
    quien: str
    # Solo en las listas de correcciones hechas a cajas de otros días: de qué día era la caja.
    dia_de_la_caja: date | None = None

    @property
    def momento(self) -> datetime:
        return self.correccion.fecha_hora

    @property
    def placa(self) -> str | None:
        return self.correccion.placa

    @property
    def detalle(self) -> str:
        return self.correccion.detalle

    @property
    def motivo(self) -> str:
        return self.correccion.motivo

    @property
    def despues_del_cierre(self) -> bool:
        return self.correccion.despues_del_cierre

    @property
    def que_paso(self) -> str:
        """ "Se deshizo la salida", "Se anuló el pago" o "Se corrigió el cobro" """
        return {
            TipoCorreccion.SALIDA_DESHECHA: "Se deshizo la salida",
            TipoCorreccion.PAGO_ANULADO: "Se anuló el pago",
            TipoCorreccion.COBRO_DESHECHO: "Se deshizo el cobro",
            TipoCorreccion.CORREGIDO: "Se corrigió el cobro",
        }[self.correccion.tipo]

    @property
    def antes(self) -> str:
        c = self.correccion
        return f"{formato.pesos(c.valor_antes)} {reglas.como_se_pago(c.medio_antes)}"

    @property
    def despues(self) -> str | None:
        """Cómo quedó, si quedó un cobro (deshecho o anulado no queda nada)."""
        c = self.correccion
        if c.tipo is not TipoCorreccion.CORREGIDO:
            return None
        return f"{formato.pesos(c.valor_despues)} {reglas.como_se_pago(c.medio_despues)}"

    @property
    def cambio_en_efectivo(self) -> int:
        """Cuánto cambió el efectivo que debe haber en el cajón: negativo si salió plata."""
        return self.correccion.cambio_en_efectivo

    @property
    def efecto(self) -> tuple[str, int] | None:
        """Lo que cambió en el cajón, para mostrarlo al lado: ("Efectivo", -6000)."""
        return ("Efectivo", self.cambio_en_efectivo) if self.cambio_en_efectivo else None

    @property
    def en_una_linea(self) -> str:
        """Para el reporte y el ticket: "ABC123: se deshizo la salida ($6.000 en efectivo).
        Motivo: era otro carro. Por eduar." """
        c = self.correccion
        cambio = f"{self.antes} → {self.despues}" if self.despues else self.antes
        que_paso = _despues_del_cierre(f"{self.que_paso.lower()} ({cambio})", c.despues_del_cierre)
        return f"{c.placa}: {que_paso}. Motivo: {c.motivo}. Por {self.quien}."


@dataclass(frozen=True)
class FilaCorreccionDeCaja:
    """Una corrección de la base o del efectivo contado de una caja, lista para mostrar."""

    correccion: CorreccionDeCaja
    quien: str
    dia_de_la_caja: date | None = None

    placa = None  # no es de un vehículo

    @property
    def momento(self) -> datetime:
        return self.correccion.fecha_hora

    @property
    def motivo(self) -> str:
        return self.correccion.motivo

    @property
    def despues_del_cierre(self) -> bool:
        return self.correccion.despues_del_cierre

    @property
    def es_del_conteo(self) -> bool:
        return self.correccion.dato is DatoDeCaja.CONTEO

    @property
    def es_de_una_salida(self) -> bool:
        return self.correccion.dato is DatoDeCaja.SALIDA

    @property
    def que_paso(self) -> str:
        if self.es_de_una_salida:
            return "Se anuló una salida"
        return "Se corrigió el efectivo contado" if self.es_del_conteo else "Se corrigió la base"

    @property
    def detalle(self) -> str:
        if self.es_de_una_salida:
            return self.correccion.detalle or "Plata que salió del cajón"
        return "Lo que se contó al cerrar" if self.es_del_conteo else "La plata para vueltos"

    @property
    def antes(self) -> str:
        return formato.pesos(self.correccion.valor_antes)

    @property
    def despues(self) -> str:
        """Vacío si la salida se anuló: esa plata ya no sale (se muestra tachada)."""
        return "" if self.es_de_una_salida else formato.pesos(self.correccion.valor_despues)

    @property
    def cambio_en_efectivo(self) -> int:
        return self.correccion.cambio_en_efectivo

    @property
    def efecto(self) -> tuple[str, int]:
        c = self.correccion
        if self.es_de_una_salida:
            return "En el cajón", c.cambio_en_efectivo
        return ("Contado" if self.es_del_conteo else "Base", c.valor_despues - c.valor_antes)

    @property
    def en_una_linea(self) -> str:
        """ "Se corrigió el efectivo contado ($150.000 → $105.000), después del cierre.
        Motivo: se escribió mal. Por eduar." """
        if self.es_de_una_salida:
            que_paso = f"Se anuló la salida «{self.detalle}» de {self.antes}"
        else:
            que_paso = f"{self.que_paso} ({self.antes} → {self.despues})"
        que_paso = _despues_del_cierre(que_paso, self.despues_del_cierre)
        return f"{que_paso}. Motivo: {self.motivo}. Por {self.quien}."


Correccion = FilaCorreccion | FilaCorreccionDeCaja


class DatosDelCierre:
    """Lo que el resumen de una caja de cualquier negocio dice de su cierre a partir de la caja y
    sus correcciones: si se corrigió después de cerrar y cómo había cerrado antes de eso. El
    resumen de cada negocio lo hereda y pone `caja` y `correcciones`."""

    caja: Caja
    correcciones: list[Correccion]

    @property
    def corregida_despues_del_cierre(self) -> bool:
        return any(fila.despues_del_cierre for fila in self.correcciones)

    @property
    def conteo_corregido(self) -> bool:
        return any(
            isinstance(fila, FilaCorreccionDeCaja) and fila.es_del_conteo
            for fila in self.correcciones
        )

    @property
    def diferencia_al_cerrar(self) -> int | None:
        """Lo que sobró o faltó al cerrar, antes de las correcciones de después: la caja
        guarda los números ya corregidos, y las correcciones dicen cuánto cambió cada uno."""
        caja = self.caja
        if caja.cerrada_en is None:
            return None
        despues = [fila for fila in self.correcciones if fila.despues_del_cierre]
        esperado = (caja.efectivo_esperado or 0) - sum(f.cambio_en_efectivo for f in despues)
        conteos = [
            fila.correccion
            for fila in despues
            if isinstance(fila, FilaCorreccionDeCaja) and fila.es_del_conteo
        ]
        contado = conteos[0].valor_antes if conteos else (caja.efectivo_contado or 0)
        return contado - esperado


def nombres(sesion: Session, ids: set[int | None]) -> dict[int, str]:
    """El usuario de cada administrador, en una consulta para todos."""
    ids = ids - {None}
    if not ids:
        return {}
    return dict(
        sesion.execute(
            select(Administrador.id, Administrador.usuario).where(Administrador.id.in_(ids))
        ).all()
    )


def filas_de_correcciones(
    de_cobros: Sequence[CorreccionCobro],
    de_cajas: Sequence[CorreccionDeCaja],
    nombres: dict[int, str],
    dias: dict[int, date] | None = None,
) -> list[Correccion]:
    """Las dos clases de correcciones en una sola lista, de la primera a la última."""
    dias = dias or {}
    filas: list[Correccion] = [
        FilaCorreccion(c, nombres.get(c.administrador_id, "—"), dias.get(c.caja_id))
        for c in de_cobros
    ]
    filas += [
        FilaCorreccionDeCaja(c, nombres.get(c.administrador_id, "—"), dias.get(c.caja_id))
        for c in de_cajas
    ]
    return sorted(filas, key=lambda fila: (fila.momento, fila.correccion.id))


def correcciones_de_las_cajas(
    sesion: Session, cajas: Sequence[Caja]
) -> tuple[dict[int, list[Correccion]], dict[int, str]]:
    """Las correcciones de varias cajas, por caja y de la primera a la última, y el usuario de
    cada administrador que abrió, cerró o corrigió alguna: unas pocas consultas para todas."""
    ids = [caja.id for caja in cajas]
    de_cobros = sesion.scalars(
        select(CorreccionCobro).where(CorreccionCobro.caja_id.in_(ids))
    ).all()
    de_cajas = sesion.scalars(
        select(CorreccionDeCaja).where(CorreccionDeCaja.caja_id.in_(ids))
    ).all()
    quienes = {caja.abierta_por for caja in cajas} | {caja.cerrada_por for caja in cajas}
    quienes |= {c.administrador_id for c in [*de_cobros, *de_cajas]}
    usuarios = nombres(sesion, quienes)
    por_caja: dict[int, list[Correccion]] = defaultdict(list)
    for fila in filas_de_correcciones(de_cobros, de_cajas, usuarios):
        por_caja[fila.correccion.caja_id].append(fila)
    return por_caja, usuarios


def correcciones_a_cajas_de_antes(
    sesion: Session, negocio: Negocio, inicio: datetime, fin: datetime
) -> list[Correccion]:
    """Las correcciones hechas entre `inicio` y `fin` a cajas que ya estaban cerradas y se
    abrieron antes de `inicio`: alguien cambió un día que los jefes ya habían revisado, y el
    reporte de hoy se lo cuenta."""
    cajas_de_antes = (
        select(Caja.id, Caja.abierta_en)
        .where(Caja.negocio == negocio, Caja.abierta_en < inicio)
        .subquery()
    )
    de_cobros = sesion.execute(
        select(CorreccionCobro, cajas_de_antes.c.abierta_en)
        .join(cajas_de_antes, cajas_de_antes.c.id == CorreccionCobro.caja_id)
        .where(
            CorreccionCobro.despues_del_cierre,
            CorreccionCobro.fecha_hora >= inicio,
            CorreccionCobro.fecha_hora < fin,
        )
    ).all()
    de_cajas = sesion.execute(
        select(CorreccionDeCaja, cajas_de_antes.c.abierta_en)
        .join(cajas_de_antes, cajas_de_antes.c.id == CorreccionDeCaja.caja_id)
        .where(
            CorreccionDeCaja.despues_del_cierre,
            CorreccionDeCaja.fecha_hora >= inicio,
            CorreccionDeCaja.fecha_hora < fin,
        )
    ).all()
    dias = {
        correccion.caja_id: tiempo.hora_local(abierta_en).date()
        for correccion, abierta_en in [*de_cobros, *de_cajas]
    }
    correcciones = [correccion for correccion, _ in [*de_cobros, *de_cajas]]
    quienes = nombres(sesion, {c.administrador_id for c in correcciones})
    return filas_de_correcciones([c for c, _ in de_cobros], [c for c, _ in de_cajas], quienes, dias)


# --- La plata que sale del cajón -------------------------------------------------------------


@dataclass(frozen=True)
class FilaSalida:
    """Una salida de caja lista para mostrar: quién la registró y, si se anuló, quién."""

    salida: SalidaDeCaja
    registro: str
    anulo: str | None = None

    @property
    def que(self) -> str:
        """ "Gasto · Aseo", "Retiro", "Consignación"."""
        s = self.salida
        nombre = NOMBRE_DE_LA_SALIDA[s.tipo]
        return f"{nombre} · {NOMBRE_DE_LA_CATEGORIA[s.categoria]}" if s.categoria else nombre


def salidas_de_las_cajas(sesion: Session, caja_ids: Sequence[int]) -> dict[int, list[FilaSalida]]:
    """Las salidas de cada caja (también las anuladas, que se muestran tachadas), en orden."""
    if not caja_ids:
        return {}
    salidas = list(
        sesion.scalars(
            select(SalidaDeCaja)
            .where(SalidaDeCaja.caja_id.in_(caja_ids))
            .order_by(SalidaDeCaja.registrada_en, SalidaDeCaja.id)
        )
    )
    quienes = nombres(
        sesion, {s.registrada_por for s in salidas} | {s.anulada_por for s in salidas}
    )
    por_caja: dict[int, list[FilaSalida]] = {caja_id: [] for caja_id in caja_ids}
    for s in salidas:
        por_caja[s.caja_id].append(
            FilaSalida(
                s,
                quienes.get(s.registrada_por, "—"),
                quienes.get(s.anulada_por, "—") if s.anulada else None,
            )
        )
    return por_caja


@dataclass(frozen=True)
class SalidasDelPeriodo:
    """Lo que salió de las cajas de un negocio en un periodo (sin las anuladas): los gastos
    por categoría, y aparte lo que se llevaron los jefes y lo que se consignó."""

    gastos: list[tuple[str, int, int]]  # (categoría, total, cuántos), de mayor a menor
    retiros: int = 0
    consignaciones: int = 0

    @property
    def total_gastos(self) -> int:
        return sum(total for _, total, _ in self.gastos)

    @property
    def total(self) -> int:
        return self.total_gastos + self.retiros + self.consignaciones


def salidas_del_periodo(
    sesion: Session, negocio: Negocio, inicio: datetime, fin: datetime
) -> SalidasDelPeriodo:
    """Las salidas registradas entre `inicio` y `fin` (sin incluirlo), sin las anuladas."""
    filas = sesion.execute(
        select(
            SalidaDeCaja.tipo,
            SalidaDeCaja.categoria,
            func.sum(SalidaDeCaja.valor),
            func.count(),
        )
        .join(Caja, Caja.id == SalidaDeCaja.caja_id)
        .where(
            Caja.negocio == negocio,
            SalidaDeCaja.anulada_en.is_(None),
            SalidaDeCaja.registrada_en >= inicio,
            SalidaDeCaja.registrada_en < fin,
        )
        .group_by(SalidaDeCaja.tipo, SalidaDeCaja.categoria)
    ).all()
    gastos = sorted(
        (
            (NOMBRE_DE_LA_CATEGORIA[categoria], int(total), cuantos)
            for tipo, categoria, total, cuantos in filas
            if tipo is TipoDeSalida.GASTO
        ),
        key=lambda gasto: -gasto[1],
    )
    por_tipo = {tipo: int(total) for tipo, _, total, _ in filas if tipo is not TipoDeSalida.GASTO}
    return SalidasDelPeriodo(
        gastos=gastos,
        retiros=por_tipo.get(TipoDeSalida.RETIRO, 0),
        consignaciones=por_tipo.get(TipoDeSalida.CONSIGNACION, 0),
    )


def lineas_de_salidas_para_excel(
    sesion: Session, negocio: Negocio, inicio: datetime, fin: datetime
) -> list[tuple[str, ...]]:
    """Cada salida del periodo (también las anuladas, marcadas), para el archivo de Excel."""
    salidas = list(
        sesion.scalars(
            select(SalidaDeCaja)
            .join(Caja, Caja.id == SalidaDeCaja.caja_id)
            .where(
                Caja.negocio == negocio,
                SalidaDeCaja.registrada_en >= inicio,
                SalidaDeCaja.registrada_en < fin,
            )
            .order_by(SalidaDeCaja.registrada_en, SalidaDeCaja.id)
        )
    )
    quienes = nombres(
        sesion, {s.registrada_por for s in salidas} | {s.anulada_por for s in salidas}
    )
    lineas: list[tuple[str, ...]] = [
        (
            "Fecha",
            "Hora",
            "Caja",
            "Tipo",
            "Categoría",
            "Descripción",
            "Valor",
            "Registró",
            "Anulada",
            "Motivo de la anulación",
        )
    ]
    for s in salidas:
        local = tiempo.hora_local(s.registrada_en)
        lineas.append(
            (
                f"{local:%d/%m/%Y}",
                formato.hora(local),
                str(s.caja_id),
                NOMBRE_DE_LA_SALIDA[s.tipo],
                NOMBRE_DE_LA_CATEGORIA[s.categoria] if s.categoria else "",
                s.descripcion,
                str(s.valor),
                quienes.get(s.registrada_por, "—"),
                "Sí" if s.anulada else "No",
                s.motivo_anulacion or "",
            )
        )
    return lineas
