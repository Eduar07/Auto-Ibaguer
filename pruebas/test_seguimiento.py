"""El seguimiento de "lo pienso" (pieza 4 del checklist "¿Tu bot vende o solo contesta?").

Lo que tiene que cumplirse siempre:
- solo se le escribe a quien dijo que sí ("Sí, escríbeme" o un sí escrito);
- siempre dentro de las 24 horas en que WhatsApp deja escribir sin plantilla: nunca un
  mensaje que Meta rechace con el error 131047;
- a una hora en que el lavadero está abierto y le alcanza para venir;
- si vuelve a escribir o pide turno antes, no se le escribe: ya está aquí;
- el celular no se guarda más de lo necesario: el seguimiento se borra al mandarlo.
"""

from datetime import datetime, time, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.bot import seguimiento
from app.bot.interprete import Intencion, Interpretacion
from app.bot.mensajes import Mensaje
from app.bot.tablas import Seguimiento
from app.catalogo.parametros import leer_parametros
from app.integraciones.tablas import MensajeEntrante, MensajeSaliente
from pruebas.fabricas import BOGOTA, LUNES_8AM, cliente_con_vehiculo, por_whatsapp
from pruebas.test_bot import CELULAR, Chat, paso, poner
from pruebas.test_gemini import chat

LUNES = LUNES_8AM.date()


def a_las(dia: int, hora: int, minuto: int = 0) -> datetime:
    """dia 0 = lunes 26 de octubre de 2026."""
    return datetime.combine(LUNES + timedelta(days=dia), time(hora, minuto), BOGOTA)


def pendiente(sesion: Session) -> Seguimiento | None:
    consulta = select(Seguimiento).where(Seguimiento.telefono == CELULAR)
    return sesion.scalars(consulta.execution_options(populate_existing=True)).first()


# --- Cuándo escribirle -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("dice", "ahora", "momento", "cuando"),
    [
        ("de mañana: mañana temprano", a_las(0, 8), a_las(1, 7), "mañana temprano"),
        ("de noche: mañana temprano", a_las(0, 19), a_las(1, 7), "mañana temprano"),
        # Mañana a las 7:00 ya serían 25 horas: se le escribe antes de que se cierre la
        # ventana, cuando ya abrieron.
        ("muy temprano", a_las(0, 6), a_las(1, 5, 30), "mañana temprano"),
        # La ventana se cierra antes de que abran mañana: más tarde hoy.
        ("apenas abren", a_las(0, 5, 40), a_las(0, 9, 40), "más tarde"),
    ],
)
def test_cuando_escribirle_siempre_dentro_de_las_24_horas(
    sesion: Session, dice: str, ahora: datetime, momento: datetime, cuando: str
):
    elegido = seguimiento.cuando(ahora, leer_parametros(sesion))

    assert (elegido.momento, elegido.dicho) == (momento, cuando), dice
    assert elegido.momento <= ahora + timedelta(hours=23, minutes=30)


def test_si_manana_no_abren_y_hoy_ya_no_alcanza_no_se_ofrece(sesion: Session):
    poner(sesion, "dias_atencion", [1, 2, 3, 4, 5, 6])  # los domingos no abre
    parametros = leer_parametros(sesion)
    sabado = a_las(5, 17)

    assert seguimiento.cuando(a_las(5, 8), parametros).dicho == "más tarde"
    assert seguimiento.cuando(sabado, parametros) is None


# --- En la conversación ----------------------------------------------------------------------


def lo_piensa_despues_de_ver_los_precios(sesion: Session) -> Chat:
    cliente = Chat(sesion).escribe("hola").toca("Servicios y precios").toca("Moto")
    return cliente.escribe("listo, lo pienso")


def test_lo_pienso_ofrece_escribirle_y_solo_con_su_si(sesion: Session):
    cliente = lo_piensa_despues_de_ver_los_precios(sesion)

    assert cliente.texto == (
        "¡Listo, sin afán! ¿Quieres que mañana temprano te escriba para contarte cómo va la fila?"
    )
    assert cliente.opciones == ["Sí, escríbeme", "No, gracias"]
    assert pendiente(sesion) is None  # todavía no ha dicho que sí

    cliente.toca("Sí, escríbeme")

    assert cliente.texto == "¡Hecho! Mañana temprano te escribo. Si te animas antes, aquí estoy."
    guardado = pendiente(sesion)
    assert guardado.enviar_en == a_las(1, 7)
    assert guardado.tipo_vehiculo_id is not None  # estaba mirando los precios de moto
    assert paso(sesion) == "menu"


def test_un_si_escrito_tambien_vale_y_un_no_no_guarda_nada(sesion: Session):
    lo_piensa_despues_de_ver_los_precios(sesion).escribe("dale")
    assert pendiente(sesion) is not None

    otro = Chat(sesion, telefono="573009998877").escribe("hola").escribe("lo voy a pensar")
    otro.toca("No, gracias")
    assert otro.texto == "Listo, sin problema. Cuando quieras, aquí estamos."
    assert sesion.scalars(select(Seguimiento)).all() == [pendiente(sesion)]


def test_si_vuelve_a_escribir_ya_no_se_le_escribe(sesion: Session):
    cliente = lo_piensa_despues_de_ver_los_precios(sesion).toca("Sí, escríbeme")

    cliente.pasan(60).escribe("hola otra vez")

    assert pendiente(sesion) is None


def test_si_ya_tiene_turno_no_se_le_ofrece(sesion: Session):
    vehiculo = cliente_con_vehiculo(sesion, telefono=CELULAR, nombre="Ana Ruiz")
    por_whatsapp(sesion, vehiculo=vehiculo)

    cliente = Chat(sesion).escribe("lo pienso")

    assert cliente.texto == "¡Listo, sin afán! Aquí estamos cuando te animes."
    assert cliente.opciones == []


def test_con_gemini_mejor_lo_pienso_en_el_resumen_tambien_lo_ofrece(sesion: Session):
    cliente_con_vehiculo(sesion, telefono=CELULAR, nombre="Ana Ruiz", placa="ABC123")
    cliente = chat(sesion)
    cliente.escribe("hola").toca("Pedir turno").toca("Básico")  # el único vehículo que tiene
    assert paso(sesion) == "confirmar"
    cliente.gemini.entendidos.append(Interpretacion(Intencion.LO_PIENSO))

    cliente.escribe("mmm mejor déjame lo pienso")

    assert cliente.opciones == ["Sí, escríbeme", "No, gracias"]


# --- El mensaje, a la hora acordada ----------------------------------------------------------


def le_escribio(sesion: Session, cuando: datetime) -> None:
    """Lo que dejó la bandeja de entrada: la ventana de 24 horas cuenta desde ahí."""
    sesion.add(
        MensajeEntrante(
            id_meta=f"wamid.{cuando.isoformat()}",
            telefono=CELULAR,
            tipo="interactive",
            contenido={"opcion": "seguimiento.si"},
            recibido_en=cuando,
            procesado_en=cuando,
        )
    )
    sesion.flush()


def salientes(sesion: Session) -> list[Mensaje]:
    filas = sesion.scalars(select(MensajeSaliente).order_by(MensajeSaliente.id))
    return [Mensaje.desde_datos(fila.contenido) for fila in filas]


@pytest.fixture
def acepto(sesion: Session) -> Chat:
    cliente = lo_piensa_despues_de_ver_los_precios(sesion).toca("Sí, escríbeme")
    le_escribio(sesion, LUNES_8AM)
    return cliente


def test_a_la_hora_acordada_le_llega_como_va_la_fila_y_se_borra(sesion: Session, acepto):
    assert seguimiento.enviar_los_que_tocan(sesion, ahora=a_las(1, 6, 59)) == 0  # aún no

    assert seguimiento.enviar_los_que_tocan(sesion, ahora=a_las(1, 7)) == 1

    [mensaje] = salientes(sesion)
    assert mensaje.texto.startswith("¡Hola! Te escribo como quedamos. Así está la fila ahora:")
    assert "*Motos:*" in mensaje.texto and "*Carros:*" not in mensaje.texto  # la que miraba
    assert mensaje.texto.endswith("¿Te aparto un turno?")
    assert [b.titulo for b in mensaje.botones] == ["Pedir turno", "Servicios y precios"]
    assert pendiente(sesion) is None  # el celular no se guarda más de lo necesario


def test_al_cliente_lo_saluda_por_su_nombre(sesion: Session):
    cliente_con_vehiculo(sesion, telefono=CELULAR, nombre="Ana Ruiz")
    Chat(sesion).escribe("lo pienso").toca("Sí, escríbeme")
    le_escribio(sesion, LUNES_8AM)

    seguimiento.enviar_los_que_tocan(sesion, ahora=a_las(1, 7))

    assert salientes(sesion)[0].texto.startswith("¡Hola, Ana! Te escribo como quedamos.")


def test_si_pidio_turno_mientras_tanto_no_se_le_escribe(sesion: Session):
    vehiculo = cliente_con_vehiculo(sesion, telefono=CELULAR, nombre="Ana Ruiz")
    Chat(sesion).escribe("lo pienso").toca("Sí, escríbeme")
    le_escribio(sesion, LUNES_8AM)
    por_whatsapp(sesion, vehiculo=vehiculo, ahora=a_las(1, 6))  # pidió turno en el local

    assert seguimiento.enviar_los_que_tocan(sesion, ahora=a_las(1, 7)) == 0
    assert salientes(sesion) == [] and pendiente(sesion) is None


def test_fuera_de_la_ventana_de_24_horas_nunca_se_manda(sesion: Session, acepto):
    pendiente(sesion).enviar_en = a_las(1, 8, 30)  # por si algo lo corrió: 24,5 horas

    assert seguimiento.enviar_los_que_tocan(sesion, ahora=a_las(1, 8, 30)) == 0
    assert salientes(sesion) == []  # nada que Meta rechace con el 131047


def test_con_el_bot_apagado_no_se_manda(sesion: Session, acepto):
    poner(sesion, "bot_activo", False)

    assert seguimiento.enviar_los_que_tocan(sesion, ahora=a_las(1, 7)) == 0


def test_el_reloj_lo_revisa_en_cada_vuelta():
    from app.reloj import tareas

    assert tareas.SEGUIMIENTOS in tareas.del_servidor(None)
