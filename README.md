# Auto Ibaguer

Sistema de turnos para el lavadero de carros y motos **Auto Ibaguer**:

- **Bot de WhatsApp**: el cliente pide turno, pregunta precios y ve cómo va la fila. Gemini le ayuda a entender lo que la persona escribe con sus propias palabras, y el bot responde también quejas, felicitaciones, preguntas sobre el negocio y la charla.
- **Dashboard**: el administrador mueve la fila, pasa vehículos a los puestos, cobra y lleva las cuentas del día, desde el celular, la tablet o el computador.
- **Parqueadero**: entradas y salidas de carros y motos con ticket impreso, cobro por hora, mensualidades y su propia caja, aparte de la del lavadero.
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
| 7c · Gemini lee todo lo que escriben | Parte 1 construida: una sola consulta a Gemini por mensaje (entiende y escribe a la vez: la mitad de cuota y de espera), Gemini en todos los pasos (una pregunta en medio de pedir el turno se contesta y el bot sigue), respaldos distintos según la falla (si el que falló es Gemini, al cliente no se le dice "no te entendí"), la ficha con lo que el sistema sabe (qué vehículos lava, cómo funcionan los turnos, qué se hace con los datos) y, en **Probar el bot**, cómo se decidió cada respuesta. Faltan la parte 2 (ese diagnóstico guardado también para WhatsApp) y la 3 (memoria de más de 24 horas). |
| 7b · Un bot que responde como una persona | Construido: quejas, felicitaciones, "quiero hablar con una persona" y "cuántas lavadas llevo", que llegan a la pantalla **Comentarios**; las preguntas sobre el negocio se contestan con la ficha del negocio y la charla ("estoy cansado", "te amo") la escribe Gemini, revisada por el bot; los mensajes graves reciben un texto fijo con las líneas de ayuda. Desde **Comentarios** se le responde al cliente por WhatsApp, con el bot en pausa mientras tanto. El bot tiene memoria (entiende un "sí porfa" por lo que venían hablando), conversa con naturalidad sin vender a la fuerza, no vuelve a preguntar lo que el cliente ya dijo y no saluda ni se despide siempre igual. Probado de punta a punta en **Probar el bot** y con un WhatsApp de mentira. Falta probarlo con WhatsApp conectado. |
| 8 · Producción | Listo para subir: la imagen de la nube, Render con su base de datos (`render.yaml`), la revisión de salud con el reloj, los avisos de caída por correo y la copia de seguridad diaria, cifrada y fuera de Render. Probado como lo hará Render, en GitHub. Falta crear la cuenta y subirlo: [Poner el sistema en la nube](#poner-el-sistema-en-la-nube). |
| 9 · Piloto en el local | — |
| 10 · Parqueadero | Construido: entradas con ticket, salidas con el cobro por hora y el vuelto, mensualidades (también las que ya venían pagadas), caja con su cierre, tarifas y el archivo para Excel. Probado con pruebas automáticas y en el navegador. Falta probarlo en el local con la impresora de tickets: [El parqueadero](#el-parqueadero). |

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

Abre <http://localhost:8000> en el navegador, entra con el usuario del paso 5 y escoge el negocio: **Lavadero** o **Parqueadero**.

Para revisar solo el servidor, <http://localhost:8000/salud> debe decir `{"estado":"ok","base_de_datos":"ok","reloj":"ok"}` (sin la parte del reloj si lo apagaste con `RELOJ_ACTIVO=false`). En <http://localhost:8000/docs> FastAPI muestra todas las rutas.

## El dashboard

Al entrar con el usuario y la contraseña salen solo dos opciones, **Lavadero** y **Parqueadero**, cada una con lo de hoy para decidir de un vistazo:

- **Lavadero**: cuántos esperan, cuántos se están lavando, cuántos faltan por cobrar, lo cobrado hoy, si el bot está activo y los comentarios por atender.
- **Parqueadero**: cuántos vehículos hay adentro, cuántos salieron hoy, lo recogido hoy (salidas y mensualidades) y si la caja está abierta. Si quedó abierta desde otro día, lo dice en naranja.

Escoges uno y quedas en ese negocio: el menú de arriba (en el celular, la barra de abajo) muestra solo sus secciones, y lo del lavadero (el bot, **Nuevo turno** y los comentarios) no sale en el parqueadero. Para cambiar de negocio, toca su nombre arriba a la izquierda (debajo de "Auto Ibaguer", con las dos flechas).

Las secciones del **Lavadero**:

| Pantalla | Qué se hace ahí |
|---|---|
| **Fila en vivo** | Los puestos, quién espera y lo que falta cobrar. Se actualiza sola en cuanto algo cambia, sin recargar: lo que haga otro celular, el bot o el reloj aparece en menos de un segundo. |
| Botón principal de cada turno | **Marcar llegada** (llamado), **Pasar a puesto** (ya llegó), **Marcar listo** (en el puesto) y **Cobrar** (listo). |
| Menú ⋯ de cada turno | Marcar llegada, corregir datos (tipo de vehículo, placa, servicio) y cancelar. |
| **Nuevo turno** | Para el cliente que llega sin escribir por WhatsApp. Muestra antes qué número le toca y cuánto esperaría. |
| **Caja** | La caja del lavadero, aparte de la del parqueadero y con las mismas partes: se abre con la base para vueltos y **sin la caja abierta no se cobra un turno** (la fila y el diálogo de cobrar lo dicen, con el enlace para abrirla). Cada turno cobrado entra a ella; se ve lo que entró por cada medio y lo que debe haber en el cajón, y se cierra contando el efectivo (dice si cuadró, sobró o faltó, e imprime el resumen). Un cobro equivocado se **corrige** (otro valor u otro medio) o se **deshace** (el turno vuelve a quedar por cobrar), siempre con el motivo; también con la caja cerrada, hasta 30 días después. Cada cobro tiene su botón **Recibo** para volver a imprimirlo. Abajo, los cierres anteriores con filtros y los archivos para Excel (cada turno cobrado, con el precio del menú y el descuento, y los cierres). |
| **Balance** | Lo cobrado por día, semana, mes o los días que escojas (hasta 92): por medio de pago, por servicio y por tipo de vehículo, el promedio por turno, los turnos pedidos (por WhatsApp o en el local), los cancelados y los que no llegaron, cuánto dura un lavado, los descuentos con su motivo, las cajas y sus correcciones. La semana, el mes y el rango traen las **gráficas** día por día y por día de la semana (en plata o en turnos). El del día es el **reporte para los jefes**: por WhatsApp, impreso o por correo (y por correo, solo, al cerrar la caja), sin nombres ni celulares de los clientes. |
| **Clientes** | Buscar por nombre, celular o placa; la ficha con sus vehículos y visitas; dar turno, editar datos y borrarlos si el cliente lo pide (Ley 1581). |
| **Configuración** | Negocio y horario, bot de WhatsApp, puestos (prender, apagar y agregar), menú y precios, y el aviso de datos personales. Se guarda todo junto, y si algo está mal no se guarda nada. Aparte, con su botón, **Recibo y reporte**: el NIT, la dirección y el texto del final del recibo, el ancho del papel, si el recibo se imprime solo al cobrar y los correos de los jefes para el reporte del día. Desde ahí se abren **Textos del bot** (cada texto se guarda por separado) y **Mensajes de WhatsApp**. |
| Lavado **Demorado** | Cuando un lavado pasa su duración estimada, su puesto se pone naranja y arriba se cuenta. En el celular, la pestaña de esa fila muestra un punto naranja aunque estés viendo la otra. Casi siempre quiere decir que ya está listo y falta marcarlo: mientras tanto el cliente no recibe el aviso y la espera de la fila se calcula mal. |

En el celular se ve una fila a la vez (pestañas **Carros** y **Motos**) y las secciones quedan en la barra de abajo. Para ver cómo queda en el celular sin salir del computador: en Chrome, `F12` y luego `Ctrl+Shift+M`. Desde el celular de verdad, cuando esté en la nube: [Poner el sistema en la nube](#poner-el-sistema-en-la-nube).

## El parqueadero

Es el otro negocio del dashboard: se escoge al entrar, o tocando el nombre del negocio arriba a la izquierda. Tiene su propia caja: la plata del parqueadero no se mezcla con la del lavadero. No cuenta puestos libres: eso se ve en el patio.

| Pantalla | Qué se hace ahí |
|---|---|
| **Entradas y salidas** | Registrar la entrada (carro o moto, la placa y, si quieres, el estado o lo que deja: sale en el ticket) e imprimir el ticket. Cobrar la salida escribiendo la placa o el número del ticket, o con **Cobrar** en la lista de los que están adentro, que dice cuánto va cada uno. **Anular** una entrada mal hecha: pide el motivo y no cuenta en la caja. |
| **Caja** | Abrirla con la base para vueltos, ver lo que ha entrado por cada medio de pago y cerrarla contando el efectivo: dice si cuadró, sobró o faltó, e imprime el resumen para firmar. Cada cobro se puede **corregir**, y también la base; en una caja cerrada, hasta 30 días después, además el efectivo contado. Abajo, los **cierres anteriores** con filtros (desde y hasta qué día, cómo cerró: cuadró, sobró, faltó o se corrigió, y quién cerró), sus cuentas y su archivo para Excel, y el archivo de los cobros para Excel. |
| **Mensualidades** | Crear la del cliente (placa, nombre, celular opcional y el valor que acuerdes), cobrar cada mes e imprimir el recibo. Dice quién está al día, a quién se le vence en 3 días o menos y quién debe. |
| **Balance** | Lo que se recogió por día, semana, mes o los días que escojas, por medio de pago, con lo que hay que revisar (descuentos, anuladas, cajas que no cuadraron, correcciones). La semana, el mes y el rango traen **gráficas de barras** día por día y por día de la semana, con filtros. En el del día, el reporte para los jefes: por WhatsApp, impreso o por correo. |
| **Tarifas y ticket** | El valor de la hora de carro y de moto, los minutos de gracia, la fracción de los primeros 15 minutos (opcional), el cobro por días (opcional, con una tabla de cuánto salen 1, 2, 3, 4, 7 y 15 días para responder en el mostrador) y lo que sale impreso: nombre, NIT (en el ticket sale "NIT 900.123.456-7"), dirección, teléfono, el texto del final del ticket y el ancho del papel. También los correos de los jefes para el reporte diario. |

### Cómo cobra

Por horas, con minutos de gracia después de cada hora. Con carro a $3.000, moto a $1.500 y 15 minutos de gracia:

| Tiempo adentro | Horas que paga | Carro | Moto |
|---|---|---|---|
| Hasta 1 h 15 min | 1 | $3.000 | $1.500 |
| De 1 h 16 min a 2 h 15 min | 2 | $6.000 | $3.000 |
| De 2 h 16 min a 3 h 15 min | 3 | $9.000 | $4.500 |

- Cada vehículo paga con la tarifa que había cuando entró: si cambias un precio, los que ya están adentro siguen con el de antes.
- Si llenas la fracción de los primeros 15 minutos, quien sale antes de 15 minutos paga ese valor y no la hora completa.
- Para cobrar otro valor (una noche completa, un descuento), abre **Cobrar otro valor** en la salida: pide el motivo, y el motivo sale en la caja y en el archivo para Excel.
- Si escribes cuánto te dieron en efectivo, dice cuánto devolver y lo pone en el recibo. A la caja entra solo lo cobrado, nunca el billete completo.
- El mensualista al día sale sin pagar y sin necesidad de caja abierta. Si se le venció, paga por horas y el sistema avisa que tiene mensualidad.

### Cobro por días

Para el que deja el vehículo varios días y no debe pagar 96 horas por 4 días. Es opcional y va por tipo, en **Tarifas y ticket > Cobro por días**:

- **Día completo**: lo máximo que paga un día (24 horas). Apenas las horas llegan a ese valor, se cobra el día y no más horas.
- **Desde el 2.º día** (opcional): lo que vale cada día después del primero. Vacío, todos los días valen lo mismo.

Con carro a $3.000 la hora, día completo a $15.000 y desde el 2.º día a $6.600:

| Tiempo adentro | Paga | Cómo sale en el recibo |
|---|---|---|
| 2 horas | $6.000 | 2 horas × $3.000 |
| 6 horas | $15.000 | 1 día: $15.000 |
| 1 día y 2 horas | $21.000 | 1 día: $15.000 + 2 horas × $3.000 |
| 4 días | $34.800 | 4 días: $15.000 + 3 × $6.600 |

- Las horas que pasan de un día se cobran por horas, pero nunca más que un día: si ya lo valen, es un día más.
- Igual que la hora, cada vehículo paga con el cobro por días que había cuando entró. Los que entraron antes de llenarlo siguen por horas.
- Debajo de los valores, una tabla dice cuánto salen 1, 2, 3, 4, 7 y 15 días con lo guardado: para el que pregunta "¿cuánto me cobra por 4 días?".
- **Ojo con el tope**: el decreto de tarifas de Bucaramanga dice que si el vehículo se queda más de 12 horas seguidas, las primeras 24 horas se cobran por horas y el resto por día, proporcional a la mensualidad. Si tu parqueadero es de categoría B, C o D, cada día después del primero no puede pasar de la mensualidad máxima de tu categoría entre 30 (con $200.000, $6.667). Para eso está **Desde el 2.º día**.

**Tarifas máximas**: en Bucaramanga la Alcaldía fija topes por categoría de parqueadero. Revisa que tus valores no pasen el de tu categoría y si te toca cobrar aparte la fracción de los primeros 15 minutos; ambos se cambian en **Tarifas y ticket** sin tocar el código.

### La caja

Solo hay una caja abierta a la vez. Sin caja abierta se pueden registrar entradas, pero no cobrar. Al cerrarla cuentas el efectivo del cajón, incluida la base: debe haber la base más todo lo cobrado en efectivo (salidas y mensualidades), y el sistema dice si cuadró, sobró o faltó. Lo de Nequi, Daviplata y tarjeta se muestra aparte porque no está en el cajón: eso lo comparas con lo que dicen esas apps.

Lo normal es cerrarla al final de cada día y abrir una nueva al otro día, para que cada día cuadre aparte. Si una caja sigue abierta de un día anterior, la pantalla lo avisa en rojo.

Si alguien está cobrando justo cuando otro cierra la caja, el sistema hace esperar a uno de los dos: o el cierre cuenta ese cobro, o el cobro pide abrir caja. Nunca queda un cobro en una caja ya contada.

### Corregir un cobro o una caja

En **Caja > Cobros de esta caja**, cada cobro tiene **Corregir**:

- **Cambiar el valor o el medio de pago**: era Nequi y no efectivo, se escribió $30.000 y eran $3.000.
- **Deshacer la salida**: se le dio salida al carro que no era. Vuelve a la lista de adentro con su ticket, su hora de entrada y su tarifa, y lo cobrado sale de la caja. Si esa placa ya volvió a entrar, no se puede: ahí se cambia el valor.
- **Anular el pago** de una mensualidad: un mes cobrado dos veces o a la mensualidad equivocada. Esos días vuelven a deberse y la plata sale de la caja.

Y la caja misma: **Corregir la base** (en la caja abierta) o **Corregir el conteo o la base** (en una cerrada), para cuando se escribió mal la base con que se abrió o el efectivo que se contó al cerrar.

Las reglas:

- **En la caja de ese cobro: abierta, o cerrada hasta 30 días después del cierre.** Una caja cerrada se corrige desde **Caja > Cierres anteriores > Ver**. Como en contabilidad, no se borra nada: se hace un ajuste con su motivo. Lo que debía haber en el cajón se pone al día y la diferencia del cierre cambia con la corrección. Después de 30 días, esas cuentas ya se entregaron y no se corrigen (el plazo es `DIAS_PARA_CORREGIR_UNA_CAJA_CERRADA`, en `app/parqueadero/reglas.py`).
- **Corregir no es devolver.** Es para un cobro o un conteo que quedó mal escrito. Si hoy hay que devolverle plata a alguien, sale de la caja de hoy y se escribe en la nota del cierre.
- **La caja no se reabre.** Si se cerró antes de tiempo, se abre otra el mismo día: el Balance suma las dos.
- **Siempre con motivo.** Queda anotado cómo estaba, cómo quedó, el motivo, quién lo hizo y a qué hora (tablas `correccion_cobro` y `correccion_de_caja`, que solo crecen: nada se borra de ahí). Lo que se corrigió con la caja ya cerrada queda marcado **Después del cierre**, y la caja dice cómo había quedado al cerrar ("Al cerrar: faltaron $3.000").
- **Se ve en todas partes**: en la caja, en su cierre y en el ticket del cierre, en **Cierres anteriores** (con la marca **Corregida**), en el Balance (**Para revisar**) y en el reporte a los jefes. El reporte del día en que se corrigió trae aparte **Correcciones a cajas de días anteriores**: si alguien cambia un día que ya revisaron, lo ven con nombre y hora.
- Hoy cualquier usuario del sistema puede corregir (todos son administradores). Por eso cada corrección lleva el usuario y sale en el reporte.
- Los días que un mensualista ya traía pagados no son un cobro: no se corrigen aquí.

### Mensualidades

El valor lo pones tú en cada mensualidad, y al cobrar cada mes se puede cambiar. Cada pago cubre 30 días seguidos contando el primero (del 10 de noviembre al 9 de diciembre) y el sistema propone el siguiente periodo justo después del último pagado, aunque pague unos días tarde: si se le venció el 10 y paga el 13, el mes va del 11 al 10 (si no, cada atraso le regalaría días). Si lleva más de un mes sin pagar, el mes empieza el día que paga. En el cobro puedes cambiar las fechas. No deja cobrar dos veces los mismos días.

Cada mensualidad tiene un estado, con su fecha:

| Estado | Cuándo | Qué se puede hacer |
|---|---|---|
| Verde: **Al día hasta el 9/12** | Lo pagado dura más de 3 días | Nada que cobrar: dice desde cuándo toca el próximo cobro ("Próximo cobro desde el 6/12") |
| Naranja: **Vence el 9/12** | Lo pagado se acaba en 3 días o menos | **Cobrar mes** |
| Rojo: **Pendiente de pago** | Ya se acabó lo pagado ("desde el 10/12 · 3 días"), o nunca ha pagado | **Cobrar mes** |

El mes siguiente solo se cobra cuando toca, para que un clic de más no le cobre otro mes. Si un cliente al día quiere adelantar, está **Adelantar mes**: pide marcar que el cliente lo pidió (la regla también está en el código, no solo en el botón).

Cuando una mensualidad queda pendiente de pago, lo ves sin buscarlo:

- En **Entradas y salidas**, arriba y en rojo: cuántas hay pendientes y desde cuándo cada una.
- En **Mensualidades**, primero las pendientes, con una raya roja.
- Si su vehículo entra, el aviso dice que la mensualidad está pendiente, y en la lista de los que están adentro sale **Mensualidad pendiente**.
- Al cobrar su salida, **Cobrar el mes** recibe el pago, imprime el recibo y vuelve a la salida, que ya sale en $0. Si no paga el mes, la salida se cobra por horas.

Al pagar, cambia sola a verde ("Al día hasta el 10/1").

Para los mensualistas que ya traían días pagados, por ejemplo con el dueño anterior, al crear la mensualidad llena **Ya trae pagado hasta**: sale sin pagar hasta ese día, el primer cobro empieza el día siguiente y en la caja no entra plata que no recibiste.

Para guardar el nombre y el celular del titular hay que marcar que él lo autorizó (Ley 1581). Si después pide que se borren, al darle de baja marca **El titular pidió que se borren sus datos**: se borran su nombre, su celular y las notas, y la placa y los pagos se quedan en las cuentas.

### Imprimir los tickets

Sirve cualquier impresora térmica de tickets de 58 u 80 mm que Windows reconozca (por USB, con el driver del fabricante). El ancho del papel se elige en **Tarifas y ticket**.

1. Instala el driver de la impresora y ponla como **predeterminada** en Windows (**Configuración > Bluetooth y dispositivos > Impresoras y escáneres**). Apaga **Permitir que Windows administre mi impresora predeterminada** para que no la cambie solo.
2. Imprime un ticket a mano una vez: en la ventana de Chrome elige la impresora de tickets y, en **Más opciones**, márgenes **Ninguno** y sin **Encabezados y pies de página**.
3. Para que imprima sin preguntar, abre el parqueadero con un acceso directo de Chrome con `--kiosk-printing`. En el escritorio, clic derecho > **Nuevo > Acceso directo**, y en la ubicación:
   ```text
   "C:\Program Files\Google\Chrome\Application\chrome.exe" --kiosk-printing https://tu-direccion/parqueadero
   ```
   Antes de usarlo, cierra todas las ventanas de Chrome: la opción solo se activa si Chrome arranca con ella.

Si la impresora deja mucho papel en blanco después del ticket, en las **Preferencias de impresión** de la impresora busca la opción de cortar al final del documento o de reducir el margen de abajo (cada marca la llama distinto).

Cualquier ticket se puede reimprimir: el botón de la impresora en la lista de los que están adentro, **Recibo** en los pagos de una mensualidad e **Imprimir resumen** en la caja.

El lavadero usa la misma impresora: su recibo lleva el nombre y el teléfono de **Configuración**, y el NIT, la dirección, el texto del final y el ancho del papel de **Configuración > Recibo y reporte**. Con **Imprimir el recibo al cobrar** prendido, sale solo al cobrar cada turno; si no, con el botón **Recibo** de ese cobro en la caja. No lleva el nombre ni el celular del cliente, solo la placa.

### Las cuentas en Excel

En **Caja > Cierres anteriores** filtras por días (los de apertura de la caja, como en el Balance), por cómo cerró (cuadró, sobró, faltó o se corrigió después) y por quién cerró. Arriba de la tabla salen las cuentas de todo lo filtrado, no solo de la página: cuántos cierres, lo que entró y lo que sobró y faltó, cada cosa aparte. Van de a 30 por página, y **Descargar estos cierres para Excel** baja todos los filtrados, uno por fila: cuánto entró por cada medio, lo que debía haber, lo que se contó, la diferencia y si se corrigió después.

En **Caja > Llevar las cuentas en Excel** eliges desde y hasta qué día y descargas un archivo con cada cobro (salidas y mensualidades): fecha, hora, placa, tipo, tiempo, medio de pago, valor, ajuste y caja. Excel lo abre directo con doble clic, con tildes y columnas en su sitio. Desde **Balance** se descarga el día, la semana, el mes o el rango que estás mirando.

### El balance: día, semana, mes o rango

En **Parqueadero > Balance** eliges **Día**, **Semana** (de lunes a domingo), **Mes** o **Rango** (los días que escojas, hasta 92, con atajos a los últimos 30 y 90), y con las flechas pasas al anterior o al siguiente. Muestra:

- **Recogido**: todo lo que entró (salidas y mensualidades), comparado con el periodo anterior. Si estás mirando hoy, la comparación es con ayer completo, porque hoy va por la mitad.
- **Por medio de pago**: el efectivo es lo que debió entrar al cajón; Nequi, Daviplata y tarjeta se comparan con lo que dicen sus apps.
- **Lo que pasó**: salidas de carros y de motos, mensualistas al día que salieron sin pagar, descuentos, entradas anuladas y si las cajas cuadraron.
- **Cómo va** (en semana, mes y rango): las gráficas. Ver lo que dicen está más abajo.
- **Día por día** (en semana, mes y rango): la misma información en una tabla. Cada día lleva a su balance.
- **Cajas**: cada caja es del día en que se abrió, aunque se cierre pasada la medianoche.
- **Para revisar**: cada cobro distinto a la tarifa y cada entrada anulada, con su motivo.

La plata cuenta el día en que se cobró, en la hora de Bogotá: una salida a las 11:30 p.m. es de ese día. Los días que un mensualista ya traía pagados (con el dueño anterior) no son plata de este sistema y no suman.

#### Las gráficas

Para saber qué días son flojos y cuáles buenos, y reforzar los flojos:

- **Día por día**: una barra por día. Marca el mejor día y el más bajo, y una línea con el promedio. Hoy va más clara: el día va por la mitad y no cuenta para el promedio.
- **Promedio por día de la semana**: los lunes, los martes... Sale con dos semanas o más de días completos. Para eso sirve el **Rango**: con los últimos 90 días, cada día de la semana tiene unas trece muestras.
- **Qué ver**: **Todo lo recogido**, solo **Salidas** (sin mensualidades, que llegan de a montones el día que se pagan y hacen ver bueno un día que no lo fue) o **Vehículos** (los que entraron). Y de **Todos**, solo **Carros** o solo **Motos**. La tabla **Día por día** sigue el mismo filtro de vehículos.
- **Al pasar el puntero** (o con el teclado) por una barra, dice el valor, el día y cómo se compone. Tocar una barra del día por día abre el balance de ese día.
- Los días antes del primero con movimiento no cuentan para comparar: no son días flojos, el sistema todavía no se usaba.
- Las gráficas son HTML hecho en el servidor (`app/caja/graficas.py` calcula cada barra para los dos negocios; `_grafica.html` la pinta): no hay librería de gráficas, y la letra se lee igual en el celular.

### Reporte diario a los jefes

Es el balance del día escrito para leerlo en el celular: lo que se recogió y por qué medio, salidas, mensualidades y entradas, los descuentos y las anuladas con su motivo, cómo cerró la caja, cuántos vehículos quedan adentro y qué mensualidades están pendientes de pago. Lleva placas y plata, nunca nombres ni celulares de los clientes. Sale de tres formas, en **Balance > Día**:

- **Enviar por WhatsApp**: abre WhatsApp con el reporte ya escrito; eliges el chat de los jefes y lo envías. Funciona desde el primer día, sin configurar nada.
- **Imprimir**: en la impresora de tickets, con una raya para firmar.
- **Por correo, solo**: al cerrar la caja les llega el reporte del día de esa caja (si la cierras pasada la medianoche, sigue siendo el del día en que se abrió). También está **Enviar por correo ahora**, para mandarlo a mano o probar. Si el correo falla, la caja igual se cierra y el error queda en **Logs**.

El lavadero tiene el suyo, con el mismo orden (lo cobrado y por qué medio, los turnos, los cobros distintos al menú y la caja), en su **Balance > Día**. Sus correos van en **Configuración > Recibo y reporte** y le llega al cerrar la caja del lavadero. El correo del sistema (los pasos de abajo) es uno solo para los dos negocios.

Para el correo:

1. Escribe los correos de los jefes en **Tarifas y ticket > Reporte diario a los jefes** (hasta 5, separados por coma). Uno mal escrito no se guarda: mejor saberlo hoy que descubrir en un mes que nunca llegó.
2. Crea un Gmail para el negocio (no uses el tuyo personal) y sácale una **contraseña de aplicación**: en [myaccount.google.com](https://myaccount.google.com) > **Seguridad** activa la **Verificación en 2 pasos**; después busca **Contraseñas de aplicación**, escribe un nombre ("Auto Ibaguer") y copia las 16 letras que muestra. No es la contraseña normal del correo, y es secreta como las de WhatsApp.
3. En Render, el servidor > **Environment** (en tu computador, en el `.env`):

   | Variable | Valor |
   |---|---|
   | `CORREO_SERVIDOR` | `smtp.gmail.com` |
   | `CORREO_PUERTO` | `465` |
   | `CORREO_USUARIO` | el Gmail del negocio |
   | `CORREO_CLAVE` | las 16 letras de la contraseña de aplicación |

4. En **Balance**, **Enviar por correo ahora**: si dice "Reporte enviado", ya quedó.

**Ojo con el plan gratis de Render**: bloquea el correo (los puertos 25, 465 y 587). Ahí el botón de correo dice que no se pudo conectar; mientras tanto, manda el reporte por WhatsApp. En el plan de pago sale normal.

Para probar el correo en tu computador sin mandar nada de verdad, enciende un servidor de correo de mentira que muestra en la terminal lo que le llega, y pon en el `.env` `CORREO_SERVIDOR=localhost` y `CORREO_PUERTO=1025` (sin usuario ni clave):

```bash
uvx --from aiosmtpd python -m aiosmtpd -n -l localhost:1025
```

La contraseña del correo nunca viaja sin cifrar: con usuario y clave, el sistema solo manda por una conexión cifrada (puerto 465, o 587 con STARTTLS).

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

**Probar el bot**: en Configuración, botón **Probar el bot** (o <http://localhost:8000/probar-bot>). Es la misma conversación que tendrá WhatsApp, con los mismos botones, y los avisos de la fila (te llamamos, tu vehículo está listo...) llegan a la charla. Lo que pidas ahí entra a la fila de verdad de tu computador. Lo que escribes cuenta como si hubiera llegado por WhatsApp (para las 24 horas y para la conversación de Comentarios), y lo que le respondas desde **Comentarios** llega a la charla. Nada de eso sale por WhatsApp: a un celular de prueba (los que empiezan por 5799) nunca se le escribe. Solo existe en desarrollo.

**Comentarios**: en el menú (o <http://localhost:8000/comentarios>). Las quejas, felicitaciones, pedidos de hablar con una persona y preguntas que el bot no supo, con el cliente y su último turno. Se marcan como atendidos o se borran (por ejemplo, si quien lo escribió pide que se borre). El número de pendientes sale en el menú y se actualiza solo cada minuto.

**Responderle por WhatsApp**: si dejó su celular, el botón **Responder por WhatsApp** abre la conversación (lo que escribió, lo que le contestó el bot y lo que le escribiste tú, al día cada 10 segundos) y le escribes desde ahí. El primer mensaje ya trae con qué empezar ("Hola Ana, te habla Eduar, de Auto Ibaguer."). Sale por la bandeja de salida, con sus reintentos, y el comentario queda atendido. WhatsApp solo deja escribirle hasta 24 horas después de su último mensaje; después, llámalo.

Mientras le respondes, el bot hace una pausa de una hora con ese cliente (se alarga con cada mensaje tuyo), para no meterse en la conversación:

| El cliente | El bot |
|---|---|
| Te contesta ("fue en el capó"), manda una foto o dice "gracias" | No contesta. Lo ves en la conversación y el comentario vuelve a **por atender**. |
| Escribe algo de la fila en un mensaje corto ("ya llegué", "voy en camino", "mi turno") o escanea el QR | Lo atiende: la fila no espera. |
| Toca un botón o va en algo con el bot (tocó *Pedir turno* y el bot le pregunta la placa) | Lo atiende. |
| Escribe un mensaje grave | Le manda las líneas de ayuda, y el comentario vuelve a **por atender** para que lo veas. |

**Devolver al bot** termina la pausa antes. Si borras el comentario, la pausa también termina.

**Textos del bot**: en Configuración, botón **Textos del bot** (o <http://localhost:8000/configuracion/textos>). Ahí se cambian los 61 textos que más leen los clientes (el saludo, el turno confirmado, los avisos de la fila...), agrupados como la conversación. Al abrir uno ves el texto, los datos que llena el bot ({numero}, {hora}...) y, al lado, cómo le llega al cliente mientras escribes. Cada texto se guarda por separado y el bot lo usa desde el siguiente mensaje; **Volver al original** lo deja como venía. Antes de guardar se revisa que no le falte un dato que el cliente necesita (por ejemplo, la hora límite del llamado), que no use datos que ese mensaje no tiene y que quepa en WhatsApp. Los botones y los demás textos (errores al escribir una placa, por ejemplo) siguen en `app/bot/textos.py`. Con Gemini conectado, los saludos, los agradecimientos y las despedidas el bot los dice con sus palabras, según la conversación, para no repetirse (el editor lo avisa); los demás, como los turnos, los precios y los avisos, salen siempre tal cual.

| Archivo de `bot/` | Qué tiene |
|---|---|
| `mensajes.py` | Lo que entra (texto o botón tocado) y lo que sale (texto con hasta 3 botones o una lista), con los límites de WhatsApp. |
| `textos.py` | Todo lo que dice el bot, en un solo lugar: los originales. Si un texto cambiado en Configuración no se puede usar, el bot usa el original. |
| `interprete.py` | Lo que el bot le pide a Gemini en una sola consulta: la lista cerrada de intenciones, lo que Gemini recibe con el mensaje (la ficha, la memoria, en qué va, las guías) y lo que devuelve (qué entendió y, si toca conversar, lo que escribió). También tapar los datos personales antes de mandar el mensaje. |
| `redactor.py` | La ficha que lee Gemini (lo que dice el sistema y lo que escribió el administrador), las reglas con que el bot revisa lo que escribió (y por qué no sirve, para la traza) y cómo se reconoce un mensaje grave. |
| `frases_de_prueba.py` | Las frases con las que se prueba un modelo de Gemini antes de usarlo, con lo que debe entender de cada una. |
| `textos_editables.py` | Los textos que se cambian en Configuración: de qué datos se arma cada uno, cuáles no se pueden quitar, cuánto puede medir, la vista previa y guardarlos. |
| `conversacion.py` | Qué contesta a cada mensaje y en qué paso va cada cliente. Llama a los mismos casos de uso que el dashboard. |
| `avisos.py` | Cómo dice el bot cada aviso de la fila. |
| `sesiones.py` | Las conversaciones sin respuesta que vencen (las limpia el reloj). |
| `pausa.py` | Con quién está en pausa el bot porque le responde una persona, y hasta cuándo. Lo que hace el bot durante la pausa está en `conversacion.py`. |

## WhatsApp

Así viaja cada mensaje:

1. El cliente escribe y Meta le avisa al servidor en `/webhook/whatsapp` (el *webhook*).
2. El servidor revisa la firma de Meta (si el aviso no viene de Meta, lo descarta), guarda el mensaje y le contesta a Meta de una vez. Si Meta manda el mismo mensaje dos veces, el repetido se ignora.
3. Enseguida, aparte, el bot lo atiende: decide qué contestar y deja las respuestas en la bandeja de salida, en la misma transacción de lo que hizo (pedir el turno, cancelarlo...). Los mensajes de un mismo cliente se atienden de a uno y en orden.
4. Las respuestas salen por la API de WhatsApp. Si WhatsApp falla un momento, el reloj las reintenta, y a cada cliente sus mensajes le llegan en el orden en que el bot los escribió.

Los avisos de la fila (te llamamos, tu vehículo está listo...) y lo que le escribes al cliente desde **Comentarios** salen por el mismo camino. WhatsApp solo deja escribirle libremente a quien le escribió al negocio en las últimas 24 horas; si no, el aviso no sale y queda anotado por qué. Si el servidor se apaga a mitad de camino no se pierde nada: lo que se guardó se atiende o se envía cuando vuelve.

La pantalla **Mensajes de WhatsApp** (en Configuración, o <http://localhost:8000/mensajes>) muestra lo último que entró y salió, si WhatsApp está conectado y, cuando un mensaje no salió, por qué. El celular se ve a medias (300 \*\*\* 4567).

| Archivo de `integraciones/` | Qué tiene |
|---|---|
| `whatsapp.py` | La API de WhatsApp de Meta: la firma, leer lo que llega y enviar (texto, botones o lista). Sabe qué errores de Meta vale la pena reintentar. |
| `gemini.py` | La API de Gemini: una sola petición por mensaje, con las reglas aparte de los datos y la respuesta en un formato fijo; espera máximo 5 segundos, repite una vez si Google tuvo un problema pasajero, dice por qué falló (cuota del minuto o del día, clave, modelo...) y decide si se usa (clave, plan, Configuración). |
| `bandeja_entrada.py` | Guarda cada mensaje que llega y lo atiende con el bot, de a uno por cliente. |
| `bandeja_salida.py` | Deja cada mensaje por enviar y lo envía con reintentos, en orden para cada cliente. |
| `historial.py` | Lo que se han dicho con un celular, de las dos bandejas y en orden: la conversación de **Comentarios** y la memoria del bot. |
| `tablas.py` | Las dos bandejas: `mensaje_entrante` y `mensaje_saliente`. |

Es lo que en sistemas grandes se llama *inbox* y *outbox*: primero se guarda y después se atiende o se envía, como cuando en Java se separa recibir de procesar con una cola.

### Conectar WhatsApp (cuando llegue la SIM)

Se hace en tu app de [Meta for Developers](https://developers.facebook.com/apps) y en el [Administrador de WhatsApp](https://business.facebook.com/wa/manage/). Cada dato va en tu `.env`, que nunca se sube a GitHub. El token, la clave secreta y el PIN no se pegan en ningún chat ni salen en capturas.

1. **Agrega el número.** Pon la SIM en un celular; ese número no puede tener WhatsApp instalado (si lo tiene, borra primero esa cuenta de WhatsApp). En el Administrador de WhatsApp: *Números de teléfono > Agregar número*. Escribe el nombre que verán los clientes (Auto Ibaguer) y confirma con el código que llega por SMS o por llamada.
2. **Agrega un método de pago** a la cuenta de WhatsApp (en la *Configuración del negocio*, la cuenta de WhatsApp > *Pagos*): sin él, Meta no entrega los mensajes.
3. **Los identificadores:** en la app, *WhatsApp > Configuración de la API* (*API Setup*), elige el número nuevo y copia su *identificador del número de teléfono* en `WHATSAPP_ID_NUMERO`, y el *identificador de la cuenta de WhatsApp Business* (sale encima del número) en `WHATSAPP_ID_CUENTA`.
4. **Un token que no vence:** en la *Configuración del negocio > Usuarios del sistema*, crea uno con rol de administrador, asígnale la app y la cuenta de WhatsApp, y genera un token sin vencimiento con los permisos `whatsapp_business_messaging` y `whatsapp_business_management`. Cópialo en `WHATSAPP_TOKEN`: es la llave para enviar mensajes a nombre del negocio.
5. **La clave secreta de la app:** en la app, *Configuración de la app > Básica > Clave secreta de la app*. Cópiala en `WHATSAPP_SECRETO_APP`. Con ella el servidor sabe que un aviso viene de Meta.
6. **Un token de verificación**, inventado por ti: `python3 -c "import secrets; print(secrets.token_urlsafe(24))"` y lo copias en `WHATSAPP_TOKEN_VERIFICACION`.
7. **Enlaza la app y registra el número** con un PIN de 6 dígitos que inventas (la verificación en dos pasos de WhatsApp). Meta solo deja hacerlo por la API:

   ```bash
   uv run auto-ibaguer registrar-whatsapp
   ```

   Primero enlaza la app con la cuenta de WhatsApp Business: Meta muchas veces no lo hace sola, y sin el enlace los mensajes llegan al celular pero nunca al servidor (aunque el botón de prueba de Meta funcione). Dice con qué app quedó enlazada. Después te pide el PIN sin mostrarlo y no lo guarda. Anótalo en un lugar seguro: WhatsApp lo vuelve a pedir si el número se registra otra vez. El comando se puede correr otra vez, con el mismo PIN, sin dañar nada.
8. **Enciende el servidor** (`uv run fastapi dev app/main.py`; si ya estaba encendido, apágalo con Ctrl+C y vuelve a encenderlo, porque el `.env` se lee al arrancar). La terminal debe decir `Reloj: WhatsApp conectado` y la pantalla **Mensajes de WhatsApp**, que está conectado.
9. **Una dirección pública** para que Meta llegue a tu computador. En otra terminal: `cloudflared tunnel --url http://localhost:8000` (cómo instalarlo, abajo) y copia la dirección `https://….trycloudflare.com` que muestra. Cambia cada vez que lo enciendes. Con el sistema en la nube no hace falta: [El día de la SIM, con el sistema en la nube](#el-día-de-la-sim-con-el-sistema-en-la-nube).
10. **Registra el webhook:** en la app, *WhatsApp > Configuración*: URL de devolución de llamada `https://….trycloudflare.com/webhook/whatsapp` y el token de verificación del paso 6; *Verificar y guardar*. Después, en *Campos del webhook*, suscríbete a **messages**.
11. **Pon la app en modo Live** (arriba, en el panel de la app): en modo de desarrollo Meta no manda todos los avisos ("some webhooks will not be sent if your app is in Dev mode"). Para pasarla, Meta pide la dirección pública de una política de privacidad: llena los datos que faltan en la política (razón social, NIT, dirección, correo), cópiala a Google Docs, publícala con *Archivo > Compartir > Publicar en la Web* y pega esa dirección en *Configuración de la app > Básica > URL de la política de privacidad*. Si Meta también pide las *instrucciones para eliminar datos*, sirve la misma dirección: la política tiene esa parte. Antes de abrir al público, que la revise tu abogado.
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
| El botón de prueba de Meta llega, pero los mensajes de verdad no | La cuenta de WhatsApp no está enlazada con la app: corre otra vez `uv run auto-ibaguer registrar-whatsapp` y mira con qué app dice que quedó enlazada. |
| Llegan los mensajes pero las respuestas dicen *No salió* | El motivo sale debajo del mensaje. `error 190`: el token venció o no es ese; `error 131042`: falta el método de pago o tiene un problema; `error 131047`: el cliente no ha escrito en 24 horas. |

## Gemini

Cuando el cliente escribe con sus palabras, el bot le hace **una sola consulta** a Gemini por mensaje, y en esa consulta Gemini hace dos cosas: dice qué quiere el cliente, de una lista cerrada (saludo, despedida, pedir turno, ver la fila, precios, mi turno, cancelar, ya llegué, voy en camino, pico y placa, mis lavadas, queja, felicitación, hablar con una persona, pregunta sobre el negocio, charla, mensaje grave u otra cosa), y, si toca conversar, escribe la respuesta. También dice de qué vehículo o servicio habla (o si es uno que el lavadero no tiene, como una tractomula) y si contesta un sí o un no. Es como un mesero que en un solo viaje a la cocina lleva el pedido y vuelve con lo que hay que decir: antes eran dos viajes, y con el plan gratis la cocina se saturaba.

Para todo lo que tiene botón, Gemini **no redacta** ni inventa precios, turnos o tiempos: el bot contesta lo mismo que si el cliente hubiera tocado el botón, con los datos del sistema. Gemini escribe solo la conversación, y siempre con reglas:

- **Una pregunta sobre el negocio** ("¿reciben Nequi?", "¿lavan tractomulas?"): la contesta solo con la ficha del negocio (abajo). Si la ficha no lo dice, no lo inventa: dice lo que sí sabe que tenga que ver y la pregunta te llega a **Comentarios**.
- **La charla** ("estoy cansado", "te amo", "¿viste el partido?"): conversa con naturalidad y no ofrece turnos a la fuerza (después de la charla no le pega el menú). No coquetea y, si le preguntan, dice que es un asistente virtual.
- **Saludos, despedidas y felicitaciones**: los dice con sus palabras (abajo).
- **Lo que no se entiende** ("asdfgh"): le pide, corto y amable, que se lo explique, con el menú debajo.

**En cualquier paso.** Si el cliente pregunta algo o conversa en medio de pedir el turno ("¿para qué necesitan la placa?", "por cierto, ¿cómo estuvo el clima?"), Gemini le contesta y el bot vuelve a hacer su pregunta: el turno no se pierde. Gemini sabe en qué va la conversación (qué le está preguntando el bot), así entiende un "la moto" o un "dale" como respuesta.

**Con memoria.** Para entender el mensaje nuevo y para escribir, Gemini ve lo último que se dijeron: hasta 10 mensajes de las últimas 24 horas, con los datos personales tapados (lo del cliente, lo del bot, los avisos de la fila y lo que le escribiste desde **Comentarios**). Así un "sí porfa" después de "¿te aparto un turno?" pide el turno, un "y para la camioneta?" después de los precios de la moto muestra los de camioneta, y no repite lo que ya dijo. No guarda nada aparte: lo lee de las bandejas de WhatsApp.

**Cómo habla** (las instrucciones de Gemini, en `app/integraciones/gemini.py`): corto, como en WhatsApp; natural y sencillo, tuteando y al tono del cliente; sin muletillas como "¿En qué te puedo ayudar?" ni repetir cómo empezó el mensaje anterior; una pregunta a la vez; un emoji como mucho y humor ligero cuando encaja; y sin convertir cada charla en una venta.

**Otros temas**: conversa de lo cotidiano (cómo está, el partido, una broma), pero no se vuelve un asistente para todo: tareas, recetas, noticias, explicaciones largas o consejos de salud, leyes o plata los declina con amabilidad. Desde el 15 de enero de 2026, Meta prohíbe los chatbots de propósito general en la API de WhatsApp Business; un bot que atiende a los clientes de un negocio sí está permitido, y así se queda.

**Lo que ya dijo no se pregunta otra vez.** Si escribe "quiero lavar la moto ABC12D", el bot no le vuelve a preguntar el vehículo ni la placa: si ya tiene esa moto, la usa; si no, la anota como nueva. Si nombra el servicio ("quiero un general para la camioneta"), va directo al resumen. Si acaba de ver los precios de moto y toca *Pedir turno*, se entiende que es para la moto. Todo queda en el resumen, con *Cambiar datos*.

**Saludos, agradecimientos y despedidas** (`CON_SUS_PALABRAS` en `app/bot/textos.py`): con Gemini, el bot los dice con sus palabras según la conversación, con la misma idea de tu texto. Si Gemini no responde o lo que dice rompe una regla, sale tu texto tal cual. Lo que lleva datos que cuidar (los turnos, los precios, la fila, los avisos, el aviso de datos y las quejas) sale siempre tal cual.

Antes de mandar lo que escribió, el bot lo revisa con reglas fijas, sin depender de que Gemini obedezca: si trae un precio, un porcentaje, un descuento, un tiempo de espera, una hora, un enlace o un número que no estén en la ficha (o en tu texto, si lo está diciendo con sus palabras), habla de festivos sin que la ficha lo diga, dice que es una persona o que apartó, confirmó o canceló un turno (eso solo lo hace el bot), no sale y el bot usa un texto fijo. Con más de un emoji, se los quita.

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
| "estoy cansado" | Una respuesta corta que escribe Gemini, por ejemplo: "Uy, se nota que fue un día largo. ¿Te tocó trabajar mucho?" |
| "sí porfa" (después de "¿te aparto un turno?") | Empieza a pedir el turno: lo entiende por lo que venían hablando. |
| "me ayudas con una tarea?" | Le dice con amabilidad que por aquí no puede: el número es del lavadero. |
| "quiero lavar la moto ABC12D" | Pide el turno sin volver a preguntar el vehículo ni la placa. |
| "gracias" | Se despide con sus palabras, según lo que venían hablando. |
| "puedes lavar camiones o tractomulas??" | Le dice qué vehículos sí lava. Si pregunta el precio, no lo inventa. |
| "¿puedo cancelar con Nequi?" | Lo contesta como una pregunta de pagos: en Colombia, cancelar también es pagar. No toca su turno. |
| "gracias, y abren mañana?" | Contesta la pregunta: no es una despedida. |
| "¿para qué necesitan la placa?" (cuando el bot la pide) | Le explica y vuelve a pedir la placa. |
| "ya no quiero vivir" | Un texto fijo con la Línea 106 (salud mental, gratis, a toda hora), la Línea Espérame de Bucaramanga (WhatsApp 322 964 3755) y el 123. No pasa por Gemini, no se guarda y no ofrece turnos. |

Las quejas, "hablar con una persona", "cuántas lavadas llevo" y los mensajes graves se reconocen aunque Gemini no esté. De quien no es cliente no se guarda el número sin su permiso: el bot le pregunta con dos botones si quiere que le respondan.

**La ficha del negocio** está en **Configuración > Bot de WhatsApp**: escribe ahí la dirección, las formas de pago, si hay sala de espera, qué no lavan... (hasta 2000 caracteres). Gemini la lee junto con lo que dice el sistema: el horario (qué días abre y cuáles no), cómo funcionan los turnos (son del mismo día, en orden de fila, con los minutos para llegar cuando lo llaman), el teléfono, qué vehículos lava (y que para otros no hay servicios), qué incluye cada servicio (sin precios: esos los da el menú) y el aviso de datos de Configuración (para contestar "¿guardan mi número?"). **Mientras la ficha esté vacía, casi todas las preguntas terminan en Comentarios.** Cuando te llegue una pregunta que el bot no supo, respóndela y agrégala a la ficha: la próxima vez la sabe.

Cuándo **no** se le pregunta a Gemini (no hace falta, y así no gasta cuota):

- Cuando el cliente toca un botón o escribe el nombre de uno.
- Cuando escribe una orden corta que el bot ya conoce: *mi turno*, *cancelar mi turno*, *ya llegué*, *voy en camino*, *un asesor*, *mis lavadas*. Si el mensaje dice más cosas, sí lo lee Gemini: "¿puedo cancelar con Nequi?" no es cancelar el turno.
- Cuando el bot pidió un dato y lo que escribe es ese dato: un nombre (también "me llamo Ana"), una placa, un número de turno. Si en vez del dato pregunta algo ("¿para qué?"), lo lee Gemini.
- Cuando contesta con un sí o un no claros ("sí porfa", "de una", "no gracias").

**Las decisiones que no se deshacen no se adivinan**: aceptar el aviso de datos, dar permiso para guardar el número y cancelar un turno necesitan el botón o un sí claro; si Gemini cree que el cliente dijo que sí, el bot le pide tocar el botón. Un no, en cambio, siempre vale. Confirmar el turno sí lo puede decidir lo que entienda Gemini ("hágale pues, resérvemelo"): se puede cancelar después.

**Si Gemini no responde** (no contestó en 5 segundos, se acabó la cuota, Google tuvo un problema...), el bot sigue solo con botones y las palabras que reconoce, y al cliente **no** le dice "no te entendí": le pide intentar de nuevo en un momento (texto *Cuando Gemini no responde*), porque el mensaje puede estar perfectamente escrito. Si Google tuvo un problema pasajero (un error 5xx o se cayó la conexión), se intenta una vez más. "No te entendí bien" sale solo cuando Gemini está apagado o de verdad no se entendió. En la terminal queda escrito por qué (una vez cada 5 minutos, nunca el mensaje del cliente), y en **Probar el bot** cada respuesta trae debajo, en gris, cómo se decidió: si le preguntó a Gemini, qué entendió, cuánto tardó y, si salió un texto fijo, por qué (por ejemplo, *Gemini no respondió: llegó al límite de consultas por minuto (429)*). Eso no le llega al cliente.

Antes de mandarle un mensaje (y lo que se venían diciendo) se tapan las placas, los celulares y los correos (`[placa]`, `[número]`, `[correo]`): Gemini no los necesita para entender qué quiere el cliente. Las reglas van aparte de los datos: la ficha, la conversación y el mensaje del cliente van marcados como datos, para que lo que alguien escriba no cambie las instrucciones.

### Conectar Gemini

1. En [Google AI Studio](https://aistudio.google.com/), en la sección de claves de la API (*API keys*), crea una clave y cópiala en `GEMINI_CLAVE` en tu `.env`. Es secreta, como el token de WhatsApp.
2. Vuelve a encender el servidor. En **Configuración > Bot de WhatsApp** debe decir *Conectado*.
3. Corre las frases de prueba: `uv run auto-ibaguer probar-gemini`. Prueba 95 frases como las escribe la gente, con una consulta cada una (como en WhatsApp), y dice cuántas entendió bien; las que dicen *(con memoria)* solo se entienden por lo que se venían diciendo ("sí porfa"), las que dicen *(en medio de una pregunta del bot)* se escriben mientras el bot pide un dato, y las que traen una letra (*[A]*, *[B]*...) son las pruebas del documento del hito 7c. Debajo de cada una muestra lo que contestaría el bot. Si alguna sale MAL, revísala antes de usar ese modelo con clientes. Con el plan gratis espera 6 segundos entre frase y frase (unos 10 minutos en total) para no pasarse del límite por minuto; si salen frases con *429*, es la cuota: espera un rato.
4. Prueba la charla en **Probar el bot**.

**Plan gratis o de pago.** Con el plan gratis de la API, Google usa los mensajes para mejorar sus productos y personas pueden leerlos; sus propios términos piden no mandar datos personales por ahí. Sirve para probar con mensajes tuyos. Con clientes de verdad hace falta el plan de pago (un proyecto de Google Cloud con facturación activa), donde Google no usa los mensajes. Por eso, en la nube (`ENTORNO=produccion`) el bot solo usa Gemini si pones `GEMINI_PLAN_DE_PAGO=true`; mientras tanto sigue con botones. Los límites del plan gratis (consultas por minuto y por día) se ven en AI Studio; son pocos (del orden de 10 por minuto), así que si escribes muy seguido en **Probar el bot** algunas respuestas salen sin Gemini, y la traza lo dice. Cada mensaje es una consulta de unos dos o tres mil tokens (las instrucciones, la ficha y la memoria).

**El modelo** se cambia en **Configuración > Bot de WhatsApp**, sin programar (al empezar, `gemini-3.5-flash-lite`, de los más rápidos y baratos de Google). Antes de cambiarlo, pruébalo con las frases: `uv run auto-ibaguer probar-gemini --modelo nombre-del-modelo`. El interruptor **Entender lo que escriben** apaga Gemini si algún día entiende mal.

## El reloj

El servidor trae un reloj que cada 30 segundos revisa lo que vence con el tiempo, sin que nadie toque nada:

| Qué revisa | Qué hace |
|---|---|
| Llamados que no llegaron en su tiempo de gracia (15 minutos) | La primera vez el turno pasa al final de la fila; la segunda, pierde el turno. La fila avanza, se llama al siguiente y el dashboard se actualiza solo. |
| Sesiones del bot sin respuesta (30 minutos) | El bot vuelve al menú y olvida lo que el cliente iba escribiendo. Si la persona nunca aceptó el aviso de datos, se borra también su celular. |
| Pausas del bot que ya pasaron (1 hora después de tu último mensaje desde Comentarios) | Se borran: el bot ya le contesta normal a ese cliente y no queda su celular guardado sin necesidad. |
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

### Mientras estés en el plan gratis de Render

Sirve para probar antes de pagar, con tres diferencias: el servidor se apaga si nadie entra en 15 minutos (la pantalla del parqueadero abierta lo mantiene despierto), la base gratis vence a los 30 días y no hay **Shell** ni **Pre-deploy**.

Las tablas nuevas no necesitan nada de tu parte: **el servidor las pone al día solo cada vez que enciende**, antes de atender (`auto-ibaguer actualizar-tablas`, en el `Dockerfile`). Así, cuando unes un cambio que trae tablas nuevas, Render lo publica y la base queda al día sola. Si algo falla, esa versión no enciende y Render no la publica: el motivo está en **Logs**. En los **Logs** de cada encendido sale `Tablas al día.` (y, si había migraciones, `Running upgrade ...`).

Para revisarla desde tu computador, con la *External Database URL* de la base (en Render: la base > **Connect** > **External**):

```bash
read -rsp "URL externa de Render: " DATABASE_URL && export DATABASE_URL   # pégala y Enter (no se ve)
uv run auto-ibaguer revisar-base      # debe decir "Tablas: al día"
unset DATABASE_URL
```

Esa URL lleva la contraseña de la base: no la pegues en ningún chat ni la guardes en un archivo. Al pasar a pago, en el servidor > **Settings** puedes poner `auto-ibaguer actualizar-tablas` como **Pre-Deploy Command**: Render lo corre antes de publicar y, si falla, ni siquiera intenta encender la versión nueva.

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
├── turnos/         la fila: turnos, llamados, avisos, puestos, historial, cobro, y la caja, el balance, el recibo y el reporte diario del lavadero (caja.py, consultas_de_caja.py, balance.py, recibo.py, reporte.py)
├── bot/            conversación de WhatsApp: pasos, botones, textos, avisos y la pausa
├── comentarios/    quejas, felicitaciones y preguntas para el administrador, y responderle al cliente
├── caja/           lo común de las cuentas de los dos negocios: la caja (abrirla, cerrarla, cuadrarla, corregirla, los cierres con filtros), los periodos del balance, sus gráficas y lo que el reporte del día dice de la caja
├── parqueadero/    entradas y salidas, cobro por hora, mensualidades, lo que entra a su caja, sus tarifas, el balance y el reporte diario
├── integraciones/  mensajes que entran y salen de WhatsApp (bandejas y reintentos) y el correo del reporte
├── web/            dashboard: pantallas, sesión, fila en vivo y administradores
├── reloj/          lo que pasa solo cada 30 segundos (gracias, sesiones del bot, avisos)
├── db/             conexión a la base de datos y lo que mira revisar-base
├── cli.py          comandos de la terminal (preparar-base, actualizar-tablas, revisar-base, crear-admin, simular-turno, registrar-whatsapp, probar-gemini)
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
| `entrar.py`, `inicio.py`, `fila.py`, `clientes.py`, `comentarios.py`, `configuracion.py`, `textos_del_bot.py`, `mensajes.py`, `probar_bot.py`, `parqueadero.py` | Las rutas de cada pantalla (`inicio.py` es la de escoger el negocio, con lo de hoy de cada uno). Llaman a los casos de uso; no tienen reglas propias. |
| `caja.py` | Las pantallas de la caja, las mismas para los dos negocios: abrirla, cerrarla, una caja con su ticket, corregir la base o el conteo, los cierres con filtros y los archivos para Excel. Cada negocio pone lo suyo en un `CajaDeUnNegocio` (`caja_del_lavadero.py` y `parqueadero.py`). |
| `negocios.py` | Los dos negocios, el lavadero y el parqueadero, y las secciones de cada uno: el menú de arriba y la barra de abajo del celular salen de ahí. |
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
| Lo que dice el bot | **Configuración > Textos del bot** para los 58 que más leen los clientes, y **Configuración > Datos personales** para el aviso de datos. Cada texto cambiado es una fila `texto_bot.<nombre>` en la tabla `configuracion`. Los botones y los demás textos, en `app/bot/textos.py`. |
| Tarifas del parqueadero, lo que sale en el ticket y a quién le llega el reporte diario | Pantalla **Parqueadero > Tarifas y ticket** (filas `parqueadero.<nombre>` en la tabla `configuracion`). Cómo se ve el ticket impreso, en `app/web/estaticos/css/ticket.css`. Lo que dice el reporte, en `app/parqueadero/reporte.py`. |
| Las secciones de cada negocio (el menú) | `app/web/negocios.py`. Cada pantalla dice de qué negocio y de qué sección es (`seccion="parqueadero/caja"`). |
| Colores, letras y tamaños del dashboard | `app/web/estaticos/css/dashboard.css`. Los colores del diseño están al comienzo, con nombre. |
| Conexión a la base, claves de Meta y Gemini, el correo del reporte, reloj encendido o apagado | Archivo `.env`, que nunca se sube a GitHub. `.env.example` solo trae los nombres. En la nube, en Render: el servidor > **Environment**. |
| El servidor y la base de la nube (plan, región, versión de PostgreSQL) | `render.yaml`. Render aplica los cambios al unirlos a `main`. |

## Cambiar una tabla

1. Cambia el modelo en el `tablas.py` del módulo.
2. Crea la migración con el siguiente número (la última es la 0012):
   `uv run alembic revision --autogenerate --rev-id 0013 -m "que cambio"`
3. Revisa el archivo nuevo en `migraciones/versions/`: Alembic adivina bien casi siempre, pero no siempre.
4. Aplícala: `uv run auto-ibaguer preparar-base` (o solo las tablas: `uv run auto-ibaguer actualizar-tablas`)
5. `uv run pytest`: una prueba falla si el modelo y las migraciones no coinciden.

En la nube se aplica sola: al unir el cambio, Render publica la versión nueva y el servidor pone las tablas al día al encender.

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
| En Render, una publicación nueva no enciende y los **Logs** dicen `alembic` o `migration` | `actualizar-tablas` no pudo poner las tablas al día al encender: el motivo está en los **Logs**. Sigue funcionando la versión anterior. |
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
