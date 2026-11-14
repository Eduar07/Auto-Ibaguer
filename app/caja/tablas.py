"""Tablas de la caja de cada negocio: la caja misma, la plata que sale de ella (gastos, retiros
y consignaciones) y sus correcciones (de un cobro equivocado, y de la base, el conteo o una
salida de la caja).

Las reglas de plata también viven en la base (CHECK e índices únicos): aunque dos personas
toquen "Abrir caja" en el mismo segundo, o un error del programa lo intente, la base no deja
dos cajas abiertas del mismo negocio ni una corrección sin motivo.
"""

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.caja.estados import CategoriaDeGasto, DatoDeCaja, Negocio, TipoCorreccion, TipoDeSalida
from app.db.base import Base, lista_cerrada
from app.turnos.estados import MedioPago


class Caja(Base):
    """Un turno de caja de un negocio: se abre con una base y se cierra contando el efectivo.

    Cada negocio tiene la suya, aparte: la plata del lavadero no se mezcla con la del
    parqueadero. De cada negocio solo puede haber una abierta a la vez.
    """

    __tablename__ = "caja"
    __table_args__ = (
        CheckConstraint("base >= 0", name="base_no_negativa"),
        CheckConstraint("abierta = (cerrada_en IS NULL)", name="abierta_sin_cierre"),
        CheckConstraint(
            "cerrada_en IS NULL "
            "OR (efectivo_contado IS NOT NULL AND efectivo_esperado IS NOT NULL)",
            name="cerrada_con_conteo",
        ),
        CheckConstraint(
            "efectivo_contado IS NULL OR efectivo_contado >= 0", name="conteo_no_negativo"
        ),
        # Una sola caja abierta por negocio, aunque dos personas toquen "Abrir caja" a la vez.
        Index(
            "uq_caja_una_abierta_por_negocio",
            "negocio",
            unique=True,
            postgresql_where=text("abierta"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    negocio: Mapped[Negocio] = mapped_column(lista_cerrada(Negocio, "negocio"))
    abierta: Mapped[bool] = mapped_column(default=True, server_default=text("true"))
    base: Mapped[int] = mapped_column(Integer)
    abierta_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    abierta_por: Mapped[int | None] = mapped_column(ForeignKey("administrador.id"))
    nota_apertura: Mapped[str | None] = mapped_column(Text)
    cerrada_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cerrada_por: Mapped[int | None] = mapped_column(ForeignKey("administrador.id"))
    # Lo que se contó en el cajón y lo que el sistema esperaba (base + efectivo), al cerrar.
    # Si después se corrige la caja (un cobro, la base o el conteo), estos dos se ponen al
    # día con la corrección; cómo estaban al cerrar se saca de las correcciones.
    efectivo_contado: Mapped[int | None] = mapped_column(Integer)
    efectivo_esperado: Mapped[int | None] = mapped_column(Integer)
    nota_cierre: Mapped[str | None] = mapped_column(Text)

    @property
    def diferencia(self) -> int | None:
        """Lo que sobró (positivo) o faltó (negativo) al contar el cajón, con las correcciones
        que se le hayan hecho después del cierre."""
        if self.efectivo_contado is None or self.efectivo_esperado is None:
            return None
        return self.efectivo_contado - self.efectivo_esperado


class CorreccionCobro(Base):
    """Un cobro equivocado que se corrigió: cómo estaba, cómo quedó, por qué y quién lo hizo.

    El cobro es de un negocio: una salida o un pago de mensualidad del parqueadero, o el cobro
    de un turno del lavadero. Solo se agregan filas: nunca se cambian ni se borran. Es lo que
    revisan el cierre de caja y los jefes: si alguien cobra en efectivo y después "anula" para
    quedarse la plata, aquí se ve. El cobro mismo sí cambia (o se borra, si se anuló o se
    deshizo), y así la caja, el balance y el Excel quedan con lo que de verdad pasó.
    """

    __tablename__ = "correccion_cobro"
    __table_args__ = (
        CheckConstraint("placa ~ '^[A-Z0-9]{4,7}$'", name="placa_limpia"),
        CheckConstraint("btrim(motivo) <> ''", name="con_motivo"),
        CheckConstraint("valor_antes >= 0 AND valor_despues >= 0", name="valores_no_negativos"),
        # De un solo cobro: una salida, un pago de mensualidad o el cobro de un turno.
        CheckConstraint(
            "num_nonnulls(parqueo_id, mensualidad_id, turno_id) = 1", name="de_un_cobro"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    tipo: Mapped[TipoCorreccion] = mapped_column(lista_cerrada(TipoCorreccion, "tipo_correccion"))
    # La caja del cobro. Si ya estaba cerrada al corregirlo, `despues_del_cierre`: su cuadre
    # cambió después de contado y eso se le muestra a quien revisa.
    caja_id: Mapped[int] = mapped_column(ForeignKey("caja.id"), index=True)
    despues_del_cierre: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    parqueo_id: Mapped[int | None] = mapped_column(ForeignKey("parqueo.id"), index=True)
    mensualidad_id: Mapped[int | None] = mapped_column(ForeignKey("mensualidad.id"))
    turno_id: Mapped[int | None] = mapped_column(ForeignKey("turno.id"), index=True)
    # El pago corregido (de la mensualidad o del turno). Sin llave: un pago anulado se borra.
    pago_id: Mapped[int | None] = mapped_column(BigInteger)
    placa: Mapped[str] = mapped_column(String(7))
    # Lo que decía el cobro y lo que quedó (en 0 y sin medio si se deshizo o se anuló).
    valor_antes: Mapped[int] = mapped_column(Integer)
    valor_despues: Mapped[int] = mapped_column(Integer)
    medio_antes: Mapped[MedioPago | None] = mapped_column(lista_cerrada(MedioPago, "medio_antes"))
    medio_despues: Mapped[MedioPago | None] = mapped_column(
        lista_cerrada(MedioPago, "medio_despues")
    )
    # Qué cobro era, en palabras: "Salida de las 10:05 a.m. · ticket 123".
    detalle: Mapped[str] = mapped_column(Text)
    motivo: Mapped[str] = mapped_column(Text)
    administrador_id: Mapped[int | None] = mapped_column(ForeignKey("administrador.id"))
    fecha_hora: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    @property
    def cambio_en_efectivo(self) -> int:
        """Cuánto cambió el efectivo que debe haber en el cajón: negativo si salió plata."""
        despues = self.valor_despues if self.medio_despues is MedioPago.EFECTIVO else 0
        antes = self.valor_antes if self.medio_antes is MedioPago.EFECTIVO else 0
        return despues - antes


class CorreccionDeCaja(Base):
    """Un dato de la caja misma que se corrigió: la base con que se abrió o el efectivo que se
    contó al cerrar (se escribió $150.000 y eran $105.000).

    Como en las correcciones de cobros, solo se agregan filas: la caja queda con el valor
    nuevo y aquí queda cómo estaba, por qué se cambió y quién lo hizo.
    """

    __tablename__ = "correccion_de_caja"
    __table_args__ = (
        CheckConstraint("btrim(motivo) <> ''", name="con_motivo"),
        CheckConstraint("valor_antes >= 0 AND valor_despues >= 0", name="valores_no_negativos"),
        CheckConstraint("valor_antes <> valor_despues", name="algo_cambio"),
        # El conteo solo existe desde el cierre: corregirlo siempre es después de cerrar.
        CheckConstraint("dato <> 'conteo' OR despues_del_cierre", name="conteo_ya_cerrada"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    dato: Mapped[DatoDeCaja] = mapped_column(lista_cerrada(DatoDeCaja, "dato"))
    caja_id: Mapped[int] = mapped_column(ForeignKey("caja.id"), index=True)
    despues_del_cierre: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    valor_antes: Mapped[int] = mapped_column(Integer)
    valor_despues: Mapped[int] = mapped_column(Integer)
    # Qué salida se anuló, en palabras ("Gasto · Aseo: bolsas de basura"). Solo en las de
    # una salida: la base y el conteo se explican solos.
    detalle: Mapped[str | None] = mapped_column(Text)
    motivo: Mapped[str] = mapped_column(Text)
    administrador_id: Mapped[int | None] = mapped_column(ForeignKey("administrador.id"))
    fecha_hora: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    @property
    def cambio_en_efectivo(self) -> int:
        """Cuánto cambió el efectivo que debe haber en el cajón. La base cuenta; una salida
        anulada también (esa plata no salió: debe estar); el conteo no (cambia lo que se
        contó, no lo que debía haber)."""
        if self.dato is DatoDeCaja.BASE:
            return self.valor_despues - self.valor_antes
        if self.dato is DatoDeCaja.SALIDA:
            return self.valor_antes - self.valor_despues
        return 0


class SalidaDeCaja(Base):
    """Plata que salió del cajón: un gasto (con su categoría), un retiro o una consignación.

    Lo que debe haber al cerrar es la base, más lo cobrado en efectivo, menos las salidas. Una
    salida no se borra: se anula, con motivo y quién (si ya se cerró la caja, también queda
    como corrección de la caja). Así nadie saca plata "sin que quede escrito".
    """

    __tablename__ = "salida_de_caja"
    __table_args__ = (
        CheckConstraint("valor > 0", name="valor_positivo"),
        CheckConstraint("btrim(descripcion) <> ''", name="con_descripcion"),
        # Un gasto dice en qué; un retiro o una consignación no tienen categoría.
        CheckConstraint("(tipo = 'gasto') = (categoria IS NOT NULL)", name="categoria_del_gasto"),
        # Anulada: cuándo, quién y por qué, juntos.
        CheckConstraint(
            "(anulada_en IS NULL) = (motivo_anulacion IS NULL)", name="anulada_con_motivo"
        ),
        CheckConstraint(
            "motivo_anulacion IS NULL OR btrim(motivo_anulacion) <> ''", name="motivo_no_vacio"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    caja_id: Mapped[int] = mapped_column(ForeignKey("caja.id"), index=True)
    tipo: Mapped[TipoDeSalida] = mapped_column(lista_cerrada(TipoDeSalida, "tipo_de_salida"))
    categoria: Mapped[CategoriaDeGasto | None] = mapped_column(
        lista_cerrada(CategoriaDeGasto, "categoria_de_gasto")
    )
    descripcion: Mapped[str] = mapped_column(Text)
    valor: Mapped[int] = mapped_column(Integer)
    registrada_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    registrada_por: Mapped[int | None] = mapped_column(ForeignKey("administrador.id"))
    anulada_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    anulada_por: Mapped[int | None] = mapped_column(ForeignKey("administrador.id"))
    motivo_anulacion: Mapped[str | None] = mapped_column(Text)

    @property
    def anulada(self) -> bool:
        return self.anulada_en is not None


class GastoFueraDeCaja(Base):
    """Un gasto del negocio que no salió del cajón: el arriendo por transferencia, la luz por
    Nequi, la nómina por el banco. No toca ninguna caja, pero sí el resultado del mes.

    Como las salidas de caja, no se borra: se anula con motivo y quién."""

    __tablename__ = "gasto_fuera_de_caja"
    __table_args__ = (
        CheckConstraint("valor > 0", name="valor_positivo"),
        CheckConstraint("btrim(descripcion) <> ''", name="con_descripcion"),
        CheckConstraint(
            "(anulado_en IS NULL) = (motivo_anulacion IS NULL)", name="anulado_con_motivo"
        ),
        CheckConstraint(
            "motivo_anulacion IS NULL OR btrim(motivo_anulacion) <> ''", name="motivo_no_vacio"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    negocio: Mapped[Negocio] = mapped_column(lista_cerrada(Negocio, "negocio"))
    fecha: Mapped[date] = mapped_column(Date, index=True)  # el día en que se pagó
    categoria: Mapped[CategoriaDeGasto] = mapped_column(
        lista_cerrada(CategoriaDeGasto, "categoria_de_gasto")
    )
    descripcion: Mapped[str] = mapped_column(Text)
    valor: Mapped[int] = mapped_column(Integer)
    registrado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    registrado_por: Mapped[int | None] = mapped_column(ForeignKey("administrador.id"))
    anulado_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    anulado_por: Mapped[int | None] = mapped_column(ForeignKey("administrador.id"))
    motivo_anulacion: Mapped[str | None] = mapped_column(Text)

    @property
    def anulado(self) -> bool:
        return self.anulado_en is not None


class CierreDeMes(Base):
    """Un mes de un negocio entregado al contador: desde ese momento sus cajas, sus salidas y
    sus gastos ya no se cambian (lo que haya que arreglar va en el mes siguiente, como hace
    cualquier contabilidad). Guarda las cifras tal como se entregaron."""

    __tablename__ = "cierre_de_mes"
    __table_args__ = (
        CheckConstraint("extract(day FROM mes) = 1", name="primer_dia_del_mes"),
        Index("uq_cierre_de_mes_negocio_mes", "negocio", "mes", unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    negocio: Mapped[Negocio] = mapped_column(lista_cerrada(Negocio, "negocio"))
    mes: Mapped[date] = mapped_column(Date)  # el primer día del mes
    cerrado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    cerrado_por: Mapped[int | None] = mapped_column(ForeignKey("administrador.id"))
    # Las cifras que se entregaron: ingresos por medio, gastos por categoría, retiros,
    # consignaciones y el resultado (app.resultados).
    cifras: Mapped[dict] = mapped_column(JSONB)
