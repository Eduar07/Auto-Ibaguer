"""La conversación del bot, como guion: lo que el cliente escribe o toca y lo que el bot
contesta y hace en la fila. Es la simulación que acordamos, repetida en cada cambio.

Un `Chat` solo toca los botones que el bot de verdad le ofreció en su última respuesta.
La hora queda fija en el lunes a las 8:00 a.m. (abierto), salvo donde se diga otra cosa.
"""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.bot import conversacion
from app.bot.avisos import mensaje_del_aviso
from app.bot.mensajes import Entrada, Lista, Mensaje, Opcion
from app.bot.tablas import SesionBot
from app.catalogo import configuracion
from app.catalogo.parametros import leer_parametros
from app.catalogo.tablas import Configuracion
from app.clientes import consultas as consultas_de_clientes
from app.clientes.tablas import Cliente, Consulta, TipoConsulta, Vehiculo
from app.turnos import casos_de_uso as casos
from app.turnos.estados import Actor, EstadoTurno, OrigenTurno, TipoAviso
from app.turnos.tablas import Turno
from pruebas.fabricas import (
    LUNES_8AM,
    cliente_con_vehiculo,
    en_el_local,
    estado,
    minutos,
    ocupar_puestos,
    por_whatsapp,
    servicio,
    tipo_vehiculo,
)

E = EstadoTurno
CELULAR = "573001234567"


class Chat:
    """Un cliente que le escribe al bot, como desde WhatsApp."""

    def __init__(self, sesion: Session, telefono: str = CELULAR, ahora: datetime = LUNES_8AM):
        self.sesion = sesion
        self.telefono = telefono
        self.ahora = ahora
        self.ultimas: list[Mensaje] = []

    def escribe(self, texto: str) -> "Chat":
        return self._enviar(Entrada(self.telefono, texto=texto))

    def toca(self, titulo: str) -> "Chat":
        ids = [o.id for m in self.ultimas for o in m.opciones if o.titulo == titulo]
        assert ids, f"El bot no ofreció {titulo!r}; ofreció {self.opciones}"
        return self._enviar(Entrada(self.telefono, opcion=ids[-1]))

    def toca_el_id(self, id_del_boton: str) -> "Chat":
        return self._enviar(Entrada(self.telefono, opcion=id_del_boton))

    def manda_un_audio(self) -> "Chat":
        return self._enviar(Entrada(self.telefono, otro=True))

    def pasan(self, cantidad: int) -> "Chat":
        self.ahora += minutos(cantidad)
        return self

    def _enviar(self, entrada: Entrada) -> "Chat":
        self.ultimas = conversacion.atender(self.sesion, entrada, ahora=self.ahora)
        return self

    @property
    def texto(self) -> str:
        return "\n".join(mensaje.texto for mensaje in self.ultimas)

    @property
    def opciones(self) -> list[str]:
        return [opcion.titulo for mensaje in self.ultimas for opcion in mensaje.opciones]

    def ids(self) -> list[str]:
        return [opcion.id for mensaje in self.ultimas for opcion in mensaje.opciones]


def turnos_de(sesion: Session, telefono: str = CELULAR) -> list[Turno]:
    consulta = select(Turno).join(Cliente).where(Cliente.telefono == telefono).order_by(Turno.id)
    return list(sesion.scalars(consulta.execution_options(populate_existing=True)))


def paso(sesion: Session, telefono: str = CELULAR) -> str:
    consulta = select(SesionBot.paso_actual).where(SesionBot.telefono == telefono)
    return sesion.scalar(consulta.execution_options(populate_existing=True))


def poner(sesion: Session, clave: str, valor) -> None:
    sesion.scalars(select(Configuracion).filter_by(clave=clave)).one().valor = valor
    sesion.flush()


MENU = ["Pedir turno", "Ver la fila", "Servicios y precios"]


# --- Saludo y menú ---------------------------------------------------------------------------


def test_el_primer_mensaje_saluda_con_el_horario_y_tres_opciones(sesion: Session):
    chat = Chat(sesion).escribe("hola")

    assert chat.texto == (
        "¡Hola! Bienvenido a Auto Ibaguer. Por WhatsApp damos turnos todos los días de "
        "5:30 a.m. a 7:30 p.m. ¿En qué te ayudamos?"
    )
    assert chat.opciones == MENU


def test_a_un_cliente_conocido_lo_saluda_por_su_nombre(sesion: Session):
    cliente_con_vehiculo(sesion, telefono=CELULAR, nombre="Ana Ruiz")

    assert Chat(sesion).escribe("Buenas").texto.startswith("¡Hola, Ana! Qué bueno saludarte")


def test_lo_que_no_entiende_muestra_el_menu(sesion: Session):
    chat = Chat(sesion).escribe("hola")

    chat.escribe("quiero saber si atienden motos grandes")
    assert "Por ahora entiendo los botones y las palabras *mi turno* y *cancelar*" in chat.texto
    assert chat.opciones == MENU

    chat.manda_un_audio()
    assert "solo entiendo mensajes escritos y los botones" in chat.texto
    assert chat.opciones == MENU


def test_se_despide_sin_volver_a_mostrar_el_menu(sesion: Session):
    chat = Chat(sesion).escribe("hola").escribe("Muchas gracias!")

    assert chat.texto == "¡Con gusto! Que tengas un buen día."
    assert chat.opciones == []


# --- Pedir turno ---------------------------------------------------------------------------


def test_un_turno_sin_placa_ni_servicio_de_punta_a_punta(sesion: Session):
    chat = Chat(sesion).escribe("hola").toca("Pedir turno")
    assert "¿Aceptas?" in chat.texto and chat.opciones == ["Acepto", "No acepto"]

    chat.toca("Acepto")
    assert chat.texto == "¿A nombre de quién hacemos el turno?"
    chat.escribe("  laura   GÓMEZ ")
    assert chat.opciones == ["Automóvil", "Camioneta", "Moto"]
    chat.toca("Automóvil")
    assert "¿Cuál es la placa de tu automóvil? La pedimos por seguridad" in chat.texto
    chat.toca("Ahora no")
    [lista] = [m.lista for m in chat.ultimas]
    assert lista.boton == "Ver servicios"
    assert [o.titulo for o in lista.opciones] == [
        "Básico",
        "Encerado",
        "General",
        "Premium",
        "Empelotado",
        "Lo decido en el local",
    ]
    assert lista.opciones[0].descripcion.startswith("$40.000 · Lavado exterior")
    chat.toca("Lo decido en el local")
    assert chat.texto == (
        "Revisa tu turno:\n\n*Nombre:* Laura Gómez\n*Vehículo:* Automóvil, placa pendiente\n"
        "*Servicio:* lo decides en el local\n*Fila de carros:* hay puesto libre, puedes venir ya"
        "\n\n¿Lo reservamos?"
    )
    assert turnos_de(sesion) == []  # nada se reserva antes de confirmar

    chat.toca("Confirmar turno")

    assert "¡Listo, Laura! Tu turno es *C-01*." in chat.texto
    assert "Hay un puesto libre: ven ya. Tienes hasta las 8:15 a.m. para llegar." in chat.texto
    assert "Cuando te llamemos tienes 15 minutos para llegar." in chat.texto
    assert chat.opciones == ["En camino", "Ya estoy aquí"]
    [turno] = turnos_de(sesion)
    assert (turno.origen, turno.estado, turno.servicio_id) == (
        OrigenTurno.WHATSAPP,
        E.LLAMADO,
        None,
    )
    vehiculo = sesion.get(Vehiculo, turno.vehiculo_id)
    assert vehiculo.placa is None  # placa pendiente
    cliente = sesion.get(Cliente, turno.cliente_id)
    assert (cliente.nombre, cliente.acepta_datos) == ("Laura Gómez", True)
    assert cliente.fecha_aceptacion == LUNES_8AM
    assert paso(sesion) == "menu"


def test_con_la_fila_llena_dice_cuantos_tiene_adelante(sesion: Session):
    ocupar_puestos(sesion, "C1", "C2")
    for _ in range(3):
        por_whatsapp(sesion)
    chat = Chat(sesion).toca_el_id("menu.pedir").toca("Acepto").escribe("Pedro")

    chat.toca("Camioneta").escribe("klm-482").toca("General").toca("Confirmar turno")

    assert "Tu turno es *C-06*." in chat.texto
    assert "Tienes 3 turnos adelante; entras en aprox. 2 h." in chat.texto
    assert "Te avisamos por aquí cuando falten 2 turnos" in chat.texto
    [turno] = turnos_de(sesion)
    assert turno.estado == E.EN_FILA
    assert turno.servicio_id == servicio(sesion, "General").id
    assert sesion.get(Vehiculo, turno.vehiculo_id).placa == "KLM482"


def test_si_queda_cerca_le_pide_salir_ya(sesion: Session):
    ocupar_puestos(sesion, "C1", "C2")
    por_whatsapp(sesion)
    chat = Chat(sesion).toca_el_id("menu.pedir").toca("Acepto").escribe("Pedro")

    chat.toca("Automóvil").toca("Ahora no").toca("Básico").toca("Confirmar turno")

    assert "Ya casi te toca (tienes 1 turno adelante): sal hacia el lavadero." in chat.texto


def test_sin_aceptar_el_aviso_de_datos_no_se_guarda_nada(sesion: Session):
    chat = Chat(sesion).escribe("hola").toca("Pedir turno").toca("No acepto")

    assert "Sin tu autorización no guardamos tus datos" in chat.texto
    assert chat.opciones == ["Ver la fila", "Servicios y precios"]
    assert sesion.scalar(select(func.count()).select_from(Cliente)) == 0


def test_la_placa_se_revisa_y_no_puede_ser_de_otro_cliente(sesion: Session):
    cliente_con_vehiculo(sesion, placa="ABC123")
    chat = Chat(sesion).toca_el_id("menu.pedir").toca("Acepto").escribe("Pedro").toca("Moto")

    chat.escribe("ABC123")
    assert chat.texto.startswith("La placa 'ABC123' no tiene el formato de placa de moto.")
    assert "(por ejemplo, ABC12D)" in chat.texto and chat.opciones == ["Ahora no"]

    chat.toca_el_id("vehiculo.otro")  # un botón de otro paso ya no sirve
    assert chat.texto.startswith("Ese botón ya no está vigente.")

    chat = Chat(sesion).toca_el_id("menu.pedir").toca("Acepto").escribe("Pedro")
    chat.toca("Automóvil").escribe("abc 123")
    assert chat.texto.startswith("La placa ABC123 ya está registrada con otro cliente.")
    chat.escribe("xyz987")
    assert chat.texto.startswith("¿Qué servicio quieres para tu automóvil?")


def test_a_un_cliente_conocido_no_le_repite_nombre_ni_vehiculo(sesion: Session):
    cliente_con_vehiculo(sesion, telefono=CELULAR, nombre="Ana Ruiz", placa="ABC123")

    chat = Chat(sesion).escribe("hola").toca("Pedir turno")

    assert chat.texto.startswith("¿Qué servicio quieres para tu automóvil?")
    chat.toca("Encerado")
    assert "*Nombre:* Ana Ruiz\n*Vehículo:* Automóvil ABC123\n*Servicio:* Encerado · $60.000" in (
        chat.texto
    )
    # Si trae otro vehículo, lo cambia en el resumen.
    chat.toca("Cambiar datos")
    assert chat.opciones == ["Automóvil ABC123", "Otro vehículo"]
    chat.toca("Otro vehículo").toca("Moto").escribe("qwe45r").toca("Básico").toca("Confirmar turno")
    [turno] = turnos_de(sesion)
    assert sesion.get(Vehiculo, turno.vehiculo_id).placa == "QWE45R"
    assert "Tu turno es *M-01*." in chat.texto  # la moto va en la fila de motos


def test_con_dos_vehiculos_pregunta_cual(sesion: Session):
    carro = cliente_con_vehiculo(sesion, telefono=CELULAR, placa="ABC123")
    cliente_id = carro.cliente_id
    sesion.add(Vehiculo(cliente_id=cliente_id, tipo_vehiculo_id=tipo_vehiculo(sesion, "Moto").id))
    sesion.flush()

    chat = Chat(sesion).toca_el_id("menu.pedir")

    assert chat.texto == "¿Para cuál vehículo es el turno?"
    assert chat.opciones == ["Automóvil ABC123", "Moto sin placa", "Otro vehículo"]
    chat.toca("Moto sin placa")
    assert chat.texto.startswith("¿Cuál es la placa de tu moto?")


def test_se_puede_escribir_la_opcion_en_vez_de_tocarla(sesion: Session):
    chat = Chat(sesion).escribe("pedir turno")
    assert "¿Aceptas?" in chat.texto

    chat.escribe("Sí").escribe("Ana").escribe("moto").escribe("ahora no")
    assert chat.texto.startswith("¿Qué servicio quieres para tu moto?")
    chat.escribe("básico").escribe("dale")

    assert "Tu turno es *M-01*." in chat.texto


def test_si_escribe_otra_cosa_le_repite_la_pregunta(sesion: Session):
    chat = Chat(sesion).toca_el_id("menu.pedir").toca("Acepto").escribe("Ana")

    chat.escribe("no sé, uno grande")

    assert chat.texto.startswith("Toca una de las opciones, por favor.\n\n¿Qué vehículo traes?")
    assert chat.opciones == ["Automóvil", "Camioneta", "Moto"]


def test_mejor_no_no_reserva_nada(sesion: Session):
    chat = Chat(sesion).toca_el_id("menu.pedir").toca("Acepto").escribe("Ana").toca("Moto")

    chat.toca("Ahora no").toca("Básico").toca("Mejor no")

    assert chat.texto == "Listo, no reservamos nada. Cuando quieras, aquí estamos."
    assert turnos_de(sesion) == []
    assert sesion.scalar(select(func.count()).select_from(Cliente)) == 0


def test_un_boton_viejo_no_pide_otro_turno(sesion: Session):
    chat = Chat(sesion).toca_el_id("menu.pedir").toca("Acepto").escribe("Ana").toca("Moto")
    chat.toca("Ahora no").toca("Básico").toca("Confirmar turno")

    chat.toca_el_id("confirmar.si")  # el mismo botón, tocado otra vez

    assert chat.texto.startswith("Ese botón ya no está vigente.")
    assert len(turnos_de(sesion)) == 1


# --- Cuando no se dan turnos ---------------------------------------------------------------


def test_fuera_de_horario_no_da_turno_pero_muestra_la_fila_y_los_precios(sesion: Session):
    noche = LUNES_8AM.replace(hour=20)
    chat = Chat(sesion, ahora=noche).escribe("hola").toca("Pedir turno")

    assert chat.texto == (
        "Por WhatsApp damos turnos todos los días de 5:30 a.m. a 7:30 p.m., así que ahora no "
        "podemos darte uno. Abrimos mañana a las 5:30 a.m. Mientras tanto puedes ver la fila o "
        "los precios."
    )
    chat.toca("Ver la fila")
    assert chat.texto.startswith("Así está la fila ahora:")
    madrugada = Chat(sesion, ahora=LUNES_8AM.replace(hour=4)).toca_el_id("menu.pedir")
    assert "Abrimos hoy a las 5:30 a.m." in madrugada.texto


def test_con_el_bot_apagado_no_da_turnos(sesion: Session):
    poner(sesion, "bot_activo", False)

    chat = Chat(sesion).escribe("hola").toca("Pedir turno")

    assert chat.texto == (
        "Hoy no estamos dando turnos por WhatsApp. Puedes venir directamente al local o llamar "
        "al 322 755 6850."
    )
    assert chat.opciones == ["Ver la fila", "Servicios y precios"]


def test_un_segundo_turno_en_la_misma_fila_muestra_el_que_tiene(sesion: Session):
    vehiculo = cliente_con_vehiculo(sesion, telefono=CELULAR, placa="ABC123")
    ocupar_puestos(sesion, "C1", "C2")
    primero = por_whatsapp(sesion, vehiculo=vehiculo)

    chat = Chat(sesion).toca_el_id("menu.pedir")
    assert chat.texto == (
        f"Ya tienes el turno *{primero.numero}* en la fila de carros: eres el siguiente. Por "
        "WhatsApp se puede tener un turno por fila. ¿Qué hacemos?"
    )
    chat.toca("Mantenerlo")
    assert chat.texto == "Perfecto, lo mantenemos. Te esperamos."
    assert estado(sesion, primero.turno.id) == E.EN_FILA

    chat.toca_el_id("menu.pedir").toca("Cancelarlo")
    assert chat.texto.startswith(f"Listo, cancelamos tu turno *{primero.numero}*.")
    assert estado(sesion, primero.turno.id) == E.CANCELADO


def test_puede_tener_un_turno_de_carro_y_otro_de_moto(sesion: Session):
    carro = cliente_con_vehiculo(sesion, telefono=CELULAR, placa="ABC123")
    sesion.add(
        Vehiculo(cliente_id=carro.cliente_id, tipo_vehiculo_id=tipo_vehiculo(sesion, "Moto").id)
    )
    sesion.flush()
    por_whatsapp(sesion, vehiculo=carro)

    chat = Chat(sesion).toca_el_id("menu.pedir").toca("Automóvil ABC123")
    assert chat.texto.startswith("Ya tienes el turno *C-01* en la fila de carros")

    chat.toca("Mantenerlo").toca_el_id("menu.pedir").toca("Moto sin placa")
    chat.toca("Ahora no").toca("Básico").toca("Confirmar turno")
    assert "Tu turno es *M-01*." in chat.texto
    assert len(turnos_de(sesion)) == 2


# --- La fila y los precios ------------------------------------------------------------------


def test_la_fila_en_sus_tres_casos_solo_con_cantidades(sesion: Session):
    chat = Chat(sesion).toca_el_id("menu.fila")
    assert chat.texto == (
        "Así está la fila ahora:\n\n*Carros:* hay puesto libre, puedes venir ya\n"
        "*Motos:* hay puesto libre, puedes venir ya"
    )
    assert chat.opciones == ["Pedir turno", "Servicios y precios"]

    ocupar_puestos(sesion, "C1", "C2")  # Básico: 1 hora cada uno
    chat.pasan(20).toca_el_id("menu.fila")
    assert "*Carros:* nadie está esperando; el próximo puesto se libera en aprox. 40 min" in (
        chat.texto
    )

    nuevo = por_whatsapp(sesion, ahora=chat.ahora)
    chat.toca_el_id("menu.fila")
    assert "*Carros:* 1 vehículo esperando; un turno nuevo entraría en aprox. 40 min" in (
        chat.texto
    )
    nombre = sesion.get(Cliente, nuevo.turno.cliente_id).nombre
    assert nombre not in chat.texto  # nunca nombres de otros clientes
    consultas = sesion.scalars(select(Consulta).order_by(Consulta.id)).all()
    assert [c.tipo for c in consultas] == [TipoConsulta.FILA] * 3


def test_los_precios_de_carro_preguntan_si_es_automovil_o_camioneta(sesion: Session):
    chat = Chat(sesion).escribe("hola").toca("Servicios y precios")
    assert chat.opciones == ["Automóvil", "Camioneta", "Moto"]

    chat.escribe("carro")
    assert chat.texto == "¿Automóvil o camioneta?"
    assert chat.opciones == ["Automóvil", "Camioneta"]
    chat.toca("Camioneta")

    assert chat.texto.startswith("Servicios para camioneta:\n\n*Básico* · $50.000\n")
    assert "*Empelotado* · $400.000" in chat.texto
    assert chat.texto.endswith("El pago es en el local.")
    assert chat.opciones == ["Pedir turno", "Ver la fila"]
    [consulta] = sesion.scalars(select(Consulta)).all()
    assert consulta.tipo == TipoConsulta.PRECIOS
    assert consulta.tipo_vehiculo_id == tipo_vehiculo(sesion, "Camioneta").id
    assert consulta.cliente_id is None  # no aceptó el aviso de datos: solo cuenta


# --- Mi turno, cancelar, llegada y en camino ---------------------------------------------------


def test_mi_turno_dice_como_va(sesion: Session):
    chat = Chat(sesion).escribe("mi turno")
    assert chat.texto == "No tienes turnos activos. ¿Quieres pedir uno?"

    vehiculo = cliente_con_vehiculo(sesion, telefono=CELULAR)
    ocupar_puestos(sesion, "C1", "C2")
    por_whatsapp(sesion)
    nuevo = por_whatsapp(sesion, vehiculo=vehiculo)
    chat.escribe("¿Cómo va mi turno?")
    assert chat.texto == (
        f"Tu turno *{nuevo.numero}* (carros): tienes 1 turno adelante; entras en aprox. 1 h."
    )
    assert chat.opciones == []


def test_cancelar_pregunta_antes_y_la_fila_avanza(sesion: Session):
    vehiculo = cliente_con_vehiculo(sesion, telefono=CELULAR)
    ocupar_puestos(sesion, "C1")
    mio = por_whatsapp(sesion, vehiculo=vehiculo)  # llamado para C2
    siguiente = por_whatsapp(sesion)

    chat = Chat(sesion).escribe("no cancelo el turno")
    assert chat.texto == (
        f"¿Seguro que quieres cancelar tu turno *{mio.numero}*? Si cancelas, pierdes tu lugar "
        "en la fila."
    )
    chat.toca("No, mantenerlo")
    assert chat.texto == "Perfecto, tu turno sigue igual."
    assert estado(sesion, mio.turno.id) == E.LLAMADO

    chat.escribe("cancelar").toca("Sí, cancelar")
    assert chat.texto.startswith(
        f"Listo, cancelamos tu turno *{mio.numero}*. Gracias por avisarnos"
    )
    assert estado(sesion, mio.turno.id) == E.CANCELADO
    assert estado(sesion, siguiente.turno.id) == E.LLAMADO  # la fila avanzó


def test_un_vehiculo_que_ya_esta_en_el_local_no_se_cancela_por_whatsapp(sesion: Session):
    vehiculo = cliente_con_vehiculo(sesion, telefono=CELULAR)
    nuevo = por_whatsapp(sesion, vehiculo=vehiculo)
    casos.marcar_llegada(sesion, nuevo.turno.id, actor=Actor.CLIENTE, ahora=LUNES_8AM)

    chat = Chat(sesion).escribe("cancelar")

    assert chat.texto.startswith("Tu vehículo ya está en el local, así que eso se resuelve allá.")
    assert estado(sesion, nuevo.turno.id) == E.LLEGO


def test_ya_estoy_aqui_y_en_camino(sesion: Session):
    vehiculo = cliente_con_vehiculo(sesion, telefono=CELULAR)
    nuevo = por_whatsapp(sesion, vehiculo=vehiculo)  # llamado hasta las 8:15

    chat = Chat(sesion).toca_el_id("turno.en_camino")
    assert chat.texto == "Gracias por avisar, te esperamos. Recuerda que tienes hasta las 8:15 a.m."
    assert sesion.get(Turno, nuevo.turno.id).en_camino

    chat.escribe("¡Ya llegué!")
    assert chat.texto.startswith(f"¡Bienvenido! Ya te marcamos como llegado ({nuevo.numero})")
    assert estado(sesion, nuevo.turno.id) == E.LLEGO
    chat.escribe("ya estoy aquí")
    assert chat.texto.startswith("Ya te tenemos como llegado")


# --- El código QR del mostrador --------------------------------------------------------------


def test_el_qr_conecta_al_cliente_del_mostrador_por_su_celular(sesion: Session):
    vehiculo = cliente_con_vehiculo(sesion, telefono=CELULAR, nombre="Ana Ruiz")
    nuevo = en_el_local(sesion, vehiculo=vehiculo)

    chat = Chat(sesion).escribe("Hola, ya estoy en la fila")

    assert chat.texto == (
        f"Listo: quedaste conectado al turno *{nuevo.numero}*. Te avisamos por aquí cuando tu "
        "vehículo esté listo."
    )
    ficha = consultas_de_clientes.ficha_del_cliente(sesion, vehiculo.cliente_id)
    assert ficha.escribio_por_whatsapp  # desde ahora recibe los avisos


def test_sin_celular_en_el_mostrador_se_conecta_con_el_numero_de_turno(sesion: Session):
    nuevo = en_el_local(sesion, sin_telefono=True)

    chat = Chat(sesion).escribe("Hola, ya estoy en la fila")
    assert chat.texto.startswith("No encontramos tu celular en la fila de hoy.")
    chat.escribe("C-9")
    assert chat.texto.startswith("No encontramos el turno C-09 en la fila de hoy.")
    chat.escribe(nuevo.numero.lower().replace("-", " "))

    assert chat.texto.startswith(f"Listo: quedaste conectado al turno *{nuevo.numero}*.")
    assert sesion.get(Cliente, nuevo.turno.cliente_id).telefono == CELULAR


def test_un_turno_con_otro_celular_no_se_puede_tomar(sesion: Session):
    nuevo = en_el_local(sesion)  # con otro celular

    Chat(sesion).escribe("ya estoy en la fila")
    chat = Chat(sesion).escribe(nuevo.numero)

    assert (
        chat.texto
        == "Ese turno tiene otro celular registrado. Pídele al administrador que lo revise."
    )
    assert sesion.get(Cliente, nuevo.turno.cliente_id).telefono != CELULAR


# --- La sesión del bot -------------------------------------------------------------------------


def test_a_los_30_minutos_sin_respuesta_vuelve_a_empezar(sesion: Session):
    chat = Chat(sesion).toca_el_id("menu.pedir").toca("Acepto")
    assert paso(sesion) == "nombre"

    chat.pasan(31).escribe("Ana")

    assert chat.texto.startswith("¡Hola! Bienvenido a Auto Ibaguer.")  # empieza de cero
    chat.toca_el_id("datos.si")
    assert chat.texto.startswith("Ese botón ya no está vigente.")


def test_dos_respuestas_de_la_conversacion_no_se_mezclan_entre_celulares(sesion: Session):
    uno = Chat(sesion, telefono="573001111111").toca_el_id("menu.pedir").toca("Acepto")
    otro = Chat(sesion, telefono="573002222222").escribe("hola")

    uno.escribe("Ana")

    assert otro.opciones == MENU
    assert uno.texto == "¿Qué vehículo traes?"


# --- Los avisos de la fila -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tipo", "contenido", "dice"),
    [
        (TipoAviso.AVISO_ANTICIPADO, {"turno": "C-05", "adelante": 2}, "tienes 2 turnos adelante"),
        (
            TipoAviso.LLAMADO,
            {"turno": "C-05", "gracia_hasta": (LUNES_8AM + minutos(15)).isoformat()},
            "Tienes hasta las 8:15 a.m. para llegar",
        ),
        (TipoAviso.PASA_AL_FINAL, {"turno": "C-05", "adelante": 4}, "pasó al final de la fila"),
        (TipoAviso.PASA_AL_FINAL, {"turno": "C-05", "adelante": 0}, "sal hacia el lavadero"),
        (TipoAviso.NO_LLEGO, {"turno": "C-05"}, "perdiste el turno *C-05*"),
        (TipoAviso.VEHICULO_LISTO, {"turno": "C-05"}, "Tu vehículo está listo (turno *C-05*)"),
    ],
)
def test_cada_aviso_de_la_fila_tiene_su_mensaje(sesion: Session, tipo, contenido, dice):
    mensaje = mensaje_del_aviso(tipo, contenido, leer_parametros(sesion))

    assert dice in mensaje.texto
    if tipo == TipoAviso.LLAMADO:
        assert [o.titulo for o in mensaje.botones] == ["En camino", "Ya estoy aquí"]


def test_los_botones_del_aviso_de_llamado_funcionan_en_cualquier_momento(sesion: Session):
    vehiculo = cliente_con_vehiculo(sesion, telefono=CELULAR)
    nuevo = por_whatsapp(sesion, vehiculo=vehiculo)
    Chat(sesion).toca_el_id("menu.precios")  # el cliente estaba en otra cosa

    Chat(sesion).toca_el_id("turno.llegue")

    assert estado(sesion, nuevo.turno.id) == E.LLEGO


# --- Lo que WhatsApp acepta ----------------------------------------------------------------------


def test_whatsapp_no_acepta_mensajes_que_no_caben():
    tres = tuple(Opcion(f"o.{i}", f"Opción {i}") for i in range(3))
    Mensaje("Hola", botones=tres)  # tres botones sí

    with pytest.raises(ValueError, match="más de 3 botones"):
        Mensaje("Hola", botones=(*tres, Opcion("o.4", "Otra")))
    with pytest.raises(ValueError, match="el botón"):
        Mensaje("Hola", botones=(Opcion("o.1", "Un título demasiado largo"),))
    with pytest.raises(ValueError, match="más de 1024"):
        Mensaje("x" * 1025, botones=tres)
    with pytest.raises(ValueError, match="una lista de 0 o más de 10"):
        Mensaje("Hola", lista=Lista("Ver", tuple(Opcion(f"o.{i}", "x") for i in range(11))))
    with pytest.raises(ValueError, match="dos opciones con el mismo id"):
        Mensaje("Hola", botones=(Opcion("o.1", "Uno"), Opcion("o.1", "Dos")))


def test_el_menu_de_precios_largo_se_acorta_para_que_quepa(sesion: Session):
    fila_de_carros = servicio(sesion, "Básico").fila_id
    precios = {tipo_vehiculo(sesion, nombre).id: 50_000 for nombre in ("Automóvil", "Camioneta")}
    for numero in range(12):
        configuracion.agregar_servicio(
            sesion,
            fila_id=fila_de_carros,
            nombre=f"Servicio especial {numero}",
            descripcion="Una descripción bastante larga del servicio " * 2,
            precios=precios,
            duracion_min=60,
        )

    chat = Chat(sesion).toca_el_id("menu.precios").toca("Automóvil")

    assert len(chat.ultimas[0].texto) <= 1024
    assert "*Básico* · $40.000" in chat.texto
    assert "Una descripción" not in chat.texto  # sin descripciones, para que quepa


def test_otro_dia_el_consecutivo_vuelve_a_empezar_tambien_por_whatsapp(sesion: Session):
    martes = LUNES_8AM + timedelta(days=1)
    chat = Chat(sesion, ahora=martes).toca_el_id("menu.pedir").toca("Acepto").escribe("Ana")

    chat.toca("Moto").toca("Ahora no").toca("Básico").toca("Confirmar turno")

    assert "Tu turno es *M-01*." in chat.texto
