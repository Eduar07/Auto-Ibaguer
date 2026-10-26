"""Tablas del catálogo: lo que el negocio ofrece y cómo está organizado."""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Fila(Base):
    """Cada fila de espera: Carros y Motos. El prefijo arma el número de turno (C-14, M-03)."""

    __tablename__ = "fila"
    __table_args__ = (CheckConstraint("prefijo ~ '^[A-Z]$'", name="prefijo_una_letra"),)

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    nombre: Mapped[str] = mapped_column(String(30), unique=True)
    prefijo: Mapped[str] = mapped_column(String(1), unique=True)


class Puesto(Base):
    """Un lugar de lavado (C1, M3...). Pasar de 2 a 4 puestos es agregar registros, no código."""

    __tablename__ = "puesto"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    fila_id: Mapped[int] = mapped_column(ForeignKey("fila.id"), index=True)
    nombre: Mapped[str] = mapped_column(String(10), unique=True)
    # Un puesto apagado (por ejemplo, por un daño) sale de la fila sin borrarse.
    activo: Mapped[bool] = mapped_column(default=True, server_default=text("true"))


class TipoVehiculo(Base):
    """Moto, Automóvil o Camioneta. Cada tipo va a una fila y tiene su formato de placa."""

    __tablename__ = "tipo_vehiculo"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    nombre: Mapped[str] = mapped_column(String(30), unique=True)
    fila_id: Mapped[int] = mapped_column(ForeignKey("fila.id"), index=True)
    # Expresión regular de la placa válida. Carro: ^[A-Z]{3}[0-9]{3}$ (ABC123).
    patron_placa: Mapped[str] = mapped_column(String(60))


class Servicio(Base):
    """Un servicio del menú (Básico, Encerado...). Su precio depende del tipo de vehículo."""

    __tablename__ = "servicio"
    __table_args__ = (UniqueConstraint("fila_id", "nombre"),)

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    fila_id: Mapped[int] = mapped_column(ForeignKey("fila.id"), index=True)
    nombre: Mapped[str] = mapped_column(String(40))
    descripcion: Mapped[str] = mapped_column(Text)
    # Orden en que el bot muestra el menú.
    orden: Mapped[int] = mapped_column(SmallInteger, default=0, server_default=text("0"))
    activo: Mapped[bool] = mapped_column(default=True, server_default=text("true"))


class ServicioPrecio(Base):
    """Precio y duración estimada de un servicio para un tipo de vehículo.

    Agregar un tipo de vehículo nuevo (por ejemplo, buseta) son filas nuevas aquí,
    no columnas nuevas.
    """

    __tablename__ = "servicio_precio"
    __table_args__ = (
        CheckConstraint("precio >= 0", name="precio_no_negativo"),
        CheckConstraint("duracion_min > 0", name="duracion_positiva"),
        # Un solo precio vigente por servicio y tipo de vehículo; los anteriores quedan inactivos.
        Index(
            "uq_servicio_precio_vigente",
            "servicio_id",
            "tipo_vehiculo_id",
            unique=True,
            postgresql_where=text("activo"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    servicio_id: Mapped[int] = mapped_column(ForeignKey("servicio.id"), index=True)
    tipo_vehiculo_id: Mapped[int] = mapped_column(ForeignKey("tipo_vehiculo.id"), index=True)
    # En pesos colombianos, sin decimales.
    precio: Mapped[int] = mapped_column(Integer)
    # Duración estimada en minutos: con ella se calcula la espera de la fila.
    duracion_min: Mapped[int] = mapped_column(SmallInteger)
    activo: Mapped[bool] = mapped_column(default=True, server_default=text("true"))


class Configuracion(Base):
    """Parámetros del negocio que el administrador cambia desde el dashboard.

    Ejemplos: horario, tiempo de gracia, teléfono que da el bot. El valor se guarda en
    JSON para aceptar números, textos, listas o verdadero/falso.
    """

    __tablename__ = "configuracion"

    clave: Mapped[str] = mapped_column(String(60), primary_key=True)
    valor: Mapped[Any] = mapped_column(JSONB)
    descripcion: Mapped[str] = mapped_column(Text)
    actualizado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
