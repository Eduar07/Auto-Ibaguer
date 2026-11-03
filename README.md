# Auto Ibaguer

Sistema de turnos para el lavadero de carros y motos **Auto Ibaguer**:

- **Bot de WhatsApp**: el cliente pide turno, pregunta precios y ve cómo va la fila. Gemini le ayuda a entender lo que la persona escribe con sus propias palabras.
- **Dashboard**: el administrador mueve la fila, pasa vehículos a los puestos, cobra y lleva las cuentas del día, desde el celular, la tablet o el computador.
- **Base de datos PostgreSQL**: ahí queda todo guardado.

## Cómo va

| Hito | Estado |
|---|---|
| 1 · Todo conectado | Estructura del proyecto y PostgreSQL listos. WhatsApp pendiente hasta tener el número del bot. |
| 2 · Base de datos | Listo: 16 tablas, reglas dentro de la base y datos iniciales del lavadero. |
| 3 · Reglas del negocio | Listo: la fila completa (pedir turno, llamados, avisos, gracia, puestos, cobro y correcciones), con sus pruebas y sin choques. |
| 4 · Dashboard | Construido: entrar, la fila en vivo con sus botones, nuevo turno en el local, cobrar, clientes (buscar, ficha, editar y borrar datos) y configuración (negocio, horario, bot, puestos, menú y precios). Falta la prueba final desde el celular. |
| 5 · Bot con botones | — |
| 6 · Reloj | Construido: cada 30 segundos pasa al final a quien no llegó a tiempo, vence las sesiones del bot y marca los lavados demorados. Los reintentos de WhatsApp quedan listos para cuando llegue el envío (hito 5). Falta su prueba en tu computador. |
| 7 · Gemini | — |
| 8 · Producción | — |
| 9 · Piloto en el local | — |

## Qué necesitas en tu computador

- Ubuntu en WSL (o Linux / macOS)
- [Docker](https://docs.docker.com/engine/install/ubuntu/)
- [uv](https://docs.astral.sh/uv/) (también instala Python 3.13)
- git

Trabaja dentro de tu carpeta de Linux (por ejemplo `~/proyectos`), no en `/mnt/c/...`: desde ahí todo corre mucho más rápido.

## La primera vez

```bash
# 1. Tu archivo de configuración local (nunca se sube a GitHub)
cp .env.example .env

# 2. Encender PostgreSQL en Docker
docker compose up -d

# 3. Instalar Python y las librerías, con las versiones exactas de uv.lock
uv sync

# 4. Crear las tablas y cargar los datos del lavadero
uv run auto-ibaguer preparar-base

# 5. Crear tu usuario del dashboard (te pide la contraseña, mínimo 10 caracteres)
uv run auto-ibaguer crear-admin --usuario eduar

# 6. Encender el servidor
uv run fastapi dev app/main.py
```

Abre <http://localhost:8000> en el navegador y entra con el usuario del paso 5.

Para revisar solo el servidor, <http://localhost:8000/salud> debe decir `{"estado": "ok", "base_de_datos": "ok"}`. En <http://localhost:8000/docs> FastAPI muestra todas las rutas.

## El dashboard

| Pantalla | Qué se hace ahí |
|---|---|
| **Fila en vivo** | Los puestos, quién espera y lo que falta cobrar. Se actualiza sola en cuanto algo cambia, sin recargar: lo que haga otro celular, el bot o el reloj aparece en menos de un segundo. |
| Botón principal de cada turno | **Marcar llegada** (llamado), **Pasar a puesto** (ya llegó), **Marcar listo** (en el puesto) y **Cobrar** (listo). |
| Menú ⋯ de cada turno | Marcar llegada, corregir datos (tipo de vehículo, placa, servicio) y cancelar. |
| **Nuevo turno** | Para el cliente que llega sin escribir por WhatsApp. Muestra antes qué número le toca y cuánto esperaría. |
| **Clientes** | Buscar por nombre, celular o placa; la ficha con sus vehículos y visitas; dar turno, editar datos y borrarlos si el cliente lo pide (Ley 1581). |
| **Configuración** | Negocio y horario, bot de WhatsApp, puestos (prender, apagar y agregar), menú y precios, y el aviso de datos personales. Se guarda todo junto, y si algo está mal no se guarda nada. |
| Lavado **Demorado** | Cuando un lavado pasa su duración estimada, su puesto se pone naranja y arriba se cuenta. En el celular, la pestaña de esa fila muestra un punto naranja aunque estés viendo la otra. Casi siempre quiere decir que ya está listo y falta marcarlo: mientras tanto el cliente no recibe el aviso y la espera de la fila se calcula mal. |

En el celular se ve una fila a la vez (pestañas **Carros** y **Motos**) y las secciones quedan en la barra de abajo. Para ver cómo queda en el celular sin salir del computador: en Chrome, `F12` y luego `Ctrl+Shift+M`. Desde el celular de verdad se usará cuando esté en la nube (hito 8).

## El reloj

El servidor trae un reloj que cada 30 segundos revisa lo que vence con el tiempo, sin que nadie toque nada:

| Qué revisa | Qué hace |
|---|---|
| Llamados que no llegaron en su tiempo de gracia (15 minutos) | La primera vez el turno pasa al final de la fila; la segunda, pierde el turno. La fila avanza, se llama al siguiente y el dashboard se actualiza solo. |
| Sesiones del bot sin respuesta (30 minutos) | El bot vuelve al menú y olvida lo que el cliente iba escribiendo. Si la persona nunca aceptó el aviso de datos, se borra también su celular. |
| Avisos de WhatsApp que no salieron | Se reintentan cada vez más tarde (30 segundos, 1, 2, 5 y 10 minutos) y después se dejan. Un aviso que ya no sirve (por ejemplo, el cliente ya llegó) no se envía. Empieza a funcionar cuando WhatsApp esté conectado (hito 5); mientras tanto los avisos esperan en la bandeja de salida. |

Lo que hace queda escrito en la terminal del servidor, sin nombres ni celulares:

```text
INFO:     Reloj encendido: revisa cada 30 segundos.
INFO:     Reloj: C-05 no llegó a tiempo: pasó al final de la fila.
```

Para probarlo sin el bot, pide un turno como si el cliente escribiera por WhatsApp:

```bash
uv run auto-ibaguer simular-turno                    # un carro, con un cliente de prueba
uv run auto-ibaguer simular-turno --vehiculo moto
uv run auto-ibaguer simular-turno --nombre "Ana Ruiz" --celular 3001234567
```

Si hay un puesto libre, el turno queda **Llamado** y, si nadie lo marca, a los 15 minutos el reloj lo pasa al final. Para no esperar tanto, baja el tiempo de gracia en **Configuración** (mínimo 5 minutos). Por WhatsApp no se dan turnos fuera del horario, así que de noche amplía el horario en **Configuración** mientras pruebas. El comando no funciona en producción.

Para mirar algo sin que el reloj mueva la fila, pon `RELOJ_ACTIVO=false` en tu `.env` y vuelve a encender el servidor.

## El día a día

```bash
docker compose up -d                # encender PostgreSQL
uv run fastapi dev app/main.py      # encender el servidor (Ctrl+C para apagarlo)
docker compose down                 # apagar PostgreSQL (los datos se conservan)
```

`preparar-base` se puede correr cuantas veces quieras: crea lo que falte y nunca borra ni cambia lo que ya existe.

## Pruebas

```bash
uv run pytest
```

Las pruebas usan una base aparte, `auto_ibaguer_pruebas`, que se borra y se crea en cada corrida; tus datos de desarrollo no se tocan. Necesitan el PostgreSQL de Docker encendido.

Antes de subir un cambio:

```bash
uv run ruff format .     # ordena el código
uv run ruff check .      # busca errores comunes
uv run pytest
```

GitHub corre lo mismo en cada pull request (pestaña **Actions**). Si sale en rojo, el cambio no se une a `main`.

## Cómo está organizado

```text
app/
├── catalogo/       filas, puestos, tipos de vehículo, servicios, precios y parámetros
├── clientes/       clientes, vehículos, placas y teléfonos
├── turnos/         la fila: turnos, llamados, avisos, puestos, historial y cobro
├── bot/            conversación de WhatsApp
├── integraciones/  mensajes que entran y salen de WhatsApp (bandejas y reintentos)
├── web/            dashboard: pantallas, sesión, fila en vivo y administradores
├── reloj/          lo que pasa solo cada 30 segundos (gracias, sesiones del bot, avisos)
├── db/             conexión a la base de datos
├── cli.py          comandos de la terminal (preparar-base, crear-admin, simular-turno)
├── config.py       configuración técnica, leída del archivo .env
├── errores.py      el error base de las reglas del negocio
├── formato.py      pesos, horas y fechas a la colombiana ($40.000, 5:30 a.m.)
├── tiempo.py       la hora del sistema y la hora de Bogotá
└── main.py         servidor web
migraciones/        cambios de la base de datos, numerados y en orden (Alembic)
pruebas/            pruebas automáticas
```

Cada módulo se arma por capas, de la más pura a la que toca la base de datos. En `turnos/`:

| Archivo | Qué tiene | Se prueba |
|---|---|---|
| `estados.py` | Estados del turno y demás listas cerradas | — |
| `reglas.py` | Las reglas puras: quién se llama, quién recibe aviso, cuánto se espera, qué cambios se permiten | Sin base de datos, en milisegundos |
| `casos_de_uso.py` | Lo que hacen el bot, el dashboard y el reloj: pedir turno, pasar a puesto, cobrar... | Con PostgreSQL |
| `consultas.py` | Lo que se pregunta sin cambiar nada: cómo va la fila, "mi turno" | Con PostgreSQL |
| `avisos.py` | Si un aviso de WhatsApp todavía sirve antes de enviarlo (por ejemplo, el del llamado ya no sirve si el cliente llegó) | Con PostgreSQL |
| `datos.py` | Lecturas y bloqueos de la base que comparten los casos de uso | Con PostgreSQL |
| `tablas.py` | Las tablas | Con PostgreSQL |

Es como en Java separar el dominio, los `@Service` y los repositorios: el bot y el dashboard solo llaman a los casos de uso, así cada regla vive en un solo lugar.

En `web/` está el dashboard. Las páginas se arman en el servidor con plantillas y [HTMX](https://htmx.org) cambia solo el pedazo que hace falta: un botón le pide la acción al servidor y este devuelve la fila ya actualizada.

| Archivo | Qué tiene |
|---|---|
| `comun.py` | Lo que comparten las pantallas: plantillas, la sesión del administrador y las respuestas. |
| `entrar.py`, `fila.py`, `clientes.py`, `configuracion.py` | Las rutas de cada pantalla. Llaman a los casos de uso; no tienen reglas propias. |
| `tablero.py` | Arma lo que muestra la fila en vivo. Solo lee. |
| `en_vivo.py` | Le avisa al navegador cuando una fila cambia (PostgreSQL `LISTEN/NOTIFY` y Server-Sent Events). |
| `sesiones.py` | La cookie firmada de la sesión y el freno a quien intenta adivinar contraseñas. |
| `plantillas/` | El HTML de cada pantalla y diálogo (Jinja2). |
| `estaticos/` | Estilos, el JavaScript de la página, HTMX y las letras del diseño. |

## Dónde se cambia cada cosa

| Qué | Dónde |
|---|---|
| Horario, tiempo de gracia, cuándo avisar, nombre y teléfono del negocio, aviso de datos | Pantalla **Configuración** del dashboard (se guarda en la tabla `configuracion`). No se toca el código. |
| Servicios, precios y puestos | Pantalla **Configuración**. Un precio nuevo apaga el viejo y crea otro en `servicio_precio`; así los cobros pasados no cambian. De la duración estimada de cada servicio dependen la espera de la fila y la alerta de lavado demorado. |
| Colores, letras y tamaños del dashboard | `app/web/estaticos/css/dashboard.css`. Los colores del diseño están al comienzo, con nombre. |
| Conexión a la base, claves de Meta y Gemini, reloj encendido o apagado | Archivo `.env`, que nunca se sube a GitHub. `.env.example` solo trae los nombres. |

## Cambiar una tabla

1. Cambia el modelo en el `tablas.py` del módulo.
2. Crea la migración con el siguiente número (la última es la 0004):
   `uv run alembic revision --autogenerate --rev-id 0005 -m "que cambio"`
3. Revisa el archivo nuevo en `migraciones/versions/`: Alembic adivina bien casi siempre, pero no siempre.
4. Aplícala: `uv run auto-ibaguer preparar-base`
5. `uv run pytest`: una prueba falla si el modelo y las migraciones no coinciden.

## Comandos útiles

| Para | Comando |
|---|---|
| Entrar a la base de datos | `docker compose exec postgres psql -U auto -d auto_ibaguer` |
| Ver las tablas (dentro de psql) | `\dt` |
| Salir de psql | `\q` |
| Ver los mensajes de PostgreSQL | `docker compose logs postgres` |
| Pedir un turno como si fuera por WhatsApp | `uv run auto-ibaguer simular-turno` |
| Ver los avisos que esperan en la bandeja de salida (dentro de psql) | `SELECT id, tipo, estado, intentos, creado_en FROM mensaje_saliente ORDER BY id DESC LIMIT 10;` |
| Borrar la base y empezar de cero | `docker compose down -v`, luego `docker compose up -d` y `uv run auto-ibaguer preparar-base` |

## Si algo falla

| Error | Solución |
|---|---|
| `permission denied ... docker.sock` | `sudo usermod -aG docker $USER`, luego en PowerShell `wsl --shutdown` y abre Ubuntu otra vez. |
| `docker: 'compose' is not a docker command` | Instala el complemento: `sudo apt install docker-compose-plugin` |
| `port is already allocated` (5433) | Otro programa usa ese puerto. Cambia `5433` en `docker-compose.yml` y en `.env`. |
| `No hay conexión con PostgreSQL`, `connection refused` o `server closed the connection unexpectedly` | PostgreSQL está apagado. Enciéndelo con `docker compose up -d` y espera a que `docker compose ps` diga `(healthy)`. |
| `docker compose ps` dice `Restarting` o `Exited` | La base no logra arrancar: mira por qué con `docker compose logs postgres --tail 30`. |
| `Cannot connect to the Docker daemon` | Docker está apagado: abre Docker Desktop o, si lo instalaste dentro de Ubuntu, `sudo service docker start`. |
| Al entrar dice `Demasiados intentos fallidos` | Hubo 5 contraseñas equivocadas seguidas con ese usuario. Espera 15 minutos o apaga y enciende el servidor. |
| Olvidaste la contraseña del dashboard | `uv run auto-ibaguer crear-admin --usuario eduar` y escribe una nueva. Las sesiones abiertas se cierran solas. |
| `simular-turno` dice `No se dio el turno: Los turnos por WhatsApp se dan de...` | Es la regla del horario. Para probar a esa hora, amplía el horario en **Configuración** y después déjalo como estaba. |
| Un llamado se queda en `Llamado · tiempo vencido` | El reloj lo pasa al final en menos de 30 segundos. Si no pasa, mira en la terminal del servidor si dice `Reloj encendido` y que `RELOJ_ACTIVO` no esté en `false`. |

## Seguridad

- El repositorio es privado y debe seguir así.
- El archivo `.env` nunca se sube. Ahí irán las claves de Meta y Gemini.
- Nunca compartas el token de Meta ni el PIN de 6 dígitos del número, tampoco en capturas de pantalla.
- Las contraseñas del dashboard se guardan cifradas con Argon2; ni siquiera quien tenga la base de datos puede leerlas.
- La sesión del dashboard vive en una cookie firmada con `CLAVE_SECRETA`. Vence tras `SESION_HORAS` (12) sin usar el dashboard, y se cierra sola si cambian la contraseña o desactivan al usuario.
- En producción, `CLAVE_SECRETA` es obligatoria: el servidor no arranca sin una clave larga y aleatoria. `.env.example` dice cómo generarla.
- Después de 5 contraseñas equivocadas, ese usuario queda bloqueado 15 minutos.
- Ninguna otra página puede enviar formularios al dashboard: el servidor revisa de dónde viene cada envío.
- Lo que el reloj escribe en la terminal no lleva nombres ni celulares: en la nube, esos registros los pueden ver otras personas.
