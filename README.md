# Auto Ibaguer

Sistema de turnos para el lavadero de carros y motos **Auto Ibaguer**:

- **Bot de WhatsApp**: el cliente pide turno, pregunta precios y ve cómo va la fila. Gemini le ayuda a entender lo que la persona escribe con sus propias palabras, y el bot responde también quejas, felicitaciones, preguntas sobre el negocio y la charla.
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
| 7 · Gemini | Construido: el bot entiende lo que el cliente escribe con sus palabras ("cuánto vale lavar la camionetica", "ya no voy a poder ir") y, si Gemini falla o tarda más de 5 segundos, sigue con botones. Probado con un Gemini de mentira. Falta correr las frases de prueba con tu clave: `uv run auto-ibaguer probar-gemini`. |
| 7b · Un bot que responde como una persona | Construido: quejas, felicitaciones, "quiero hablar con una persona" y "cuántas lavadas llevo", que llegan a la pantalla **Comentarios**; las preguntas sobre el negocio se contestan con la ficha del negocio y la charla ("estoy cansado", "te amo") la escribe Gemini, revisada por el bot; los mensajes graves reciben un texto fijo con las líneas de ayuda. Falta: responderle al cliente desde Comentarios por WhatsApp. |
| 8 · Producción | Listo para subir: la imagen de la nube, Render con su base de datos (`render.yaml`), la revisión de salud con el reloj, los avisos de caída por correo y la copia de seguridad diaria, cifrada y fuera de Render. Probado como lo hará Render, en GitHub. Falta crear la cuenta y subirlo: [Poner el sistema en la nube](#poner-el-sistema-en-la-nube). |
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

Para revisar solo el servidor, <http://localhost:8000/salud> debe decir `{"estado":"ok","base_de_datos":"ok","reloj":"ok"}` (sin la parte del reloj si lo apagaste con `RELOJ_ACTIVO=false`). En <http://localhost:8000/docs> FastAPI muestra todas las rutas.

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

En el celular se ve una fila a la vez (pestañas **Carros** y **Motos**) y las secciones quedan en la barra de abajo. Para ver cómo queda en el celular sin salir del computador: en Chrome, `F12` y luego `Ctrl+Shift+M`. Desde el celular de verdad, cuando esté en la nube: [Poner el sistema en la nube](#poner-el-sistema-en-la-nube).

## El bot

El bot conversa con botones y unas pocas palabras; lo que el cliente escribe con sus palabras lo entiende Gemini ([más abajo](#gemini)).

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

**Comentarios**: en el menú (o <http://localhost:8000/comentarios>). Las quejas, felicitaciones, pedidos de hablar con una persona y preguntas que el bot no supo, con el cliente y su último turno. Se marcan como atendidos o se borran (por ejemplo, si quien lo escribió pide que se borre). El número de pendientes sale en el menú y se actualiza solo cada minuto.

**Textos del bot**: en Configuración, botón **Textos del bot** (o <http://localhost:8000/configuracion/textos>). Ahí se cambian los 58 textos que más leen los clientes (el saludo, el turno confirmado, los avisos de la fila...), agrupados como la conversación. Al abrir uno ves el texto, los datos que llena el bot ({numero}, {hora}...) y, al lado, cómo le llega al cliente mientras escribes. Cada texto se guarda por separado y el bot lo usa desde el siguiente mensaje; **Volver al original** lo deja como venía. Antes de guardar se revisa que no le falte un dato que el cliente necesita (por ejemplo, la hora límite del llamado), que no use datos que ese mensaje no tiene y que quepa en WhatsApp. Los botones y los demás textos (errores al escribir una placa, por ejemplo) siguen en `app/bot/textos.py`.

| Archivo de `bot/` | Qué tiene |
|---|---|
| `mensajes.py` | Lo que entra (texto o botón tocado) y lo que sale (texto con hasta 3 botones o una lista), con los límites de WhatsApp. |
| `textos.py` | Todo lo que dice el bot, en un solo lugar: los originales. Si un texto cambiado en Configuración no se puede usar, el bot usa el original. |
| `interprete.py` | Lo que el bot le pide a Gemini: la lista cerrada de intenciones (pedir turno, ver la fila, precios, mi turno, cancelar, pico y placa, mis lavadas, queja, felicitación, hablar con una persona, pregunta, charla, mensaje grave u otra cosa) y tapar los datos personales antes de mandar el mensaje. |
| `redactor.py` | Lo único que se redacta: la respuesta a una pregunta sobre el negocio o a la charla. La ficha que lee Gemini, las reglas con que el bot revisa lo que escribió y cómo se reconoce un mensaje grave. |
| `frases_de_prueba.py` | Las frases con las que se prueba un modelo de Gemini antes de usarlo, con lo que debe entender de cada una. |
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
| `gemini.py` | La API de Gemini: le pide la intención en un formato fijo y, para una pregunta o la charla, la respuesta con la ficha del negocio; espera máximo 5 segundos y decide si se usa (clave, plan, Configuración). |
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
9. **Una dirección pública** para que Meta llegue a tu computador. En otra terminal: `cloudflared tunnel --url http://localhost:8000` (cómo instalarlo, abajo) y copia la dirección `https://….trycloudflare.com` que muestra. Cambia cada vez que lo enciendes. Con el sistema en la nube no hace falta: [El día de la SIM, con el sistema en la nube](#el-día-de-la-sim-con-el-sistema-en-la-nube).
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

## Gemini

Cuando el cliente escribe con sus palabras, Gemini dice qué quiere, de una lista cerrada: pedir turno, ver la fila, precios, mi turno, cancelar, pico y placa, mis lavadas, queja, felicitación, hablar con una persona, pregunta sobre el negocio, charla, mensaje grave u otra cosa. También dice de qué vehículo o servicio habla. Para todo lo que tiene botón, Gemini **no redacta** ni inventa precios, turnos o tiempos: el bot contesta lo mismo que si el cliente hubiera tocado el botón.

Gemini redacta solo en dos casos, y siempre con reglas:

- **Una pregunta sobre el negocio** ("¿reciben Nequi?"): la contesta solo con la ficha del negocio (abajo). Si la ficha no lo dice, no lo inventa: la pregunta te llega a **Comentarios**.
- **La charla** ("estoy cansado", "te amo", "¿eres un robot?"): una respuesta corta y amable que vuelve al lavadero. No coquetea y, si le preguntan, dice que es un asistente virtual.

Antes de mandar lo que escribió, el bot lo revisa con reglas fijas, sin depender de que Gemini obedezca: si trae un precio, un porcentaje, un descuento, un tiempo de espera, un enlace o un número que no estén en la ficha, o dice que es una persona, no sale y el bot usa un texto fijo.

| El cliente escribe | El bot |
|---|---|
| "cuánto vale lavar la camionetica" | Los precios de camioneta. Si dice "carro", pregunta si es automóvil o camioneta. |
| "está muy lleno?" | Cómo está la fila. |
| "ya no voy a poder ir" | Pregunta con botones antes de cancelar: nada se cancela solo. |
| "en la camionetica" (cuando el bot pregunta el vehículo) | Elige Camioneta. Igual con el servicio: "el que trae cera" elige Encerado. |
| "me atendieron mal" | Pide perdón, te avisa en **Comentarios** y deja contar más. |
| "su servicio es muy bueno" | Agradece y te lo pasa a **Comentarios**. |
| "quiero hablar con una persona" | Te avisa en **Comentarios** y le da el teléfono por si es urgente. |
| "cuántas lavadas llevo" | Cuántas lleva y cuándo fue la última, con los datos del sistema. |
| "a qué hora abren el domingo?" | Lo contesta con el horario de Configuración y la ficha del negocio. |
| "estoy cansado" | Una respuesta corta que escribe Gemini, por ejemplo: "Se entiende. Si quieres, nos dejas el carro y descansas mientras lo lavamos." |
| "ya no quiero vivir" | Un texto fijo con la Línea 106 (salud mental, gratis, a toda hora), la Línea Espérame de Bucaramanga (WhatsApp 322 964 3755) y el 123. No pasa por Gemini, no se guarda y no ofrece turnos. |

Las quejas, "hablar con una persona", "cuántas lavadas llevo" y los mensajes graves se reconocen aunque Gemini no esté. De quien no es cliente no se guarda el número sin su permiso: el bot le pregunta con dos botones si quiere que le respondan.

**La ficha del negocio** está en **Configuración > Bot de WhatsApp**: escribe ahí la dirección, las formas de pago, si hay sala de espera, qué no lavan... (hasta 2000 caracteres). Gemini la lee junto con el horario, el teléfono y los servicios de Configuración (sin precios: esos los da el menú). Cuando te llegue una pregunta que el bot no supo, respóndela y agrégala a la ficha: la próxima vez la sabe.

Cuándo **no** se le pregunta a Gemini:

- Cuando el cliente toca un botón, escribe una de las palabras que el bot ya conoce (*mi turno*, *cancelar*, *gracias*...) o solo saluda.
- Cuando el bot pidió un dato (el nombre, la placa, el número de turno): lo que escribe es ese dato.
- Al aceptar el aviso de datos y al confirmar el turno: ahí se responde con los botones o con un sí o un no claros, porque son decisiones que no se adivinan.

Si Gemini no responde en 5 segundos, falla o se acabó la cuota, el bot muestra el menú y sigue con botones; en la terminal queda escrito por qué (una vez cada 5 minutos, nunca el mensaje del cliente). Antes de mandarle un mensaje se tapan las placas, los celulares y los correos (`[placa]`, `[número]`, `[correo]`): Gemini no los necesita para entender qué quiere el cliente.

### Conectar Gemini

1. En [Google AI Studio](https://aistudio.google.com/), en la sección de claves de la API (*API keys*), crea una clave y cópiala en `GEMINI_CLAVE` en tu `.env`. Es secreta, como el token de WhatsApp.
2. Vuelve a encender el servidor. En **Configuración > Bot de WhatsApp** debe decir *Conectado*.
3. Corre las frases de prueba: `uv run auto-ibaguer probar-gemini`. Prueba unas 65 frases como las escribe la gente y dice cuántas entendió bien; a las preguntas y a la charla les muestra, además, lo que contestaría el bot. Si alguna sale MAL, revísala antes de usar ese modelo con clientes.
4. Prueba la charla en **Probar el bot**.

**Plan gratis o de pago.** Con el plan gratis de la API, Google usa los mensajes para mejorar sus productos y personas pueden leerlos; sus propios términos piden no mandar datos personales por ahí. Sirve para probar con mensajes tuyos. Con clientes de verdad hace falta el plan de pago (un proyecto de Google Cloud con facturación activa), donde Google no usa los mensajes. Por eso, en la nube (`ENTORNO=produccion`) el bot solo usa Gemini si pones `GEMINI_PLAN_DE_PAGO=true`; mientras tanto sigue con botones. Los límites del plan gratis (mensajes por minuto y por día) se ven en AI Studio; cada mensaje usa unos pocos cientos de tokens, y una pregunta o la charla, unos mil (son dos peticiones y la segunda lleva la ficha).

**El modelo** se cambia en **Configuración > Bot de WhatsApp**, sin programar (al empezar, `gemini-3.5-flash-lite`, de los más rápidos y baratos de Google). Antes de cambiarlo, pruébalo con las frases: `uv run auto-ibaguer probar-gemini --modelo nombre-del-modelo`. El interruptor **Entender lo que escriben** apaga Gemini si algún día entiende mal.

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

## Poner el sistema en la nube

En la nube el sistema vive en [Render](https://render.com): el servidor y la base de datos quedan encendidos todo el día, con una dirección fija y con HTTPS (`https://….onrender.com`) a la que entran el dashboard, desde cualquier celular, y los mensajes de WhatsApp. Todo está escrito en archivos del repositorio, así Render lo arma igual cada vez:

| Archivo | Qué tiene |
|---|---|
| `Dockerfile` | La *imagen*: el sistema con Python y sus librerías, sin las herramientas de desarrollo. Como un `.jar` que trae todo lo que necesita para correr. |
| `render.yaml` | Lo que Render crea: el servidor (siempre encendido, porque el reloj corre adentro) y la base PostgreSQL 17, con sus variables. |
| `.github/workflows/pruebas.yml` | Además de las pruebas, arma la imagen y la enciende como lo hará Render. Render solo publica un cambio de `main` cuando esto sale en verde. |
| `.github/workflows/respaldo.yml` | La copia de seguridad diaria, cifrada y guardada fuera de Render. |

**Cuánto cuesta:** unos 13 dólares al mes: el servidor 7, la base 6 y 0,30 por su GB de disco. La cuenta de Render (plan *Hobby*) no cuesta; Render le cobra a una tarjeta por los días de uso.

### Subirlo la primera vez

1. Crea la cuenta en Render entrando con tu GitHub y agrega la tarjeta en **Billing**.
2. **New > Blueprint**. Dale acceso a Render solo al repositorio `auto-ibaguer` y elígelo (rama `main`). Render lee `render.yaml` y muestra lo que va a crear: `auto-ibaguer` (el servidor) y `auto-ibaguer-base` (la base).
3. Te pide los secretos de WhatsApp y Gemini. Si todavía no los tienes, déjalos vacíos: el sistema funciona sin ellos y se llenan después ([el día de la SIM](#el-día-de-la-sim-con-el-sistema-en-la-nube)). `CLAVE_SECRETA` la inventa Render.
4. **Apply**. Render crea la base, arma la imagen, prepara las tablas (corre `preparar-base` antes de cada publicación) y enciende el servidor. La primera vez tarda unos minutos; cuando el servidor dice **Live**, está listo.
5. Crea tu usuario del dashboard. En el servidor, pestaña **Shell** (ahí no se escribe `uv run`):

   ```bash
   auto-ibaguer crear-admin --usuario eduar
   auto-ibaguer revisar-base          # debe decir "Tablas: al día"
   ```

6. Abre desde el celular la dirección que Render muestra arriba (`https://auto-ibaguer.onrender.com` o parecida) y entra. `…/salud` debe decir `{"estado":"ok","base_de_datos":"ok","reloj":"ok"}`.
7. **Avisos por correo:** en Render, **Integrations > Notifications**: destino **Email** y **Only failure notifications**. Te llega un correo si una publicación falla o si el servidor deja de estar sano. Render revisa `/salud` todo el tiempo: si la base no responde o el reloj lleva 15 minutos sin dar una vuelta, `/salud` dice que no, Render reinicia el servidor y te avisa.
8. Activa la [copia de seguridad diaria](#copias-de-seguridad).

Desde ese día, **cada cambio que unes a `main` se publica solo**: Render espera a que las pruebas de GitHub salgan en verde, arma la imagen nueva, actualiza las tablas y cambia de versión sin apagar el servicio. Si algo falla (las pruebas, la imagen o las tablas), sigue la versión anterior y te llega el correo. Durante el cambio hay unos segundos con dos servidores encendidos; los mismos bloqueos de la base que evitan choques entre dos celulares evitan que el reloj haga algo dos veces.

Los secretos de la nube no van en un `.env`: van en el servidor de Render, pestaña **Environment**. Al guardar un cambio ahí, Render vuelve a encender el servidor con el valor nuevo. Lo que escribe el servidor (lo que en tu computador sale en la terminal) está en la pestaña **Logs**.

### El día de la SIM, con el sistema en la nube

Son los pasos de [Conectar WhatsApp](#conectar-whatsapp-cuando-llegue-la-sim), con estos cambios:

- Los datos de los pasos 3 a 6 van en **Environment** del servidor de Render, no en el `.env`.
- El paso 7 se corre en la **Shell** del servidor: `auto-ibaguer registrar-whatsapp`.
- En el paso 8, `Reloj: WhatsApp conectado` sale en **Logs**. El paso 9 (el túnel) no hace falta: la URL del webhook del paso 10 es la de Render, `https://auto-ibaguer.onrender.com/webhook/whatsapp` (o la dirección que muestre Render).

Con Gemini, igual: `GEMINI_CLAVE` en **Environment**, y `GEMINI_PLAN_DE_PAGO=true` solo cuando el proyecto de Google tenga facturación.

### Copias de seguridad

| Copia | Dónde queda | Cuánto dura | Para qué |
|---|---|---|---|
| La automática de Render | En Render | Cualquier momento de los últimos 3 días | Deshacer un error reciente, por ejemplo algo que se borró hoy. |
| La diaria de GitHub (`respaldo.yml`) | En GitHub, cifrada | Una por día, por 14 días | Si se pierde la base o la cuenta de Render. |

Para activar la de GitHub, una sola vez:

1. En Render, la base `auto-ibaguer-base` > **Connect** > **External**: copia la *External Database URL*.
2. Inventa la contraseña de las copias: `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`. **Guárdala en tu gestor de contraseñas**: sin ella ninguna copia se puede abrir, y no queda en ningún otro lado.
3. En GitHub, el repositorio > **Settings > Secrets and variables > Actions > New repository secret**: `RESPALDO_DATABASE_URL` con la URL del paso 1 y `RESPALDO_CLAVE` con la contraseña del paso 2.
4. **Actions > Copia de seguridad > Run workflow**. En un par de minutos sale en verde. Abre la corrida: el resumen dice cuánto pesa la copia y qué había en la base de prueba donde se restauró (la versión de las tablas, cuántos clientes y turnos). El archivo está abajo, en **Artifacts**.

Desde ahí corre sola cada madrugada (3:17 a. m.), y si una falla GitHub te manda un correo. GitHub gratis guarda hasta 500 MB entre todas las copias: el resumen avisa cuando ya no van a caber.

**Abrir una copia en tu computador**, para revisar algo o como ensayo (vale la pena hacerlo una vez, antes de necesitarlo):

```bash
# 1. Descarga la copia (Actions > Copia de seguridad > la corrida > Artifacts) y pásala a
#    la carpeta respaldos/ del proyecto, que nunca se sube a GitHub (.gitignore).
mkdir -p respaldos
mv /mnt/c/Users/TU_USUARIO/Downloads/auto-ibaguer-*.dump.gpg respaldos/

# 2. Descifrarla (te pide la contraseña de las copias)
gpg --pinentry-mode loopback --output respaldos/copia.dump --decrypt respaldos/auto-ibaguer-AAAA-MM-DD-HHMM.dump.gpg

# 3. Restaurarla en una base aparte del PostgreSQL de Docker y revisarla
docker compose exec postgres createdb -U auto restaurada
docker compose exec -T postgres pg_restore -U auto -d restaurada --no-owner --no-privileges --exit-on-error < respaldos/copia.dump
uv run auto-ibaguer revisar-base --url postgresql://auto:auto@localhost:5433/restaurada

# 4. Al terminar, bórrala: tiene los datos de los clientes
docker compose exec postgres dropdb -U auto restaurada
rm respaldos/copia.dump
```

**Si se pierde la base de Render:** en el Blueprint, **Manual Sync** crea otra `auto-ibaguer-base`, vacía. Suspende el servidor (**Suspend**: así nadie escribe en la base nueva mientras tanto), descifra la última copia (paso 2 de arriba) y restáurala:

```bash
read -rs URL_DE_RENDER && export URL_DE_RENDER   # pega la External Database URL de la base nueva y Enter (no se ve)
docker run --rm -i -e URL_DE_RENDER -e PGSSLMODE=require postgres:17-alpine \
  sh -c 'exec pg_restore --clean --if-exists --no-owner --no-privileges --single-transaction --dbname="$URL_DE_RENDER"' \
  < respaldos/copia.dump
unset URL_DE_RENDER
```

Después, en el servidor: **Resume**, **Manual Deploy > Deploy latest commit** (así arranca con la dirección de la base nueva) y `auto-ibaguer revisar-base` en su **Shell**. Por último, actualiza el secreto `RESPALDO_DATABASE_URL` de GitHub con la URL nueva.

**Para deshacer algo de los últimos 3 días:** en Render, la base > **Recovery** > el momento que quieres. Render crea una base **nueva** con los datos de ese momento; la actual no se toca. Para que el servidor la use hay que cambiar también `render.yaml`: pide ayuda antes de hacerlo la primera vez.

### Los datos de los clientes en la nube

En la nube, los datos de los clientes (nombre, celular, placas, turnos) quedan en servidores de Estados Unidos: la base en Render (Virginia) y las copias cifradas en GitHub. Los mensajes pasan además por Meta (WhatsApp) y, con el plan de pago, por Google (Gemini, con las placas y los celulares tapados). Antes de abrir al público, pídele a tu abogado que lo revise con la Ley 1581 y que la política de privacidad y el aviso de datos del bot lo digan.

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

GitHub corre lo mismo en cada pull request (pestaña **Actions**) y, además, arma la imagen de la nube y la enciende como lo hará Render. Si sale en rojo, el cambio no se une a `main`.

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
├── db/             conexión a la base de datos y lo que mira revisar-base
├── cli.py          comandos de la terminal (preparar-base, revisar-base, crear-admin, simular-turno, registrar-whatsapp, probar-gemini)
├── config.py       configuración técnica, leída del archivo .env
├── errores.py      el error base de las reglas del negocio
├── formato.py      pesos, horas y fechas a la colombiana ($40.000, 5:30 a.m.)
├── tiempo.py       la hora del sistema y la hora de Bogotá
└── main.py         servidor web
migraciones/        cambios de la base de datos, numerados y en orden (Alembic)
pruebas/            pruebas automáticas
Dockerfile          la imagen de la nube
render.yaml         lo que Render crea: el servidor y la base de datos
.github/workflows/  las pruebas de cada pull request y la copia de seguridad diaria
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
| `cabeceras.py` | Las cabeceras de seguridad de cada respuesta: en la nube, siempre HTTPS, y ninguna otra página puede mostrar el dashboard dentro de la suya. |
| `registro.py` | Que el registro de peticiones del servidor no guarde lo que se busca ni la palabra secreta del webhook. |
| `plantillas/` | El HTML de cada pantalla y diálogo (Jinja2). |
| `estaticos/` | Estilos, el JavaScript de la página, HTMX y las letras del diseño. |

## Dónde se cambia cada cosa

| Qué | Dónde |
|---|---|
| Horario, tiempo de gracia, cuándo avisar, nombre y teléfono del negocio, aviso de datos | Pantalla **Configuración** del dashboard (se guarda en la tabla `configuracion`). No se toca el código. |
| Servicios, precios y puestos | Pantalla **Configuración**. Un precio nuevo apaga el viejo y crea otro en `servicio_precio`; así los cobros pasados no cambian. De la duración estimada de cada servicio dependen la espera de la fila y la alerta de lavado demorado. |
| Lo que dice el bot | **Configuración > Textos del bot** para los 39 que más leen los clientes, y **Configuración > Datos personales** para el aviso de datos. Cada texto cambiado es una fila `texto_bot.<nombre>` en la tabla `configuracion`. Los botones y los demás textos, en `app/bot/textos.py`. |
| Colores, letras y tamaños del dashboard | `app/web/estaticos/css/dashboard.css`. Los colores del diseño están al comienzo, con nombre. |
| Conexión a la base, claves de Meta y Gemini, reloj encendido o apagado | Archivo `.env`, que nunca se sube a GitHub. `.env.example` solo trae los nombres. En la nube, en Render: el servidor > **Environment**. |
| El servidor y la base de la nube (plan, región, versión de PostgreSQL) | `render.yaml`. Render aplica los cambios al unirlos a `main`. |

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
| Probar un modelo de Gemini con las frases de prueba | `uv run auto-ibaguer probar-gemini` (o `--modelo nombre`; `--pausa 0` con el plan de pago) |
| Ver los mensajes que esperan en la bandeja de salida (dentro de psql) | `SELECT id, tipo, estado, intentos, creado_en FROM mensaje_saliente ORDER BY id DESC LIMIT 10;` |
| Ver si las tablas están al día y cuánto hay, sin mostrar datos | `uv run auto-ibaguer revisar-base` (en la Shell de Render, sin `uv run`) |
| Probar la imagen de la nube en tu computador | `docker build -t auto-ibaguer .` y luego `docker run --rm --network host -e ENTORNO=desarrollo auto-ibaguer` |
| Sacar una copia de seguridad ya (antes de un cambio grande) | GitHub > **Actions > Copia de seguridad > Run workflow** |
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
| En Render, una publicación falla en **Pre-deploy** | `preparar-base` no pudo con la base: el motivo está en el registro de esa publicación (**Events**). Sigue funcionando la versión anterior. |
| Te llega el correo de Render de que el servidor no está sano | Abre `https://…/salud`: dice si es la base de datos o el reloj. Render ya reinició el servidor; si sigue igual, mira **Logs** y que la base diga **Available**. |
| Un cambio ya está en `main` y Render no lo publica | Render espera a que GitHub termine las pruebas (pestaña **Actions**); si alguna sale en rojo, no lo publica. |
| La copia de seguridad sale en rojo | Abre la corrida en **Actions**: si no pudo conectarse, revisa `RESPALDO_DATABASE_URL` (cambia si la base de Render se crea de nuevo). |

## Seguridad

- El repositorio es privado y debe seguir así.
- El archivo `.env` nunca se sube. Ahí van las claves de Meta y Gemini de tu computador; las de la nube van en Render (**Environment**) y las de la copia de seguridad, en los secretos de GitHub.
- Nunca compartas el token de Meta ni el PIN de 6 dígitos del número, tampoco en capturas de pantalla.
- Las contraseñas del dashboard se guardan cifradas con Argon2; ni siquiera quien tenga la base de datos puede leerlas.
- La sesión del dashboard vive en una cookie firmada con `CLAVE_SECRETA`. Vence tras `SESION_HORAS` (12) sin usar el dashboard, y se cierra sola si cambian la contraseña o desactivan al usuario.
- En producción, `CLAVE_SECRETA` es obligatoria: el servidor no arranca sin una clave larga y aleatoria. `.env.example` dice cómo generarla.
- Después de 5 contraseñas equivocadas, ese usuario queda bloqueado 15 minutos.
- Ninguna otra página puede enviar formularios al dashboard: el servidor revisa de dónde viene cada envío.
- Lo que el reloj escribe en la terminal no lleva nombres ni celulares: en la nube, esos registros los pueden ver otras personas. El registro de peticiones tampoco guarda lo que va después del `?` en cada dirección: ahí viajan las búsquedas del dashboard y la palabra secreta del webhook.
- En la nube todo va por HTTPS, y el navegador ya no intenta entrar sin él. Ninguna otra página puede mostrar el dashboard dentro de la suya.
- El servidor de la nube corre con un usuario sin permisos de administrador, y la imagen no lleva el `.env`, las pruebas ni la historia de git.
- Las copias de seguridad salen cifradas (AES-256) con una contraseña que solo tú tienes, y nunca se suben al repositorio (`.gitignore`).
