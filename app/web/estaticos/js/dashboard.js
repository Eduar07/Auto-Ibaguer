/* Dashboard de Auto Ibaguer: lo poco que el navegador hace por su cuenta.
 *
 * HTMX pide y reemplaza los pedazos de la página; aquí solo va lo que pasa sin hablar con
 * el servidor: abrir y cerrar el diálogo, mostrar avisos, la cuenta regresiva de los
 * llamados, las pestañas del celular y los cálculos de los formularios.
 */
(function () {
  "use strict";

  const dialogo = document.getElementById("dialogo");
  const contenido = document.getElementById("dialogo-contenido");
  const avisos = document.getElementById("avisos");

  // --- Avisos -------------------------------------------------------------------------

  function avisar(texto, esError) {
    if (!texto) return;
    if (esError && dialogo.open) {
      // Un aviso normal quedaría detrás del diálogo: el error se muestra dentro de él.
      const cuerpo = contenido.querySelector(".dialogo__cuerpo");
      if (cuerpo) {
        let error = cuerpo.querySelector(".error");
        if (!error) {
          error = document.createElement("p");
          error.className = "error";
          error.setAttribute("role", "alert");
          cuerpo.prepend(error);
        }
        error.textContent = texto;
        error.scrollIntoView({ block: "nearest" });
        return;
      }
    }
    const aviso = document.createElement("div");
    aviso.className = esError ? "aviso aviso--error" : "aviso";
    aviso.setAttribute("role", esError ? "alert" : "status");
    aviso.textContent = texto;
    avisos.append(aviso);
    setTimeout(() => aviso.remove(), esError ? 8000 : 4500);
  }

  document.body.addEventListener("aviso", (evento) => avisar(evento.detail.value, false));
  document.body.addEventListener("error", (evento) => avisar(evento.detail.value, true));

  function refrescarFila() {
    const refrescar = document.getElementById("refrescar");
    if (refrescar) htmx.trigger(refrescar, "refrescar"); // solo existe en la fila en vivo
  }

  document.body.addEventListener("htmx:responseError", (evento) => {
    const xhr = evento.detail.xhr;
    let texto = "Algo salió mal. Intenta de nuevo.";
    try {
      const respuesta = JSON.parse(xhr.responseText);
      if (typeof respuesta.detail === "string") texto = respuesta.detail;
    } catch (_) {
      if (xhr.status >= 500) texto = "El servidor tuvo un problema. Intenta de nuevo en un momento.";
    }
    avisar(texto, true);
    refrescarFila(); // lo más probable es que la fila haya cambiado en otro celular
  });

  document.body.addEventListener("htmx:sendError", () => {
    avisar("No hay conexión con el servidor. Revisa el internet e intenta de nuevo.", true);
  });

  // --- Diálogo ------------------------------------------------------------------------

  document.body.addEventListener("htmx:afterSwap", (evento) => {
    if (evento.detail.target !== contenido) return;
    // Una respuesta que no cambia nada (la que solo cierra el diálogo y avisa) no lo abre.
    if (evento.detail.xhr && evento.detail.xhr.getResponseHeader("HX-Reswap") === "none") return;
    if (!contenido.firstElementChild) return;
    if (!dialogo.open) {
      dialogo.showModal();
    } else {
      const error = contenido.querySelector(".error");
      if (error) error.scrollIntoView({ block: "nearest" });
    }
    prepararFormularios(contenido);
  });

  document.body.addEventListener("cerrar-dialogo", () => dialogo.close());

  dialogo.addEventListener("click", (evento) => {
    if (evento.target.closest("[data-cerrar-dialogo]")) dialogo.close();
  });

  dialogo.addEventListener("close", () => contenido.replaceChildren());

  // --- Menú de cada turno (⋯) ---------------------------------------------------------

  document.addEventListener("click", (evento) => {
    for (const menu of document.querySelectorAll("details.menu-turno[open]")) {
      const eligioOpcion = evento.target.closest(".menu-turno__opciones button");
      if (!menu.contains(evento.target) || eligioOpcion) menu.open = false;
    }
  });

  // --- La fila se actualiza sola --------------------------------------------------------

  let menuAbierto = null; // si había un menú abierto, sigue abierto tras actualizar

  document.body.addEventListener("htmx:beforeSwap", (evento) => {
    if (evento.detail.target.id !== "tablero") return;
    const abierto = document.querySelector("#tablero details.menu-turno[open]");
    menuAbierto = abierto ? abierto.closest(".turno")?.id : null;
  });

  document.body.addEventListener("htmx:afterSwap", (evento) => {
    if (evento.detail.target.id !== "tablero") return;
    if (menuAbierto) {
      document.getElementById(menuAbierto)?.querySelector("details.menu-turno")?.setAttribute("open", "");
    }
    aplicarPestana();
    prepararCuentas();
  });

  // Al volver a la pantalla (el celular estaba bloqueado), traer la fila de una vez.
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) refrescarFila();
  });

  // Si la conexión de avisos se cortó y volvió, pudo perderse algún cambio.
  let conexionesAbiertas = 0;
  document.body.addEventListener("htmx:sseOpen", () => {
    conexionesAbiertas += 1;
    if (conexionesAbiertas > 1) refrescarFila();
  });

  // --- Cuenta regresiva de los llamados ---------------------------------------------------

  function dosCifras(numero) {
    return String(numero).padStart(2, "0");
  }

  function prepararCuentas() {
    const ahora = Date.now();
    for (const insignia of document.querySelectorAll("[data-restante]:not([data-fin])")) {
      insignia.dataset.fin = String(ahora + Number(insignia.dataset.restante) * 1000);
    }
    actualizarCuentas();
  }

  function actualizarCuentas() {
    const ahora = Date.now();
    for (const insignia of document.querySelectorAll("[data-fin]")) {
      const segundos = Math.max(0, Math.round((Number(insignia.dataset.fin) - ahora) / 1000));
      const texto = insignia.querySelector(".cuenta-regresiva");
      if (!texto) continue;
      texto.textContent = segundos > 0
        ? "Llamado · " + dosCifras(Math.floor(segundos / 60)) + ":" + dosCifras(segundos % 60)
        : "Llamado · tiempo vencido";
      insignia.classList.toggle("insignia--llamado", segundos > 0);
      insignia.classList.toggle("insignia--vencido", segundos === 0);
    }
  }

  setInterval(actualizarCuentas, 1000);

  // --- Pestañas del celular -------------------------------------------------------------

  const CLAVE_PESTANA = "auto-ibaguer:pestana";
  let pestana = null;
  try {
    pestana = localStorage.getItem(CLAVE_PESTANA);
  } catch (_) {
    // Sin almacenamiento (modo privado): empieza siempre en la primera fila.
  }

  function aplicarPestana() {
    const botones = [...document.querySelectorAll("#tablero .pestana")];
    if (!botones.length) return;
    if (!botones.some((boton) => boton.dataset.fila === pestana)) pestana = botones[0].dataset.fila;
    for (const boton of botones) {
      boton.setAttribute("aria-selected", String(boton.dataset.fila === pestana));
    }
    for (const seccion of document.querySelectorAll("#tablero .seccion[data-fila]")) {
      seccion.classList.toggle("seccion--otra-pestana", seccion.dataset.fila !== pestana);
    }
  }

  document.addEventListener("click", (evento) => {
    const boton = evento.target.closest("#tablero .pestana");
    if (!boton) return;
    pestana = boton.dataset.fila;
    try {
      localStorage.setItem(CLAVE_PESTANA, pestana);
    } catch (_) {
      // No pasa nada: la pestaña se recuerda mientras la página siga abierta.
    }
    aplicarPestana();
  });

  // --- Formularios ------------------------------------------------------------------------

  function conPuntos(numero) {
    return String(numero).replace(/\B(?=(\d{3})+(?!\d))/g, ".");
  }

  function soloDigitos(texto) {
    return (texto || "").replace(/\D/g, "");
  }

  // Nuevo turno y corregir: cada tipo de vehículo tiene su lista de servicios y precios.
  function cambiarTipo(formulario, radio) {
    const elegido = formulario.querySelector("input[name='servicio_id']:checked")?.value;
    for (const grupo of formulario.querySelectorAll(".servicios-del-tipo")) {
      const visible = grupo.dataset.tipo === radio.value;
      grupo.hidden = !visible;
      grupo.disabled = !visible;
      if (!visible) continue;
      // Se conserva el mismo servicio si existe para el otro tipo (Básico de carro sirve
      // para camioneta); si no, el nuevo turno propone el Básico y la corrección no marca nada.
      const opciones = [...grupo.querySelectorAll("input[name='servicio_id']")];
      const mismo = elegido === undefined ? null : opciones.find((opcion) => opcion.value === elegido);
      const basico = "elegirBasico" in formulario.dataset ? grupo.querySelector("input[data-basico]") : null;
      const marcar = mismo || basico;
      if (marcar) marcar.checked = true;
    }
    const placa = formulario.querySelector("input[name='placa']");
    if (placa && radio.dataset.placa) placa.placeholder = radio.dataset.placa;
  }

  // Cobrar: el precio del menú sigue al servicio y el total al precio cobrado.
  function actualizarCobro(formulario, cambioElServicio) {
    const opcion = formulario.querySelector("select[name='servicio_id']").selectedOptions[0];
    const menu = opcion && opcion.dataset.precio ? Number(opcion.dataset.precio) : null;
    const cobrado = formulario.querySelector("input[name='precio_cobrado']");
    const precioMenu = formulario.querySelector("[data-precio-menu]");
    if (cambioElServicio && menu !== null) {
      precioMenu.textContent = "$" + conPuntos(menu);
      cobrado.value = conPuntos(menu);
      cobrado.placeholder = conPuntos(menu);
    }
    const digitos = soloDigitos(cobrado.value);
    const total = digitos ? Number(digitos) : menu;
    formulario.querySelector("[data-total]").textContent = total === null ? "—" : "$" + conPuntos(total);

    // Cobrar distinto al menú exige escribir el motivo (el servidor también lo revisa).
    const motivo = formulario.querySelector("input[name='motivo']");
    const etiqueta = formulario.querySelector("label[for='motivo']");
    const distinto = menu !== null && total !== menu;
    motivo.required = distinto;
    etiqueta.textContent = distinto ? "Motivo del cambio de precio (obligatorio)" : "Motivo del cambio de precio";

    const medio = formulario.querySelector("input[name='medio_pago']:checked");
    if (medio) formulario.querySelector("[data-medio]").textContent = medio.parentElement.textContent.trim();

    const placa = formulario.querySelector("input[name='placa']");
    const confirmar = formulario.querySelector("[data-confirmar]");
    const faltaPlaca = placa !== null && placa.value.trim() === "";
    // Con la caja del lavadero cerrada no se cobra: el diálogo dice dónde abrirla.
    const cajaCerrada = formulario.hasAttribute("data-caja-cerrada");
    confirmar.disabled = faltaPlaca || cajaCerrada;
    confirmar.textContent = cajaCerrada
      ? "Abre la caja para cobrar"
      : faltaPlaca ? "Escribe la placa para cobrar" : "Confirmar cobro";
  }

  function prepararFormularios(raiz) {
    const cobro = raiz.querySelector("form[data-formulario='cobro']");
    if (cobro) actualizarCobro(cobro, false);
    const salida = raiz.querySelector("form[data-formulario='salida-parqueo']");
    if (salida) actualizarSalida(salida);
    const cierre = raiz.querySelector("form[data-formulario='cierre-caja']");
    if (cierre) actualizarCierre(cierre);
  }

  // --- Parqueadero -------------------------------------------------------------------------

  function pesos(numero) {
    return "$" + conPuntos(numero);
  }

  // Salida: el total sigue al "valor a cobrar" (si se escribió otro) y el vuelto, a lo
  // recibido. El vuelto solo se calcula aquí: a la caja entra el total, nunca el billete.
  function actualizarSalida(formulario) {
    const calculado = Number(formulario.dataset.calculado || 0);
    const escrito = soloDigitos(formulario.querySelector("input[name='valor_cobrado']")?.value);
    const total = escrito ? Number(escrito) : calculado;
    formulario.querySelector("[data-total]").textContent = pesos(total);

    const motivo = formulario.querySelector("input[name='motivo']");
    if (motivo) motivo.required = total !== calculado;

    const medio = formulario.querySelector("input[name='medio_pago']:checked");
    const efectivo = !medio || medio.value === "efectivo";
    const bloque = formulario.querySelector("[data-solo-efectivo]");
    if (bloque) bloque.hidden = !efectivo;

    const vuelto = formulario.querySelector("[data-vuelto]");
    if (vuelto) {
      const recibido = soloDigitos(formulario.querySelector("input[name='recibido']")?.value);
      if (!recibido) vuelto.textContent = "—";
      else if (Number(recibido) >= total) vuelto.textContent = pesos(Number(recibido) - total);
      else vuelto.textContent = "Faltan " + pesos(total - Number(recibido));
    }
  }

  // Cierre de caja: lo contado contra lo que debe haber en el cajón.
  function actualizarCierre(formulario) {
    const esperado = Number(formulario.dataset.esperado || 0);
    const contado = soloDigitos(formulario.querySelector("input[name='efectivo_contado']").value);
    const texto = formulario.querySelector("[data-diferencia]");
    if (!contado) {
      texto.textContent = "Escribe lo que contaste y aquí ves si cuadra.";
      return;
    }
    const diferencia = Number(contado) - esperado;
    texto.textContent = diferencia === 0
      ? "Cuadra exacto."
      : (diferencia > 0 ? "Sobran " : "Faltan ") + pesos(Math.abs(diferencia)) + ".";
  }

  // Pago de mensualidad: 30 días seguidos contando el primero.
  function ajustarHasta(formulario) {
    const desde = formulario.querySelector("input[name='desde']").value;
    if (!desde) return;
    const [anio, mes, dia] = desde.split("-").map(Number);
    const fin = new Date(Date.UTC(anio, mes - 1, dia + 29));
    formulario.querySelector("input[name='hasta']").value = fin.toISOString().slice(0, 10);
  }

  // Imprimir: el ticket se carga en un marco escondido y se abre la ventana de impresión.
  // Con Chrome abierto con --kiosk-printing, sale directo a la impresora sin preguntar.
  function imprimir(direccion) {
    if (!direccion) return;
    let marco = document.getElementById("marco-impresion");
    if (!marco) {
      marco = document.createElement("iframe");
      marco.id = "marco-impresion";
      marco.className = "marco-impresion";
      marco.title = "Ticket para imprimir";
      marco.setAttribute("aria-hidden", "true");
      marco.tabIndex = -1;
      document.body.append(marco);
    }
    marco.onload = () => {
      try {
        marco.contentWindow.focus();
        marco.contentWindow.print();
      } catch (_) {
        window.open(direccion, "_blank", "noopener");
      }
    };
    marco.src = direccion;
  }

  document.body.addEventListener("imprimir", (evento) => imprimir(evento.detail.value));

  document.addEventListener("click", (evento) => {
    const boton = evento.target.closest("[data-imprimir]");
    if (boton) imprimir(boton.dataset.imprimir);
  });

  // Después de registrar una entrada: el formulario queda listo para la siguiente placa.
  document.body.addEventListener("limpiar-entrada", () => {
    const formulario = document.querySelector("form[data-formulario='entrada-parqueo']");
    if (!formulario) return;
    for (const nombre of ["placa", "observaciones"]) {
      const campo = formulario.querySelector(`input[name='${nombre}']`);
      if (campo) campo.value = "";
    }
    formulario.querySelector("input[name='placa']").focus();
  });

  document.addEventListener("change", (evento) => {
    const formulario = evento.target.closest("form[data-formulario]");
    if (!formulario) return;
    if (formulario.dataset.formulario === "servicio-por-tipo" && evento.target.name === "tipo_vehiculo_id") {
      cambiarTipo(formulario, evento.target);
    }
    if (formulario.dataset.formulario === "cobro") {
      actualizarCobro(formulario, evento.target.name === "servicio_id");
    }
    if (formulario.dataset.formulario === "salida-parqueo") actualizarSalida(formulario);
    if (formulario.dataset.formulario === "pago-mensualidad" && evento.target.name === "desde") {
      ajustarHasta(formulario);
    }
  });

  document.addEventListener("input", (evento) => {
    const formulario = evento.target.closest("form[data-formulario]");
    if (!formulario) return;
    if (formulario.dataset.formulario === "cobro") actualizarCobro(formulario, false);
    if (formulario.dataset.formulario === "salida-parqueo") actualizarSalida(formulario);
    if (formulario.dataset.formulario === "cierre-caja") actualizarCierre(formulario);
  });

  // --- Gráficas del Balance ----------------------------------------------------------------
  // Al pasar el puntero o el foco por una columna, el aviso dice su valor primero y después
  // qué día es. Ayuda, pero no es la única forma de verlo: la tabla de abajo tiene lo mismo.
  // Los textos van con textContent: nunca se pegan como HTML.

  function mostrarAviso(columna) {
    const figura = columna.closest("[data-grafica]");
    const aviso = figura ? figura.querySelector(".grafica__aviso") : null;
    if (!aviso) return;
    const valor = document.createElement("strong");
    valor.textContent = columna.dataset.valor || "";
    const nombre = document.createElement("span");
    nombre.textContent = columna.dataset.nombre || "";
    aviso.replaceChildren(valor, nombre);
    if (columna.dataset.detalle) {
      const detalle = document.createElement("span");
      detalle.className = "suave";
      detalle.textContent = columna.dataset.detalle;
      aviso.append(detalle);
    }
    aviso.hidden = false;
    const marco = figura.getBoundingClientRect();
    const caja = columna.getBoundingClientRect();
    const centro = caja.left + caja.width / 2 - marco.left;
    const izquierda = Math.min(Math.max(centro - aviso.offsetWidth / 2, 0), marco.width - aviso.offsetWidth);
    aviso.style.left = Math.max(izquierda, 0) + "px";
  }

  function esconderAviso(columna) {
    const figura = columna.closest("[data-grafica]");
    const aviso = figura ? figura.querySelector(".grafica__aviso") : null;
    if (aviso) aviso.hidden = true;
  }

  for (const evento of ["pointerover", "focusin"]) {
    document.addEventListener(evento, (e) => {
      const columna = e.target.closest ? e.target.closest("[data-punto]") : null;
      if (columna) mostrarAviso(columna);
    });
  }
  for (const evento of ["pointerout", "focusout"]) {
    document.addEventListener(evento, (e) => {
      const columna = e.target.closest ? e.target.closest("[data-punto]") : null;
      if (columna && !(e.relatedTarget && columna.contains(e.relatedTarget))) esconderAviso(columna);
    });
  }

  // --- Clientes ---------------------------------------------------------------------------

  function marcarClienteElegido() {
    const ficha = document.querySelector("#ficha-cliente .ficha");
    const elegido = ficha ? ficha.dataset.cliente : null;
    for (const boton of document.querySelectorAll(".tabla-clientes__cliente")) {
      if (boton.dataset.cliente === elegido) boton.setAttribute("aria-current", "true");
      else boton.removeAttribute("aria-current");
    }
  }

  document.body.addEventListener("htmx:afterSwap", (evento) => {
    const destino = evento.detail.target.id;
    if (destino === "ficha-cliente") {
      marcarClienteElegido();
      // En el celular la ficha queda debajo de la lista: se baja hasta ella.
      if (window.matchMedia("(max-width: 1011px)").matches && evento.detail.requestConfig?.verb === "get") {
        evento.detail.target.scrollIntoView({ behavior: "smooth", block: "start" });
      }
    }
    if (destino === "lista-clientes") marcarClienteElegido();
  });

  // --- Probar el bot ----------------------------------------------------------------------

  // La charla siempre muestra lo último, como en WhatsApp.
  const charla = document.getElementById("charla");
  if (charla) {
    new MutationObserver(() => {
      charla.scrollTop = charla.scrollHeight;
    }).observe(charla, { childList: true });
  }

  // Después de enviar un mensaje, el campo queda vacío y listo para el siguiente.
  document.body.addEventListener("htmx:afterRequest", (evento) => {
    const formulario = evento.detail.elt;
    if (!evento.detail.successful || !formulario.matches("form[data-chat-bot]")) return;
    formulario.reset();
    formulario.querySelector("input[name='texto']").focus();
  });

  // --- Textos del bot ----------------------------------------------------------------------

  // Un dato ({numero}...) va donde está el cursor, y "Volver al original" pone el texto
  // original en el campo. En los dos casos la vista previa se actualiza sola.
  function cambiarCampo(campo, texto, desde, hasta) {
    campo.focus();
    campo.setRangeText(texto, desde, hasta, "end");
    campo.dispatchEvent(new Event("input", { bubbles: true }));
  }

  document.addEventListener("click", (evento) => {
    const boton = evento.target.closest("[data-insertar], [data-original]");
    if (!boton) return;
    const campo = document.getElementById(boton.dataset.campo);
    if (!campo) return;
    if ("insertar" in boton.dataset) {
      cambiarCampo(campo, boton.dataset.insertar, campo.selectionStart, campo.selectionEnd);
    } else {
      cambiarCampo(campo, boton.dataset.original, 0, campo.value.length);
    }
  });

  // --- Al abrir la página ------------------------------------------------------------------

  marcarClienteElegido();

  aplicarPestana();
  prepararCuentas();

  // Una pantalla que llega pidiendo imprimir (el resumen al cerrar la caja).
  const alAbrir = document.querySelector("[data-imprimir-al-abrir]");
  if (alAbrir) {
    imprimir(alAbrir.dataset.imprimirAlAbrir);
    history.replaceState(null, "", location.pathname); // al recargar, no imprime otra vez
  }
})();
