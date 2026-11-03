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
    htmx.trigger("#refrescar", "refrescar");
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
    confirmar.disabled = faltaPlaca;
    confirmar.textContent = faltaPlaca ? "Escribe la placa para cobrar" : "Confirmar cobro";
  }

  function prepararFormularios(raiz) {
    const cobro = raiz.querySelector("form[data-formulario='cobro']");
    if (cobro) actualizarCobro(cobro, false);
  }

  document.addEventListener("change", (evento) => {
    const formulario = evento.target.closest("form[data-formulario]");
    if (!formulario) return;
    if (formulario.dataset.formulario === "servicio-por-tipo" && evento.target.name === "tipo_vehiculo_id") {
      cambiarTipo(formulario, evento.target);
    }
    if (formulario.dataset.formulario === "cobro") {
      actualizarCobro(formulario, evento.target.name === "servicio_id");
    }
  });

  document.addEventListener("input", (evento) => {
    const formulario = evento.target.closest("form[data-formulario='cobro']");
    if (formulario) actualizarCobro(formulario, false);
  });

  // --- Al abrir la página ------------------------------------------------------------------

  aplicarPestana();
  prepararCuentas();
})();
