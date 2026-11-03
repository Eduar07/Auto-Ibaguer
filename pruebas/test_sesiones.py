"""La cookie de sesión y el freno a quien intenta adivinar contraseñas (sin base de datos)."""

import pytest

from app.web.sesiones import DatosDeSesion, LimiteDeIntentos, crear_valor, leer_valor

CLAVE = "clave-secreta-de-prueba"
AHORA = 1_800_000_000.0
DATOS = DatosDeSesion(administrador_id=7, huella="abc123", vence=int(AHORA) + 3600)


def test_la_cookie_trae_los_mismos_datos():
    assert leer_valor(crear_valor(DATOS, CLAVE), CLAVE, AHORA) == DATOS


def test_firmada_con_otra_clave_no_sirve():
    assert leer_valor(crear_valor(DATOS, "otra-clave"), CLAVE, AHORA) is None


def test_cambiar_los_datos_rompe_la_firma():
    valor = crear_valor(DATOS, CLAVE)
    otro_administrador = crear_valor(
        DatosDeSesion(administrador_id=1, huella=DATOS.huella, vence=DATOS.vence), CLAVE
    )
    datos_ajenos = otro_administrador.split(".")[0]
    firma_propia = valor.split(".")[1]

    assert leer_valor(f"{datos_ajenos}.{firma_propia}", CLAVE, AHORA) is None


def test_vencida_no_sirve():
    assert leer_valor(crear_valor(DATOS, CLAVE), CLAVE, DATOS.vence) is None


@pytest.mark.parametrize("valor", [None, "", "sin-punto", "a.b", "....", "eyJ.firma"])
def test_una_cookie_cualquiera_no_sirve(valor):
    assert leer_valor(valor, CLAVE, AHORA) is None


def test_cinco_fallos_bloquean_y_a_los_15_minutos_se_desbloquea():
    limite = LimiteDeIntentos(maximo=5, ventana_segundos=900)
    for segundo in range(5):
        assert not limite.bloqueado("eduar", AHORA + segundo)
        limite.registrar_fallo("eduar", AHORA + segundo)

    assert limite.bloqueado("eduar", AHORA + 10)
    assert not limite.bloqueado("otro", AHORA + 10)  # los demás usuarios no se afectan
    assert not limite.bloqueado("eduar", AHORA + 905)  # el primer fallo ya salió de la ventana


def test_entrar_bien_borra_los_fallos():
    limite = LimiteDeIntentos(maximo=2)
    limite.registrar_fallo("eduar", AHORA)
    limite.registrar_exito("eduar")
    limite.registrar_fallo("eduar", AHORA + 1)

    assert not limite.bloqueado("eduar", AHORA + 2)


def test_preguntar_por_usuarios_inventados_no_gasta_memoria():
    limite = LimiteDeIntentos()
    for numero in range(100):
        limite.bloqueado(f"inventado-{numero}", AHORA)
    limite.registrar_fallo("eduar", AHORA)

    assert limite.bloqueado("eduar", AHORA + 1) is False
    assert len(limite._fallos) == 1
    assert not limite.bloqueado("eduar", AHORA + 2000)
    assert limite._fallos == {}
