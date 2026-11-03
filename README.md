# Auto Ibaguer

Sistema de turnos para el lavadero de carros y motos **Auto Ibaguer**:

- **Bot de WhatsApp**: el cliente pide turno, pregunta precios y ve cómo va la fila. Gemini le ayuda a entender lo que la persona escribe con sus propias palabras.
- **Dashboard**: el administrador mueve la fila, pasa vehículos a los puestos, cobra y lleva las cuentas del día, desde el celular, la tablet o el computador.
- **Base de datos PostgreSQL**: ahí queda todo guardado.

## Cómo va

| Hito | Estado |
|---|---|
| 1 · Todo conectado | Estructura del proyecto y PostgreSQL listos. Lo de WhatsApp (el webhook con la firma de Meta, guardar cada mensaje y la pantalla **Mensajes de WhatsApp**) está construido y probado con mensajes de mentira. Falta la prueba de verdad cuando llegue la SIM. |
| 2 · Base de datos | Listo: 16 tablas, reglas dentro de la base y datos iniciales del lavadero. |
| 3 · Reglas del negocio | Listo: la fila completa (pedir turno, llamados, avisos, gracia, puestos, cobro y correcciones), con sus pruebas y sin choques. |
| 4 · Dashboard | Construido: entrar, la fila en vivo con sus botones, nuevo turno en el local, cobrar, clientes (buscar, ficha, editar y borrar datos) y configuración (negocio, horario, bot, puestos, menú y precios). Falta la prueba final desde el celular. |
| 5 · Bot con botones | Construido: la conversación con botones funciona en **Probar el bot**, ya está conectada a WhatsApp (recibe, contesta, reintenta y a cada cliente le llegan sus mensajes en orden) y sus textos se cambian en **Configuración > Textos del bot**. Falta la prueba desde tu celular cuando llegue la SIM. |
| 6 · Reloj | Construido: cada 30 segundos pasa al final a quien no llegó a tiempo, vence las sesiones del bot y marca los lavados demorados. Con WhatsApp conectado también envía y reintenta los mensajes, y atiende los que quedaron sin atender. Falta su prueba en tu computador. |
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
| **Configuración** | Negocio y horario, bot de WhatsApp, puestos (prender, apagar y agregar), menú y precios, y el aviso de datos personales. Se guarda todo junto, y si algo está mal no se guarda nada. Desde ahí se abren **Textos del bot** (cada texto se guarda por separado) y **Mensajes de WhatsApp**. |
| Lavado **Demorado** | Cuando un lavado pasa su duración estimada, su puesto se pone naranja y arriba se cuenta. En el celular, la pestaña de esa fila muestra un punto naranja aunque estés viendo la otra. Casi siempre quiere decir que ya está listo y falta marcarlo: mientras tanto el cliente no recibe el aviso y la espera de la fila se calcula mal. |

En el celular se ve una fila a la vez (pestañas **Carros** y **Motos**) y las secciones quedan en la barra de abajo. Para ver cómo queda en el celular sin salir del computador: en Chrome, `F12` y luego `Ctrl+Shift+M`. Desde el celular de verdad se usará cuando esté en la nube (hito 8).

## El bot

El bot conversa con botones y unas pocas palabras; el texto libre lo entenderá Gemini en el hito 7.

| El cliente | El bot |
|---|---|
| Escribe cualquier cosa la primera vez | Saluda con el horario y ofrece **Pedir turno**, **Ver la fila** y **Servicios y precios**. |
| Pide turno | Si es nuevo, primero el aviso de datos (sin aceptarlo no se guarda nada). Después nombre (solo la primera vez), vehículo, placa (puede ser después), servicio (o "lo decido en el local"), resumen y confirmación. |
| Ve la fila | Solo cantidades, nunca nombres ni placas: hay puesto libre, nadie esperando, o cuántos esperan y cuánto tardaría. |
| Ve los precios | El menú de su tipo de vehículo, con descripción. Si dice "carro", pregunta si es automóvil o camioneta. |
| Escribe *mi turno* | Cómo va: cuántos tiene adelante y cuánto le falta, o hasta qué hora tiene si lo están llamando. |
| Escribe *cancelar* | Siempre pregunta antes con botones (cancelar no se puede deshacer), y la fila avanza. |
| Escribe *ya estoy aquí* o *en camino* | Marca la llegada o el "en camino"; el dashboard lo ve al instante. |
| Escanea el código QR del mostrador | "Hola, ya estoy en la fila": el bot encuentra su turno por el celular, o le pide el número de turno. |
| Dice "gracias" | Se despide sin volver a mostrar el menú. |

Fuera del horario o con el bot apagado no da turnos, pero sí muestra la fila y los precios. Tampoco deja pedir un segundo turno en la misma fila: muestra el que tiene y ofrece mantenerlo o cancelarlo. Si el cliente no responde en 30 minutos, la conversación vuelve a empezar.

**Probar el bot**: en Configuración, botón **Probar el bot** (o <http://localhost:8000/probar-bot>). Es la misma conversación que tendrá WhatsApp, con los mismos botones, y los avisos de la fila (te llamamos, tu vehículo está listo...) llegan a la charla. Lo que pidas ahí entra a la fila de verdad de tu computador. Solo existe en desarrollo.

**Textos del bot**: en Configuración, botón **Textos del bot** (o <http://localhost:8000/configuracion/textos>). Ahí se cambian los 38 textos que más leen los clientes (el saludo, el turno confirmado, los avisos de la fila...), agrupados como la conversación. Al abrir uno ves el texto, los datos que llena el bot ({numero}, {hora}...) y, al lado, cómo le llega al cliente mientras escribes. Cada texto se guarda por separado y el bot lo usa desde el siguiente mensaje; **Volver al original** lo deja como venía. Antes de guardar se revisa que no le falte un dato que el cliente necesita (por ejemplo, la hora límite del llamado), que no use datos que ese mensaje no tiene y que quepa en WhatsApp. Los botones y los demás textos (errores al escribir una placa, por ejemplo) siguen en `app/bot/textos.py`.

| Archivo de `bot/` | Qué tiene |
|---|---|
| `mensajes.py` | Lo que entra (texto o botón tocado) y lo que sale (texto con hasta 3 botones o una lista), con los límites de WhatsApp. |
| `textos.py` | Todo lo que dice el bot, en un solo lugar: los originales. Si un texto cambiado en Configuración no se puede usar, el bot usa el original. |
| `textos_editables.py` | Los textos que se cambian en Configuración: de qué datos se arma cada uno, cuáles no se pueden quitar, cuánto puede medir, la vista previa y guardarlos. |
| `conversacion.py` | Qué contesta a cada mensaje y en qué paso va cada cliente. Llama a los mismos casos de uso que el dashboard. |
| `avisos.py` | Cómo dice el bot cada aviso de la fila. |
| `sesiones.py` | Las conversaciones sin respuesta que vencen (las limpia el reloj). |

## WhatsApp

Así viaja cada mensaje:

1. El cliente escribe y Meta le avisa al servidor en `/webhook/whatsapp` (el *webhook*).
2. El servidor revisa la firma de Meta (si el aviso no viene de Meta, lo descarta), guarda el mensaje y le contesta a Meta de una vez. Si Meta manda el mismo mensaje dos veces, el repetido se ignora.
3. Enseguida, aparte, el bot lo atiende: decide qué contestar y deja las respuestas en la bandeja de salida, en la misma transacción de lo que hizo (pedir el turno, cancelarlo...). Los mensajes de un mismo cliente se atienden de a uno y en orden.
4. Las respuestas salen por la API de WhatsApp. Si WhatsApp falla un momento, el reloj las reintenta, y a cada cliente sus mensajes le llegan en el orden en que el bot los escribió.

Los avisos de la fila (te llamamos, tu vehículo está listo...) salen por el mismo camino. WhatsApp solo deja escribirle libremente a quien le escribió al negocio en las últimas 24 horas; si no, el aviso no sale y queda anotado por qué. Si el servidor se apaga a mitad de camino no se pierde nada: lo que se guardó se atiende o se envía cuando vuelve.

La pantalla **Mensajes de WhatsApp** (en Configuración, o <http://localhost:8000/mensajes>) muestra lo último que entró y salió, si WhatsApp está conectado y, cuando un mensaje no salió, por qué. El celular se ve a medias (300 \*\*\* 4567).

| Archivo de `integraciones/` | Qué tiene |
|---|---|
| `whatsapp.py` | La API de WhatsApp de Meta: la firma, leer lo que llega y enviar (texto, botones o lista). Sabe qué errores de Meta vale la pena reintentar. |
| `bandeja_entrada.py` | Guarda cada mensaje que llega y lo atiende con el bot, de a uno por cliente. |
| `bandeja_salida.py` | Deja cada mensaje por enviar y lo envía con reintentos, en orden para cada cliente. |
| `tablas.py` | Las dos bandejas: `mensaje_entrante` y `mensaje_saliente`. |

Es lo que en sistemas grandes se llama *inbox* y *outbox*: primero se guarda y después se atiende o se envía, como cuando en Java se separa recibir de procesar con una cola.

### Conectar WhatsApp (cuando llegue la SIM)

Se hace en tu app de [Meta for Developers](https://developers.facebook.com/apps) y en el [Administrador de WhatsApp](https://business.facebook.com/wa/manage/). Cada dato va en tu `.env`, que nunca se sube a GitHub. El token, la clave secreta y el PIN no se pegan en ningún chat ni salen en capturas.

1. **Agrega el número.** Pon la SIM en un celular; ese número no puede tener WhatsApp instalado (si lo tiene, borra primero esa cuenta de WhatsApp). En el Administrador de WhatsApp: *Números de teléfono > Agregar número*. Escribe el nombre que verán los clientes (Auto Ibaguer) y confirma con el código que llega por SMS o por llamada.
2. **Agrega un método de pago** a la cuenta de WhatsApp (en la *Configuración del negocio*, la cuenta de WhatsApp > *Pagos*): sin él, Meta no entrega los mensajes.
3. **El identificador del número:** en la app, *WhatsApp > Configuración de la API* (*API Setup*), elige el número nuevo y copia su *identificador del número de teléfono* en `WHATSAPP_ID_NUMERO`.
4. **Un token que no vence:** en la *Configuración del negocio > Usuarios del sistema*, crea uno con rol de administrador, asígnale la app y la cuenta de WhatsApp, y genera un token sin vencimiento con los permisos `whatsapp_business_messaging` y `whatsapp_business_management`. Cópialo en `WHATSAPP_TOKEN`: es la llave para enviar mensajes a nombre del negocio.
5. **La clave secreta de la app:** en la app, *Configuración de la app > Básica > Clave secreta de la app*. Cópiala en `WHATSAPP_SECRETO_APP`. Con ella el servidor sabe que un aviso viene de Meta.
6. **Un token de verificación**, inventado por ti: `python3 -c "import secrets; print(secrets.token_urlsafe(24))"` y lo copias en `WHATSAPP_TOKEN_VERIFICACION`.
7. **Registra el número** con un PIN de 6 dígitos que inventas (la verificación en dos pasos de WhatsApp). Meta solo deja hacerlo por la API:

   ```bash
   uv run auto-ibaguer registrar-whatsapp
   ```

   Te pide el PIN sin mostrarlo y no lo guarda. Anótalo en un lugar seguro: WhatsApp lo vuelve a pedir si el número se registra otra vez.
8. **Enciende el servidor** (`uv run fastapi dev app/main.py`; si ya estaba encendido, apágalo con Ctrl+C y vuelve a encenderlo, porque el `.env` se lee al arrancar). La terminal debe decir `Reloj: WhatsApp conectado` y la pantalla **Mensajes de WhatsApp**, que está conectado.
9. **Una dirección pública** para que Meta llegue a tu computador. En otra terminal: `cloudflared tunnel --url http://localhost:8000` (cómo instalarlo, abajo) y copia la dirección `https://….trycloudflare.com` que muestra. Cambia cada vez que lo enciendes; en el hito 8 queda una fija en la nube.
10. **Registra el webhook:** en la app, *WhatsApp > Configuración*: URL de devolución de llamada `https://….trycloudflare.com/webhook/whatsapp` y el token de verificación del paso 6; *Verificar y guardar*. Después, en *Campos del webhook*, suscríbete a **messages**.
11. **Pon la app en modo Live** (arriba, en el panel de la app): en modo de desarrollo Meta no manda todos los avisos. Para pasarla, Meta pide la dirección de una política de privacidad; la armamos ese día.
12. **Pruébalo:** desde otro celular, escríbele "hola" al número del bot. Debe contestar con el menú, y en **Mensajes de WhatsApp** aparecen los dos mensajes.

Instalar `cloudflared` en Ubuntu (WSL), una sola vez:

```bash
curl -L -o cloudflared.deb https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb
sudo dpkg -i cloudflared.deb && rm cloudflared.deb
```

Si no llegan los mensajes, mira la terminal del servidor y la pantalla **Mensajes de WhatsApp**:

| Lo que ves | Qué revisar |
|---|---|
| `sin una firma válida` en la terminal | `WHATSAPP_SECRETO_APP` no es la clave secreta de esta app. |
| Meta no verifica la URL del webhook | Que el servidor y el túnel estén encendidos y que el token de verificación sea el mismo del `.env`. |
| No aparece nada al escribirle al bot | Que el webhook esté suscrito a **messages**, la app en modo Live y el túnel encendido (si lo apagaste, la dirección cambió). |
| Llegan los mensajes pero las respuestas dicen *No salió* | El motivo sale debajo del mensaje. `error 190`: el token venció o no es ese; `error 131042`: falta el método de pago o tiene un problema; `error 131047`: el cliente no ha escrito en 24 horas. |

## El reloj

El servidor trae un reloj que cada 30 segundos revisa lo que vence con el tiempo, sin que nadie toque nada:

| Qué revisa | Qué hace |
|---|---|
| Llamados que no llegaron en su tiempo de gracia (15 minutos) | La primera vez el turno pasa al final de la fila; la segunda, pierde el turno. La fila avanza, se llama al siguiente y el dashboard se actualiza solo. |
| Sesiones del bot sin respuesta (30 minutos) | El bot vuelve al menú y olvida lo que el cliente iba escribiendo. Si la persona nunca aceptó el aviso de datos, se borra también su celular. |
| Mensajes de WhatsApp que no salieron | Se reintentan cada vez más tarde (30 segundos, 1, 2, 5 y 10 minutos) y después se dejan. Un aviso que ya no sirve (por ejemplo, el cliente ya llegó) no se envía. Mientras WhatsApp no esté conectado, esperan en la bandeja de salida. |
| Mensajes de clientes que nadie atendió (más de 1 minuto) | Si el servidor se apagó justo después de recibir un mensaje, el reloj lo atiende y le contesta al cliente. |

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
├── bot/            conversación de WhatsApp: pasos, botones, textos y avisos
├── integraciones/  mensajes que entran y salen de WhatsApp (bandejas y reintentos)
├── web/            dashboard: pantallas, sesión, fila en vivo y administradores
├── reloj/          lo que pasa solo cada 30 segundos (gracias, sesiones del bot, avisos)
├── db/             conexión a la base de datos
├── cli.py          comandos de la terminal (preparar-base, crear-admin, simular-turno, registrar-whatsapp)
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
| `entrar.py`, `fila.py`, `clientes.py`, `configuracion.py`, `textos_del_bot.py`, `mensajes.py` | Las rutas de cada pantalla. Llaman a los casos de uso; no tienen reglas propias. |
| `webhook.py` | La dirección a la que Meta avisa cada mensaje de WhatsApp. No pide la sesión del administrador: la protege la firma de Meta. |
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
| Lo que dice el bot | **Configuración > Textos del bot** para los 38 que más leen los clientes, y **Configuración > Datos personales** para el aviso de datos. Cada texto cambiado es una fila `texto_bot.<nombre>` en la tabla `configuracion`. Los botones y los demás textos, en `app/bot/textos.py`. |
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
| Conversar con el bot sin WhatsApp | <http://localhost:8000/probar-bot> |
| Ver lo que entró y salió por WhatsApp | <http://localhost:8000/mensajes> |
| Registrar el número del bot en WhatsApp (el día de la SIM) | `uv run auto-ibaguer registrar-whatsapp` |
| Ver los mensajes que esperan en la bandeja de salida (dentro de psql) | `SELECT id, tipo, estado, intentos, creado_en FROM mensaje_saliente ORDER BY id DESC LIMIT 10;` |
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
