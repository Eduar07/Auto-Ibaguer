"""Tablas de clientes, vehículos y consultas."""

from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Integer,
    String,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, lista_cerrada


class Cliente(Base):
    """Quien escribe por WhatsApp o llega al local.

    El teléfono lo identifica (WhatsApp ya lo trae). Es opcional porque hay clientes
    que llegan al local sin celular.
    """

    __tablename__ = "cliente"
    __table_args__ = (
        # Formato internacional sin "+": 573001234567
        CheckConstraint("telefono ~ '^[0-9]{8,15}$'", name="telefono_solo_digitos"),
        # Ley 1581: si aceptó el tratamiento de datos, queda la fecha en que lo hizo.
        CheckConstraint(
            "NOT acepta_datos OR fecha_aceptacion IS NOT NULL", name="aceptacion_con_fecha"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    telefono: Mapped[str | None] = mapped_column(String(15), unique=True)
    nombre: Mapped[str] = mapped_column(String(80))
    acepta_datos: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    fecha_aceptacion: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Vehiculo(Base):
    """Cada carro o moto atendido. Sin placa = "placa pendiente"."""

    __tablename__ = "vehiculo"
    __table_args__ = (
        # Placa guardada limpia: mayúsculas y sin espacios ni guiones (ABC123, ABC12D).
        # El formato exacto según el tipo de vehículo lo valida la aplicación.
        CheckConstraint("placa ~ '^[A-Z0-9]{5,7}$'", name="placa_formato"),
    )

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    cliente_id: Mapped[int] = mapped_column(ForeignKey("cliente.id"), index=True)
    tipo_vehiculo_id: Mapped[int] = mapped_column(ForeignKey("tipo_vehiculo.id"))
    # Única: dos vehículos nunca comparten placa. Vacía se permite varias veces.
    placa: Mapped[str | None] = mapped_column(String(7), unique=True)
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TipoConsulta(StrEnum):
    PRECIOS = "precios"
    FILA = "fila"


class Consulta(Base):
    """Una consulta de precios o de la fila que no terminó en turno.

    Sirve para medir cuántas personas preguntan y no vienen. Si la persona no aceptó
    el aviso de datos, se guarda sin cliente: solo cuenta.
    """

    __tablename__ = "consulta"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    cliente_id: Mapped[int | None] = mapped_column(ForeignKey("cliente.id"), index=True)
    tipo: Mapped[TipoConsulta] = mapped_column(lista_cerrada(TipoConsulta, "tipo_consulta"))
    tipo_vehiculo_id: Mapped[int | None] = mapped_column(ForeignKey("tipo_vehiculo.id"))
    fecha_hora: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
