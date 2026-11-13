"""Un servidor de correo de mentira para las pruebas: guarda lo que le mandan."""

import smtplib


class ServidorFalso:
    """Hace de servidor SMTP: guarda lo que le mandan, sin salir a internet."""

    def __init__(self, enviados: list, fallar: Exception | None = None, cifrado: bool = True):
        self.enviados = enviados
        self.fallar = fallar
        self.cifrado = cifrado
        self.cerrado = False

    def __call__(self, servidor: str, puerto: int, **opciones):
        self.conexion = {"servidor": servidor, "puerto": puerto, **opciones, "tls": False}
        if isinstance(self.fallar, OSError):
            raise self.fallar
        return self

    def __enter__(self):
        return self

    def __exit__(self, *error):
        self.cerrado = True

    def ehlo(self):
        pass

    def has_extn(self, nombre: str) -> bool:
        return self.cifrado and nombre == "starttls"

    def starttls(self, **opciones):
        self.conexion["tls"] = True

    def close(self):
        self.cerrado = True

    def login(self, usuario: str, clave: str):
        if isinstance(self.fallar, smtplib.SMTPAuthenticationError):
            raise self.fallar
        self.conexion["usuario"] = usuario

    def send_message(self, mensaje):
        self.enviados.append(mensaje)
