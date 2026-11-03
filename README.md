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
| 4 · Dashboard | Parte 1 lista: entrar, la fila en vivo, nuevo turno en el local, pasar a puesto, marcar listo, cobrar, corregir y cancelar. Sigue la parte 2: clientes y configuración. |
| 5 · Bot con botones | — |
| 6 · Reloj | — |
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

En el celular se ve una fila a la vez (pestañas **Carros** y **Motos**). Para ver cómo queda en el celular sin salir del computador: en Chrome, `F12` y luego `Ctrl+Shift+M`. Desde el celular de verdad se usará cuando esté en la nube (hito 8).

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
├── integraciones/  mensajes que entran y salen de WhatsApp (bandejas)
├── web/            dashboard: pantallas, sesión, fila en vivo y administradores
├── db/             conexión a la base de datos
├── cli.py          comandos de la terminal (preparar-base, crear-admin)
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
| `datos.py` | Lecturas y bloqueos de la base que comparten los casos de uso | Con PostgreSQL |
| `tablas.py` | Las tablas | Con PostgreSQL |

Es como en Java separar el dominio, los `@Service` y los repositorios: el bot y el dashboard solo llaman a los casos de uso, así cada regla vive en un solo lugar.

En `web/` está el dashboard. Las páginas se arman en el servidor con plantillas y [HTMX](https://htmx.org) cambia solo el pedazo que hace falta: un botón le pide la acción al servidor y este devuelve la fila ya actualizada.

| Archivo | Qué tiene |
|---|---|
| `dashboard.py` | Las rutas: entrar, la fila y el botón de cada acción. Llama a los casos de uso; no tiene reglas propias. |
| `tablero.py` | Arma lo que muestra la fila en vivo. Solo lee. |
| `en_vivo.py` | Le avisa al navegador cuando una fila cambia (PostgreSQL `LISTEN/NOTIFY` y Server-Sent Events). |
| `sesiones.py` | La cookie firmada de la sesión y el freno a quien intenta adivinar contraseñas. |
| `plantillas/` | El HTML de cada pantalla y diálogo (Jinja2). |
| `estaticos/` | Estilos, el JavaScript de la página, HTMX y las letras del diseño. |

## Dónde se cambia cada cosa

| Qué | Dónde |
|---|---|
| Horario, tiempo de gracia, cuándo avisar, nombre y teléfono del negocio | Tabla `configuracion` de la base de datos (más adelante, desde el dashboard). No se toca el código. |
| Servicios y precios | Tablas `servicio` y `servicio_precio`. Para cambiar un precio se desactiva el viejo y se crea el nuevo; así los cobros pasados no cambian. |
| Colores, letras y tamaños del dashboard | `app/web/estaticos/css/dashboard.css`. Los colores del diseño están al comienzo, con nombre. |
| Conexión a la base, claves de Meta y Gemini | Archivo `.env`, que nunca se sube a GitHub. `.env.example` solo trae los nombres. |

## Cambiar una tabla

1. Cambia el modelo en el `tablas.py` del módulo.
2. Crea la migración con el siguiente número (la última es la 0002):
   `uv run alembic revision --autogenerate --rev-id 0003 -m "que cambio"`
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

## Seguridad

- El repositorio es privado y debe seguir así.
- El archivo `.env` nunca se sube. Ahí irán las claves de Meta y Gemini.
- Nunca compartas el token de Meta ni el PIN de 6 dígitos del número, tampoco en capturas de pantalla.
- Las contraseñas del dashboard se guardan cifradas con Argon2; ni siquiera quien tenga la base de datos puede leerlas.
- La sesión del dashboard vive en una cookie firmada con `CLAVE_SECRETA`. Vence tras `SESION_HORAS` (12) sin usar el dashboard, y se cierra sola si cambian la contraseña o desactivan al usuario.
- En producción, `CLAVE_SECRETA` es obligatoria: el servidor no arranca sin una clave larga y aleatoria. `.env.example` dice cómo generarla.
- Después de 5 contraseñas equivocadas, ese usuario queda bloqueado 15 minutos.
- Ninguna otra página puede enviar formularios al dashboard: el servidor revisa de dónde viene cada envío.
