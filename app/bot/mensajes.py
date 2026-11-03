"""Lo que entra y sale del bot, sin saber nada de WhatsApp ni de la base de datos.

Un `Mensaje` del bot es texto con hasta 3 botones, o texto con una lista desplegable
(hasta 10 opciones), que es lo que WhatsApp deja mandar. Los límites de WhatsApp se
revisan aquí, al armar cada mensaje: si un texto queda muy largo, falla en las pruebas y
no cuando un cliente de verdad está esperando la respuesta.

Cuando el cliente toca un botón o una opción de la lista, WhatsApp devuelve su `id`. Los
ids llevan un prefijo con el paso de la conversación al que pertenecen ("vehiculo.3",
"confirmar.si"), así el bot reconoce un botón viejo que ya no aplica.
"""

from dataclasses import dataclass, field
from typing import Any

# Límites de WhatsApp para los mensajes con botones o lista.
MAX_BOTONES = 3
MAX_TITULO_BOTON = 20
MAX_OPCIONES_LISTA = 10
MAX_TITULO_OPCION = 24
MAX_DESCRIPCION_OPCION = 72
MAX_TEXTO_INTERACTIVO = 1024
MAX_TEXTO = 4096
MAX_ID = 200


@dataclass(frozen=True)
class Opcion:
    id: str
    titulo: str
    descripcion: str | None = None  # solo en las listas


@dataclass(frozen=True)
class Lista:
    boton: str  # el texto del botón que abre la lista, por ejemplo "Ver servicios"
    opciones: tuple[Opcion, ...]


@dataclass(frozen=True)
class Mensaje:
    texto: str
    botones: tuple[Opcion, ...] = ()
    lista: Lista | None = None

    def __post_init__(self) -> None:
        interactivo = bool(self.botones) or self.lista is not None
        _revisar(self.texto.strip() != "", "un mensaje sin texto")
        maximo = MAX_TEXTO_INTERACTIVO if interactivo else MAX_TEXTO
        _revisar(len(self.texto) <= maximo, f"un texto de más de {maximo} caracteres")
        _revisar(not (self.botones and self.lista), "botones y lista en el mismo mensaje")
        _revisar(len(self.botones) <= MAX_BOTONES, f"más de {MAX_BOTONES} botones")
        for boton in self.botones:
            _revisar(len(boton.titulo) <= MAX_TITULO_BOTON, f"el botón {boton.titulo!r}")
            _revisar(boton.descripcion is None, "un botón con descripción")
        if self.lista is not None:
            opciones = self.lista.opciones
            _revisar(len(self.lista.boton) <= MAX_TITULO_BOTON, "el botón de la lista")
            _revisar(1 <= len(opciones) <= MAX_OPCIONES_LISTA, "una lista de 0 o más de 10")
            for opcion in opciones:
                _revisar(len(opcion.titulo) <= MAX_TITULO_OPCION, f"la opción {opcion.titulo!r}")
                _revisar(
                    len(opcion.descripcion or "") <= MAX_DESCRIPCION_OPCION,
                    f"la descripción de {opcion.titulo!r}",
                )
        ids = [opcion.id for opcion in self.opciones]
        _revisar(len(ids) == len(set(ids)), "dos opciones con el mismo id")
        _revisar(all(0 < len(i) <= MAX_ID for i in ids), "un id vacío o muy largo")

    @property
    def opciones(self) -> tuple[Opcion, ...]:
        """Los botones o las opciones de la lista: lo que el cliente puede tocar."""
        return self.lista.opciones if self.lista is not None else self.botones

    # Las respuestas del bot esperan en la bandeja de salida como JSON (mensaje_saliente).

    def a_datos(self) -> dict[str, Any]:
        datos: dict[str, Any] = {"texto": self.texto}
        if self.botones:
            datos["botones"] = [[boton.id, boton.titulo] for boton in self.botones]
        if self.lista is not None:
            datos["lista"] = {
                "boton": self.lista.boton,
                "opciones": [[o.id, o.titulo, o.descripcion] for o in self.lista.opciones],
            }
        return datos

    @classmethod
    def desde_datos(cls, datos: dict[str, Any]) -> "Mensaje":
        lista = datos.get("lista")
        return cls(
            texto=datos["texto"],
            botones=tuple(Opcion(id_, titulo) for id_, titulo in datos.get("botones", [])),
            lista=Lista(
                lista["boton"],
                tuple(
                    Opcion(id_, titulo, descripcion)
                    for id_, titulo, descripcion in lista["opciones"]
                ),
            )
            if lista
            else None,
        )


def _revisar(cumple: bool, que: str) -> None:
    if not cumple:
        raise ValueError(f"WhatsApp no acepta {que}.")


def recortar(texto: str, largo: int) -> str:
    """Corta un texto para que quepa, con "…" al final si se cortó."""
    return texto if len(texto) <= largo else texto[: largo - 1].rstrip() + "…"


@dataclass(frozen=True)
class Entrada:
    """Lo que escribió o tocó el cliente."""

    telefono: str  # como lo trae WhatsApp: 573001234567
    texto: str | None = None
    opcion: str | None = None  # el id del botón u opción de la lista que tocó
    # Lo que mandó y no es texto ni botón (audio, foto, ubicación...): el bot no lo entiende.
    otro: bool = field(default=False)
