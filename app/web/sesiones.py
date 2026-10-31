"""La sesión del dashboard: quién entró y hasta cuándo.

Se guarda en una cookie firmada con CLAVE_SECRETA (HMAC-SHA256). El navegador la
guarda, pero no la puede cambiar: si alguien toca un solo carácter, la firma deja de
coincidir y el sistema la ignora. Así no hace falta una tabla de sesiones.

- Vence tras SESION_HORAS sin usar el dashboard; cada vez que se usa, se renueva.
- Si al administrador le cambian la contraseña o lo desactivan, sus sesiones abiertas
  dejan de servir: la cookie lleva una huella de la contraseña cifrada.
"""

import base64
import hashlib
import hmac
import json
from collections import deque
from dataclasses import dataclass

NOMBRE_COOKIE = "auto_ibaguer_sesion"


def _a_texto(datos: bytes) -> str:
    return base64.urlsafe_b64encode(datos).rstrip(b"=").decode()


def _desde_texto(texto: str) -> bytes:
    return base64.urlsafe_b64decode(texto + "=" * (-len(texto) % 4))


def _firmar(contenido: str, clave: str) -> str:
    return _a_texto(hmac.new(clave.encode(), contenido.encode(), hashlib.sha256).digest())


def huella_de_la_clave(clave_cifrada: str) -> str:
    """Cambia si cambia la contraseña: así las sesiones viejas dejan de servir."""
    return hashlib.sha256(clave_cifrada.encode()).hexdigest()[:16]


@dataclass(frozen=True)
class DatosDeSesion:
    administrador_id: int
    huella: str
    vence: int  # segundos desde 1970 (hora UTC)


def crear_valor(datos: DatosDeSesion, clave: str) -> str:
    """El valor de la cookie: los datos y su firma, separados por un punto."""
    contenido = _a_texto(
        json.dumps(
            {"a": datos.administrador_id, "h": datos.huella, "v": datos.vence},
            separators=(",", ":"),
        ).encode()
    )
    return f"{contenido}.{_firmar(contenido, clave)}"


def leer_valor(valor: str | None, clave: str, ahora: float) -> DatosDeSesion | None:
    """Los datos de la cookie, solo si la firma es auténtica y no ha vencido."""
    if not valor or "." not in valor:
        return None
    contenido, firma = valor.rsplit(".", 1)
    if not hmac.compare_digest(firma, _firmar(contenido, clave)):
        return None
    try:
        datos = json.loads(_desde_texto(contenido))
        sesion = DatosDeSesion(int(datos["a"]), str(datos["h"]), int(datos["v"]))
    except (ValueError, KeyError, TypeError):
        return None
    if sesion.vence <= ahora:
        return None
    return sesion


class LimiteDeIntentos:
    """Frena a quien intenta adivinar contraseñas.

    Después de `maximo` intentos fallidos con el mismo usuario dentro de la ventana,
    ese usuario queda bloqueado hasta que el intento más viejo salga de la ventana.
    Vive en la memoria del servidor: si el servidor se reinicia, vuelve a cero. Para un
    lavadero alcanza; la contraseña larga y Argon2 hacen el resto.
    """

    def __init__(self, maximo: int = 5, ventana_segundos: int = 15 * 60) -> None:
        self.maximo = maximo
        self.ventana_segundos = ventana_segundos
        self._fallos: dict[str, deque[float]] = {}

    def _recientes(self, usuario: str, ahora: float) -> int:
        """Cuántos fallos de ese usuario siguen dentro de la ventana. Olvida los viejos."""
        fallos = self._fallos.get(usuario)
        if fallos is None:
            return 0
        while fallos and fallos[0] <= ahora - self.ventana_segundos:
            fallos.popleft()
        if not fallos:
            del self._fallos[usuario]  # así la memoria no crece con usuarios inventados
        return len(fallos)

    def bloqueado(self, usuario: str, ahora: float) -> bool:
        return self._recientes(usuario, ahora) >= self.maximo

    def registrar_fallo(self, usuario: str, ahora: float) -> None:
        self._recientes(usuario, ahora)
        self._fallos.setdefault(usuario, deque()).append(ahora)

    def registrar_exito(self, usuario: str) -> None:
        self._fallos.pop(usuario, None)
