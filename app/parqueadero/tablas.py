"""Tablas del parqueadero: cada entrada (parqueo), las mensualidades y sus pagos. La caja y sus
correcciones son comunes a los dos negocios: viven en app/caja/tablas.py.

Las reglas de plata también viven en la base (CHECK e índices únicos): aunque dos personas
toquen "Cobrar" en el mismo segundo, o un error del programa lo intente, la base no deja
cobrar dos veces ni guardar un cobro sin caja.
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
    SmallInteger,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.caja.tablas import Caja, CorreccionCobro, CorreccionDeCaja  # noqa: F401
from app.db.base import Base, lista_cerrada
from app.parqueadero.estados import EstadoParqueo, TipoParqueo
from app.turnos.estados import MedioPago

_PLACA_LIMPIA = "placa ~ '^[A-Z0-9]{4,7}$'"


class Mensualidad(Base):
    """Un vehículo que paga por mes. El valor lo pone el administrador en cada caso."""

    __tablename__ = "mensualidad"
    __table_args__ = (
        CheckConstraint(_PLACA_LIMPIA, name="placa_limpia"),
        CheckConstraint("valor_mensual > 0", name="valor_positivo"),
        CheckConstraint("telefono IS NULL OR telefono ~ '^[0-9]{8,15}$'", name="telefono_digitos"),
        # Una placa tiene máximo una mensualidad activa.
        Index("uq_mensualidad_placa_activa", "placa", unique=True, postgresql_where=text("activa")),
    )

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    placa: Mapped[str] = mapped_column(String(7))
    tipo: Mapped[TipoParqueo] = mapped_column(lista_cerrada(TipoParqueo, "tipo"))
    titular: Mapped[str] = mapped_column(String(80))
    telefono: Mapped[str | None] = mapped_column(String(15))
    valor_mensual: Mapped[int] = mapped_column(Integer)
    notas: Mapped[str | None] = mapped_column(Text)
    activa: Mapped[bool] = mapped_column(default=True, server_default=text("true"))
    # Ley 1581: el titular autorizó guardar su nombre y su celular (se marca en el local).
    autorizo_datos_en: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    creada_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    actualizada_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class PagoMensualidad(Base):
    """Un pago de mensualidad: qué días cubre, cuánto y en qué caja entró.

    Sin caja: los días que el mensualista ya traía pagados al crear la mensualidad (por
    ejemplo, con el dueño anterior). Cuentan para que salga sin pagar, pero esa plata no entró
    a ninguna caja de este sistema: van con valor 0 y sin medio de pago.
    """

    __tablename__ = "pago_mensualidad"
    __table_args__ = (
        CheckConstraint("hasta >= desde", name="periodo_valido"),
        CheckConstraint("valor >= 0", name="valor_no_negativo"),
        # Con caja: plata cobrada, con su medio de pago. Sin caja: días que ya traía, en 0.
        CheckConstraint(
            "(caja_id IS NULL) = (medio_pago IS NULL) AND (caja_id IS NULL) = (valor = 0)",
            name="cobro_en_caja",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    mensualidad_id: Mapped[int] = mapped_column(ForeignKey("mensualidad.id"), index=True)
    desde: Mapped[date] = mapped_column(Date)
    hasta: Mapped[date] = mapped_column(Date)
    valor: Mapped[int] = mapped_column(Integer)
    medio_pago: Mapped[MedioPago | None] = mapped_column(lista_cerrada(MedioPago, "medio_pago"))
    caja_id: Mapped[int | None] = mapped_column(ForeignKey("caja.id"), index=True)
    administrador_id: Mapped[int | None] = mapped_column(ForeignKey("administrador.id"))
    fecha_hora: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class Parqueo(Base):
    """Cada vez que un vehículo entra al parqueadero. El número del ticket es el id.

    Guarda una copia de la tarifa al entrar (valor de la hora, de la fracción y la gracia):
    si el precio cambia mientras el carro está adentro, paga el de cuando entró.
    """

    __tablename__ = "parqueo"
    __table_args__ = (
        CheckConstraint(_PLACA_LIMPIA, name="placa_limpia"),
        CheckConstraint("valor_hora > 0", name="valor_hora_positivo"),
        CheckConstraint(
            "valor_fraccion IS NULL OR valor_fraccion >= 0", name="fraccion_no_negativa"
        ),
        CheckConstraint("gracia_minutos BETWEEN 0 AND 59", name="gracia_valida"),
        CheckConstraint("valor_dia IS NULL OR valor_dia >= valor_hora", name="dia_valido"),
        CheckConstraint(
            "valor_dia_siguiente IS NULL OR (valor_dia IS NOT NULL AND valor_dia_siguiente > 0)",
            name="dia_siguiente_valido",
        ),
        CheckConstraint("estado = 'adentro' OR salida_en IS NOT NULL", name="salida_con_hora"),
        CheckConstraint(
            "estado <> 'salio' OR (valor_calculado IS NOT NULL AND valor_cobrado IS NOT NULL)",
            name="salida_con_valor",
        ),
        CheckConstraint(
            "valor_cobrado IS NULL OR valor_cobrado >= 0", name="valor_cobrado_no_negativo"
        ),
        # Si se cobró plata, quedó en una caja y con un medio de pago.
        CheckConstraint(
            "coalesce(valor_cobrado, 0) = 0 OR (caja_id IS NOT NULL AND medio_pago IS NOT NULL)",
            name="cobro_en_caja",
        ),
        # Si se cobró distinto a la tarifa, se escribió el motivo.
        CheckConstraint(
            "valor_cobrado IS NULL OR valor_cobrado = valor_calculado "
            "OR btrim(coalesce(motivo_ajuste, '')) <> ''",
            name="motivo_si_cambia_el_valor",
        ),
        CheckConstraint("recibido IS NULL OR recibido >= valor_cobrado", name="recibido_alcanza"),
        CheckConstraint(
            "estado <> 'anulado' OR btrim(coalesce(motivo_anulacion, '')) <> ''",
            name="anulado_con_motivo",
        ),
        # Una placa adentro una sola vez: dos entradas abiertas serían dos cobros.
        Index(
            "uq_parqueo_placa_adentro",
            "placa",
            unique=True,
            postgresql_where=text("estado = 'adentro'"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    placa: Mapped[str] = mapped_column(String(7))
    tipo: Mapped[TipoParqueo] = mapped_column(lista_cerrada(TipoParqueo, "tipo"))
    estado: Mapped[EstadoParqueo] = mapped_column(
        lista_cerrada(EstadoParqueo, "estado"),
        default=EstadoParqueo.ADENTRO,
        server_default=EstadoParqueo.ADENTRO.value,
    )
    entrada_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    salida_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    # La tarifa con la que entró.
    valor_hora: Mapped[int] = mapped_column(Integer)
    valor_fraccion: Mapped[int | None] = mapped_column(Integer)
    gracia_minutos: Mapped[int] = mapped_column(SmallInteger)
    # El cobro por días con que entró (vacío: todo por horas). Ver reglas.calcular_cobro.
    valor_dia: Mapped[int | None] = mapped_column(Integer)
    valor_dia_siguiente: Mapped[int | None] = mapped_column(Integer)
    # Cómo entró el vehículo o lo que dejó adentro (un casco, un rayón): sale en el ticket.
    observaciones: Mapped[str | None] = mapped_column(Text)
    # Al salir: si era mensualista al día, el tiempo, lo que dio la tarifa y lo que se cobró.
    mensualidad_id: Mapped[int | None] = mapped_column(ForeignKey("mensualidad.id"))
    minutos: Mapped[int | None] = mapped_column(Integer)
    horas_cobradas: Mapped[int | None] = mapped_column(SmallInteger)
    valor_calculado: Mapped[int | None] = mapped_column(Integer)
    valor_cobrado: Mapped[int | None] = mapped_column(Integer)
    motivo_ajuste: Mapped[str | None] = mapped_column(Text)
    medio_pago: Mapped[MedioPago | None] = mapped_column(lista_cerrada(MedioPago, "medio_pago"))
    # Lo que entregó el cliente en efectivo, solo para el vuelto: no se suma a la caja.
    recibido: Mapped[int | None] = mapped_column(Integer)
    caja_id: Mapped[int | None] = mapped_column(ForeignKey("caja.id"), index=True)
    motivo_anulacion: Mapped[str | None] = mapped_column(Text)
    entrada_por: Mapped[int | None] = mapped_column(ForeignKey("administrador.id"))
    salida_por: Mapped[int | None] = mapped_column(ForeignKey("administrador.id"))
