"""Tablas de turnos, historial de estados y pagos."""

from datetime import date, datetime
from enum import StrEnum

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
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, lista_cerrada


class EstadoTurno(StrEnum):
    """Los estados posibles de un turno. La base de datos rechaza cualquier otro."""

    EN_FILA = "en_fila"
    LLAMADO = "llamado"
    LLEGO = "llego"
    EN_LAVADO = "en_lavado"
    LISTO = "listo"
    PAGADO = "pagado"
    CANCELADO = "cancelado"
    NO_LLEGO = "no_llego"


# Un turno en estos estados todavía está "vivo" en el lavadero.
ESTADOS_ACTIVOS = frozenset(
    {
        EstadoTurno.EN_FILA,
        EstadoTurno.LLAMADO,
        EstadoTurno.LLEGO,
        EstadoTurno.EN_LAVADO,
        EstadoTurno.LISTO,
    }
)


class OrigenTurno(StrEnum):
    WHATSAPP = "whatsapp"
    DASHBOARD = "dashboard"


class Actor(StrEnum):
    """Quién hizo un cambio de estado."""

    CLIENTE = "cliente"
    ADMINISTRADOR = "administrador"
    SISTEMA = "sistema"


class MedioPago(StrEnum):
    EFECTIVO = "efectivo"
    NEQUI = "nequi"
    DAVIPLATA = "daviplata"
    TARJETA = "tarjeta"


class Turno(Base):
    """Cada turno, desde que entra a la fila hasta que se paga.

    El número que ve el cliente (C-14) no se guarda como texto: se arma con el
    prefijo de la fila y el consecutivo del día.
    """

    __tablename__ = "turno"
    __table_args__ = (
        # El consecutivo vuelve a 1 cada día y no se repite en la misma fila.
        UniqueConstraint("fila_id", "fecha", "consecutivo"),
        CheckConstraint("consecutivo > 0", name="consecutivo_positivo"),
        CheckConstraint("intentos_sin_llegar BETWEEN 0 AND 2", name="intentos_entre_0_y_2"),
        CheckConstraint(
            "estado <> 'en_lavado' OR puesto_id IS NOT NULL", name="en_lavado_con_puesto"
        ),
        CheckConstraint(
            "estado <> 'llamado' OR (llamado_en IS NOT NULL AND gracia_hasta IS NOT NULL)",
            name="llamado_con_hora_limite",
        ),
        # Un puesto no puede tener dos vehículos lavándose al mismo tiempo, aunque dos
        # personas toquen "Pasar a puesto" en el mismo segundo.
        Index(
            "uq_turno_puesto_en_lavado",
            "puesto_id",
            unique=True,
            postgresql_where=text("estado = 'en_lavado'"),
        ),
        # Así se lee la fila de espera; el índice la hace rápida.
        Index(
            "ix_turno_fila_de_espera",
            "fila_id",
            "en_fila_desde",
            postgresql_where=text("estado IN ('en_fila', 'llamado', 'llego')"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    fila_id: Mapped[int] = mapped_column(ForeignKey("fila.id"))
    # Día del turno en la hora de Bogotá.
    fecha: Mapped[date] = mapped_column(Date)
    consecutivo: Mapped[int] = mapped_column(SmallInteger)
    cliente_id: Mapped[int] = mapped_column(ForeignKey("cliente.id"), index=True)
    vehiculo_id: Mapped[int] = mapped_column(ForeignKey("vehiculo.id"), index=True)
    # Vacío = "servicio por definir"; la espera se calcula con el servicio por defecto.
    servicio_id: Mapped[int | None] = mapped_column(ForeignKey("servicio.id"))
    puesto_id: Mapped[int | None] = mapped_column(ForeignKey("puesto.id"))
    estado: Mapped[EstadoTurno] = mapped_column(
        lista_cerrada(EstadoTurno, "estado"),
        default=EstadoTurno.EN_FILA,
        server_default=EstadoTurno.EN_FILA.value,
    )
    origen: Mapped[OrigenTurno] = mapped_column(lista_cerrada(OrigenTurno, "origen"))
    # Marca informativa: el cliente avisó que viene en camino. No es un estado.
    en_camino: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    # 1 = ya pasó al final una vez; con la segunda queda en "no_llego".
    intentos_sin_llegar: Mapped[int] = mapped_column(
        SmallInteger, default=0, server_default=text("0")
    )
    # Lugar en la fila: se ordena por esta hora. Si pasa al final, se actualiza.
    en_fila_desde: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    llamado_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    gracia_hasta: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    actualizado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class TurnoHistorial(Base):
    """Cada cambio de estado, con hora y quién lo hizo. Nunca se borra.

    Sirve para auditar, para medir cuánto dura de verdad cada lavado y para los reportes.
    """

    __tablename__ = "turno_historial"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    turno_id: Mapped[int] = mapped_column(ForeignKey("turno.id"), index=True)
    # Vacío en el primer registro, cuando el turno se crea.
    estado_anterior: Mapped[EstadoTurno | None] = mapped_column(
        lista_cerrada(EstadoTurno, "estado_anterior")
    )
    estado_nuevo: Mapped[EstadoTurno] = mapped_column(lista_cerrada(EstadoTurno, "estado_nuevo"))
    actor: Mapped[Actor] = mapped_column(lista_cerrada(Actor, "actor"))
    administrador_id: Mapped[int | None] = mapped_column(ForeignKey("administrador.id"))
    detalle: Mapped[str | None] = mapped_column(Text)
    fecha_hora: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Pago(Base):
    """El cobro de un turno.

    Guarda una copia del precio del menú en ese momento: si mañana suben los precios,
    los cobros de hoy no cambian.
    """

    __tablename__ = "pago"
    __table_args__ = (
        CheckConstraint("precio_menu >= 0", name="precio_menu_no_negativo"),
        CheckConstraint("precio_cobrado >= 0", name="precio_cobrado_no_negativo"),
        # Si el administrador cambia el precio, debe escribir el motivo.
        CheckConstraint(
            "precio_cobrado = precio_menu OR btrim(coalesce(motivo_ajuste, '')) <> ''",
            name="motivo_si_cambia_el_precio",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    turno_id: Mapped[int] = mapped_column(ForeignKey("turno.id"), unique=True)
    precio_menu: Mapped[int] = mapped_column(Integer)
    precio_cobrado: Mapped[int] = mapped_column(Integer)
    motivo_ajuste: Mapped[str | None] = mapped_column(Text)
    medio_pago: Mapped[MedioPago] = mapped_column(lista_cerrada(MedioPago, "medio_pago"))
    administrador_id: Mapped[int | None] = mapped_column(ForeignKey("administrador.id"))
    fecha_hora: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
