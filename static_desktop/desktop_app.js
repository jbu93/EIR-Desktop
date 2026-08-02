/* desktop_app.js — EIR DR. Desktop (tema Organic Professional)
 * Autocontenido: cero dependencia de /static/ del proyecto principal,
 * cero CDN en runtime. Habla con el backend real vía
 * POST /api/shell/conversar (rol, mensaje, historial) y GET /health.
 *
 * Honestidad (L4/L7 AGENTS.md): nunca se inventan pasos, cifras de
 * hardware ni resultados. Todo lo que se pinta viene de la respuesta
 * real del backend o se declara explícitamente como "no implementado".
 */
(() => {
  'use strict';

  const ROLES = {
    odontologo:  { label: 'Odontólogo' },
    recepcion:   { label: 'Recepción' },
    laboratorio: { label: 'Laboratorio' },
    marketing:   { label: 'Marketing' },
  };

  const state = {
    mode: 'chat',
    role: 'odontologo',
    historyByRole: { odontologo: [], recepcion: [], laboratorio: [], marketing: [] },
    conversations: [], // {id, role, title, timestamp}
    connection: 'checking',
    sesion: { autenticado: false }, // reflejo de /api/sesion (el token vive en el proceso Python)
    paradigmaPlan: false, // M-057/M-061 · false=build (default histórico), true=plan
  };

  // M-058/M-061 · nombres de tool locales que el servidor cloud puede pedir
  // en delegación (D085, solo lectura). Etiqueta legible en la traza cuando
  // aparecen — NO es un indicador en vivo (la respuesta llega ya completa,
  // sin streaming), es honesto sobre lo que EIR hizo, no sobre cuándo.
  const ETIQUETAS_TOOL = {
    leer_archivo: 'Leyó un archivo local',
    listar_archivos: 'Listó archivos locales',
    buscar_texto: 'Buscó texto en archivos locales',
    lsp_definicion: 'Buscó una definición de código',
    lsp_referencias: 'Buscó referencias de código',
    lsp_diagnosticos: 'Revisó diagnósticos de código',
  };

  const el = (id) => document.getElementById(id);

  /**
   * Mapa de elementos del DOM, tipado una sola vez aquí (en vez de castear
   * cada acceso disperso en el archivo). Los tipos concretos vienen del tag
   * real en desktop_chat.html — `el()` solo sabe HTMLElement|null, por eso
   * cada propiedad que necesita .value/.disabled/.href/.requestSubmit() se
   * anota con su subtipo real.
   * @type {{
   *   modeBtns: () => HTMLElement[], roleBtns: () => HTMLElement[],
   *   panelChat: HTMLElement, panelCowork: HTMLElement, panelAuto: HTMLElement,
   *   coworkGeneric: HTMLElement, coworkLab: HTMLElement,
   *   messages: HTMLElement, welcome: HTMLElement,
   *   chatForm: HTMLFormElement, chatInput: HTMLTextAreaElement, sendBtn: HTMLButtonElement,
   *   tracePanel: HTMLElement, traceContent: HTMLElement,
   *   labForm: HTMLFormElement, labInput: HTMLInputElement, labStream: HTMLElement,
   *   statusDot: HTMLElement, statusText: HTMLElement,
   *   historyList: HTMLElement, historyEmpty: HTMLElement, clearHistoryBtn: HTMLButtonElement,
   *   footerRoleLabel: HTMLElement, settingsBtn: HTMLButtonElement,
   *   sessionLoggedOut: HTMLElement, sessionLoggedIn: HTMLElement,
   *   sessionUser: HTMLElement, sessionCredito: HTMLElement,
   *   sessionLoginBtn: HTMLButtonElement, sessionLogoutBtn: HTMLButtonElement,
   *   loginModal: HTMLElement, loginForm: HTMLFormElement,
   *   loginEmail: HTMLInputElement, loginPassword: HTMLInputElement,
   *   loginError: HTMLElement, loginSubmitBtn: HTMLButtonElement, loginCancelBtn: HTMLButtonElement,
   *   updateBanner: HTMLElement, updateText: HTMLElement, updateLink: HTMLAnchorElement,
   *   paradigmaPlanBtn: HTMLButtonElement,
   * }}
   */
  const els = {
    modeBtns: () => /** @type {HTMLElement[]} */ (Array.from(document.querySelectorAll('.mode-btn'))),
    roleBtns: () => /** @type {HTMLElement[]} */ (Array.from(document.querySelectorAll('.role-btn'))),
    panelChat: el('panel-chat'),
    panelCowork: el('panel-cowork'),
    panelAuto: el('panel-auto'),
    coworkGeneric: el('cowork-generic'),
    coworkLab: el('cowork-lab'),
    messages: el('messages'),
    welcome: el('welcome-message'),
    chatForm: /** @type {HTMLFormElement} */ (el('chat-form')),
    chatInput: /** @type {HTMLTextAreaElement} */ (el('chat-input')),
    sendBtn: /** @type {HTMLButtonElement} */ (el('send-btn')),
    micBtn: /** @type {HTMLButtonElement} */ (el('mic-btn')),
    tracePanel: el('trace-panel'),
    traceContent: el('trace-content'),
    labForm: /** @type {HTMLFormElement} */ (el('lab-chat-form')),
    labInput: /** @type {HTMLInputElement} */ (el('lab-chat-input')),
    labStream: el('lab-thought-stream'),
    statusDot: el('status-dot'),
    statusText: el('status-text'),
    historyList: el('history-list'),
    historyEmpty: el('history-empty'),
    clearHistoryBtn: /** @type {HTMLButtonElement} */ (el('clear-history-btn')),
    footerRoleLabel: el('footer-role-label'),
    settingsBtn: /** @type {HTMLButtonElement} */ (el('settings-btn')),
    sessionLoggedOut: el('session-logged-out'),
    sessionLoggedIn: el('session-logged-in'),
    sessionUser: el('session-user'),
    sessionCredito: el('session-credito'),
    sessionLoginBtn: /** @type {HTMLButtonElement} */ (el('session-login-btn')),
    sessionLogoutBtn: /** @type {HTMLButtonElement} */ (el('session-logout-btn')),
    loginModal: el('login-modal'),
    loginForm: /** @type {HTMLFormElement} */ (el('login-form')),
    loginEmail: /** @type {HTMLInputElement} */ (el('login-email')),
    loginPassword: /** @type {HTMLInputElement} */ (el('login-password')),
    loginError: el('login-error'),
    loginSubmitBtn: /** @type {HTMLButtonElement} */ (el('login-submit-btn')),
    loginCancelBtn: /** @type {HTMLButtonElement} */ (el('login-cancel-btn')),
    updateBanner: el('update-banner'),
    updateText: el('update-text'),
    updateLink: /** @type {HTMLAnchorElement} */ (el('update-link')),
    paradigmaPlanBtn: /** @type {HTMLButtonElement} */ (el('paradigma-plan-btn')),
  };

  // ─── Arranque ───
  function init() {
    loadFromStorage();
    bindEvents();
    renderHistory();
    updateRoleUI();
    renderMessagesForRole();
    checkConnection();
    setInterval(checkConnection, 20000);
    cargarSesion();
    cargarVersion();
  }

  function bindEvents() {
    els.modeBtns().forEach((btn) => btn.addEventListener('click', () => switchMode(btn.dataset.mode)));
    els.roleBtns().forEach((btn) => btn.addEventListener('click', () => switchRole(btn.dataset.role)));

    els.chatForm.addEventListener('submit', (e) => {
      e.preventDefault();
      const texto = els.chatInput.value.trim();
      if (!texto) return;
      els.chatInput.value = '';
      autoGrow(els.chatInput);
      enviarMensaje(state.role, texto, { destino: 'chat' });
    });

    els.chatInput.addEventListener('input', () => autoGrow(els.chatInput));
    els.chatInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        els.chatForm.requestSubmit();
      }
    });

    els.labForm.addEventListener('submit', (e) => {
      e.preventDefault();
      const texto = els.labInput.value.trim();
      if (!texto) return;
      els.labInput.value = '';
      enviarMensaje('laboratorio', texto, { destino: 'lab' });
    });

    els.clearHistoryBtn.addEventListener('click', () => {
      state.conversations = [];
      state.historyByRole = { odontologo: [], recepcion: [], laboratorio: [], marketing: [] };
      saveToStorage();
      renderHistory();
      resetChatView();
    });

    els.settingsBtn.addEventListener('click', () => {
      addSystemNote('Configuración: disponible próximamente. Hoy no hay ajustes que tocar en el sandbox.');
    });

    bindMicDictation();

    els.sessionLoginBtn.addEventListener('click', abrirLogin);
    els.loginCancelBtn.addEventListener('click', cerrarLogin);
    els.loginForm.addEventListener('submit', enviarLogin);
    els.sessionLogoutBtn.addEventListener('click', logout);

    // M-057/M-061 · alterna build/plan. Solo cambia CÓMO se envía el próximo
    // mensaje (paradigma) — cero efecto sobre lo ya conversado.
    els.paradigmaPlanBtn.addEventListener('click', () => {
      state.paradigmaPlan = !state.paradigmaPlan;
      els.paradigmaPlanBtn.setAttribute('aria-pressed', String(state.paradigmaPlan));
      els.paradigmaPlanBtn.classList.toggle('active', state.paradigmaPlan);
    });
  }

  // ─── §UI-2026-08-02 · Dictado por voz (Web Speech API) ───
  // Mismo patrón que btn-mic-chat de la web (atelier_shell.js). No gasta la
  // cuota/API de EIR (no es una llamada a Groq/NVIDIA/Google) — el motor de
  // reconocimiento lo resuelve el navegador/ventana nativa, no nuestro backend.
  //
  // HONESTIDAD (L4): si el motor de la ventana (pywebview/WebView2) no expone
  // SpeechRecognition, el botón se OCULTA. No se finge un dictado que no va a
  // funcionar — se declara la ausencia, como manda el protocolo.
  let _reconocedorVoz = null;
  let _dictando = false;

  function bindMicDictation() {
    if (!els.micBtn) return;
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SR) {
      els.micBtn.hidden = true;
      return;
    }
    _reconocedorVoz = new SR();
    _reconocedorVoz.lang = 'es-CO';
    _reconocedorVoz.continuous = true;
    _reconocedorVoz.interimResults = false;

    _reconocedorVoz.onstart = () => {
      _dictando = true;
      els.micBtn.classList.add('on');
      els.micBtn.title = 'Detener dictado';
    };
    _reconocedorVoz.onend = () => {
      _dictando = false;
      els.micBtn.classList.remove('on');
      els.micBtn.title = 'Dictar por voz (sin gastar la cuota de EIR)';
    };
    _reconocedorVoz.onerror = () => {
      _dictando = false;
      els.micBtn.classList.remove('on');
    };
    _reconocedorVoz.onresult = (ev) => {
      let texto = '';
      for (let i = ev.resultIndex; i < ev.results.length; i++) {
        if (ev.results[i].isFinal) texto += ev.results[i][0].transcript;
      }
      if (!texto) return;
      const previo = els.chatInput.value;
      els.chatInput.value = (previo ? previo.trim() + ' ' : '') + texto.trim();
      autoGrow(els.chatInput);
    };

    els.micBtn.addEventListener('click', () => {
      if (_dictando) { _reconocedorVoz.stop(); return; }
      try { _reconocedorVoz.start(); } catch (_e) { /* ya estaba iniciado */ }
    });
  }

  function autoGrow(textarea) {
    textarea.style.height = '';
    textarea.style.height = Math.min(textarea.scrollHeight, 160) + 'px';
  }

  // ─── Mode / role switching ───
  function switchMode(mode) {
    state.mode = mode;
    els.modeBtns().forEach((btn) => btn.classList.toggle('active', btn.dataset.mode === mode));

    els.panelChat.hidden = mode !== 'chat';
    els.panelChat.classList.toggle('flex', mode === 'chat');
    els.panelCowork.hidden = mode !== 'cowork';
    els.panelCowork.classList.toggle('flex', mode === 'cowork');
    els.panelAuto.hidden = mode !== 'auto';
    els.panelAuto.classList.toggle('flex', mode === 'auto');

    els.tracePanel.hidden = mode !== 'chat' || !els.traceContent.childElementCount;

    if (mode === 'cowork') updateCoworkVariant();
  }

  function switchRole(role) {
    if (!ROLES[role]) return;
    state.role = role;
    els.roleBtns().forEach((btn) => btn.classList.toggle('active', btn.dataset.role === role));
    updateRoleUI();
    if (state.mode === 'cowork') updateCoworkVariant();
    renderMessagesForRole();
  }

  function updateRoleUI() {
    els.footerRoleLabel.textContent = ROLES[state.role].label.toUpperCase();
  }

  function updateCoworkVariant() {
    const isLab = state.role === 'laboratorio';
    els.coworkGeneric.hidden = isLab;
    els.coworkLab.hidden = !isLab;
    els.coworkLab.classList.toggle('flex', isLab);
    if (isLab && window.EIR_STL_VIEWER) {
      // retardo para que el layout asiente y el canvas tenga tamaño real
      requestAnimationFrame(() => requestAnimationFrame(() => window.EIR_STL_VIEWER.init()));
    }
  }

  // ─── Mensajería (chat principal) ───
  function resetChatView() {
    els.messages.innerHTML = '';
    els.messages.appendChild(els.welcome);
    els.welcome.hidden = false;
    els.tracePanel.hidden = true;
    els.traceContent.innerHTML = '';
  }

  function renderMessagesForRole() {
    els.messages.innerHTML = '';
    const historial = state.historyByRole[state.role] || [];
    if (!historial.length) {
      els.messages.appendChild(els.welcome);
      els.welcome.hidden = false;
      els.tracePanel.hidden = true;
      return;
    }
    els.welcome.hidden = true;
    historial.forEach((turno) => {
      pintarBurbuja(turno.rol === 'usuario' ? 'usuario' : 'eir', turno.texto, turno.error);
    });
    const ultimo = [...historial].reverse().find((t) => t.pasos);
    if (ultimo && ultimo.pasos.length) {
      pintarTraza(ultimo.pasos);
      els.tracePanel.hidden = false;
    } else {
      els.tracePanel.hidden = true;
    }
    scrollToBottom();
  }

  function pintarBurbuja(quien, texto, esError) {
    if (!els.welcome.hidden) els.welcome.hidden = true;
    const row = document.createElement('div');
    row.className = 'msg-row ' + quien + (esError ? ' error' : '');
    const bubble = document.createElement('div');
    bubble.className = 'msg-bubble';
    bubble.textContent = texto;
    row.appendChild(bubble);
    els.messages.appendChild(row);
    scrollToBottom();
    return row;
  }

  function pintarTraza(pasos) {
    els.traceContent.innerHTML = '';
    if (!pasos || !pasos.length) {
      els.traceContent.innerHTML = '<p class="text-[12px] text-on-surface-variant">Sin pasos de herramienta en esta respuesta.</p>';
      return;
    }
    pasos.forEach((p) => {
      const item = document.createElement('div');
      item.className = 'paso-item';
      const dot = document.createElement('span');
      dot.className = 'paso-dot material-symbols-outlined fill-icon ' + (p.ok ? 'ok' : 'err');
      dot.textContent = p.ok ? 'check_circle' : 'error';
      const body = document.createElement('div');
      body.className = 'flex-1';
      const nombre = document.createElement('div');
      nombre.className = 'paso-nombre';
      const etiqueta = ETIQUETAS_TOOL[p.herramienta] ? `${ETIQUETAS_TOOL[p.herramienta]} (${p.herramienta})` : (p.herramienta || '?');
      nombre.textContent = etiqueta + (p.ok ? ' · OK' : ' · FAIL (' + (p.motivo || 'sin motivo') + ')');
      body.appendChild(nombre);
      if (p.resumen) {
        const detalle = document.createElement('div');
        detalle.className = 'paso-detalle';
        detalle.textContent = p.resumen;
        body.appendChild(detalle);
      }
      item.appendChild(dot);
      item.appendChild(body);
      els.traceContent.appendChild(item);
    });
  }

  function addSystemNote(texto) {
    const row = document.createElement('div');
    row.style.textAlign = 'center';
    row.style.fontSize = '12px';
    row.style.color = '#42493e';
    row.style.opacity = '0.8';
    row.style.padding = '6px 0';
    row.textContent = texto;
    els.messages.appendChild(row);
    scrollToBottom();
  }

  function scrollToBottom() {
    const viewport = document.getElementById('chat-viewport');
    if (viewport) viewport.scrollTop = viewport.scrollHeight;
  }

  /**
   * @param {string} rol
   * @param {string} mensaje
   * @param {{ destino: string, tokenAprobacion?: string, paradigma?: string, plan?: any, tokenPlan?: string }} opciones
   */
  async function enviarMensaje(rol, mensaje, { destino, tokenAprobacion, paradigma, plan, tokenPlan }) {
    const historialPrevio = (state.historyByRole[rol] || [])
      .slice(-10)
      .map((t) => ({ role: t.rol === 'usuario' ? 'user' : 'assistant', text: t.texto }));

    let esperaRow = null;
    if (destino === 'chat') {
      pintarBurbuja('usuario', mensaje);
      esperaRow = pintarBurbuja('eir', '…pensando');
    } else {
      pintarLabEntry('usuario', mensaje);
      esperaRow = pintarLabEntry('eir', '…pensando');
    }

    // M-057/M-061 · si no viene explícito (reintento tras aprobar un plan),
    // usa el paradigma que el doctor eligió con el botón "Modo plan".
    const paradigmaFinal = paradigma || (state.paradigmaPlan ? 'plan' : 'build');

    let resultado = null;
    let error = null;
    try {
      const resp = await fetch('/api/shell/conversar', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          rol, mensaje, historial: historialPrevio,
          token_aprobacion: tokenAprobacion || '',
          paradigma: paradigmaFinal,
          plan: plan || null,
          token_plan: tokenPlan || '',
        }),
      });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        error = data.error ? `${data.error}${data.detalle ? ': ' + data.detalle : ''}` : `HTTP ${resp.status}`;
      } else {
        resultado = data.resultado || {};
      }
    } catch (e) {
      error = 'backend no disponible (' + (e instanceof Error ? e.message : String(e)) + ')';
    }

    if (esperaRow && esperaRow.remove) esperaRow.remove();

    const registro = { rol: 'usuario', texto: mensaje };
    state.historyByRole[rol] = state.historyByRole[rol] || [];
    state.historyByRole[rol].push(registro);

    if (error) {
      const texto = 'No pude completar la consulta: ' + error + '. Modo offline: solo quedan disponibles las tools locales.';
      if (destino === 'chat') pintarBurbuja('eir', texto, true);
      else pintarLabEntry('eir', texto, true);
      state.historyByRole[rol].push({ rol: 'eir', texto, error: true });
    } else {
      if (resultado.credito_restante_hoy !== undefined) {
        state.sesion.credito_hoy = resultado.credito_restante_hoy;
        pintarSesion(state.sesion);
      }
      const texto = resultado.resumen || '(sin resumen; motivo_fin: ' + (resultado.motivo_fin || 'desconocido') + ')';
      if (destino === 'chat') {
        pintarBurbuja('eir', texto);
        pintarTraza(resultado.pasos || []);
        els.tracePanel.hidden = false;
      } else {
        pintarLabEntry('eir', texto);
      }
      state.historyByRole[rol].push({ rol: 'eir', texto, pasos: resultado.pasos || [] });
      registrarConversacion(rol, mensaje);
    }

    saveToStorage();
    renderHistory();

    // M-057/M-061 · el modelo propuso un PLAN completo (paradigma=plan) y
    // espera aprobación antes de que "auto" ejecute nada.
    if (!error && resultado && resultado.aprobacion_pendiente
        && resultado.aprobacion_pendiente.tipo === 'plan' && !tokenPlan) {
      const token = await pedirAprobacionPlan(resultado.plan, resultado.aprobacion_pendiente);
      if (token) {
        await enviarMensaje(rol, mensaje, {
          destino, paradigma: 'auto', plan: resultado.plan, tokenPlan: token,
        });
      } else {
        addSystemNote('Plan rechazado: no se ejecutó ningún paso.');
      }
      return;
    }

    // M-055 · un paso quedó esperando permiso humano. Se le muestra al doctor
    // lo que se va a ejecutar; si aprueba, se reintenta el MISMO mensaje con el
    // token. Si rechaza, no se reintenta nada.
    if (!error && resultado && resultado.aprobacion_pendiente
        && resultado.aprobacion_pendiente.tipo !== 'plan' && !tokenAprobacion) {
      const token = await pedirAprobacion(resultado.aprobacion_pendiente);
      if (token) {
        await enviarMensaje(rol, mensaje, { destino, tokenAprobacion: token });
      } else {
        addSystemNote('Acción rechazada: no se ejecutó nada.');
      }
    }
  }

  // ─── Panel Laboratorio (Cowork) ───
  function pintarLabEntry(quien, texto, esError) {
    const empty = els.labStream.querySelector('p');
    if (empty) empty.remove();
    const wrap = document.createElement('div');
    wrap.className = 'flex gap-3';
    const dot = document.createElement('div');
    dot.className = 'mt-1.5 w-2.5 h-2.5 rounded-full shrink-0';
    dot.style.background = quien === 'usuario' ? '#605f53' : (esError ? '#ba1a1a' : '#154212');
    const body = document.createElement('div');
    body.className = 'space-y-1';
    const time = document.createElement('p');
    time.className = 'font-tech-log text-[11px] text-outline';
    time.textContent = new Date().toLocaleTimeString('es-CO', { hour12: false });
    const text = document.createElement('p');
    text.className = 'text-body-sm font-semibold text-on-surface';
    text.textContent = (quien === 'usuario' ? '[Doctor] ' : '[EIR] ') + texto;
    body.appendChild(time);
    body.appendChild(text);
    wrap.appendChild(dot);
    wrap.appendChild(body);
    els.labStream.appendChild(wrap);
    els.labStream.scrollTop = els.labStream.scrollHeight;
    return wrap;
  }

  // ─── Historial / persistencia local ───
  function registrarConversacion(rol, primerMensaje) {
    const existente = state.conversations.find((c) => c.role === rol && c.pinned !== false);
    if (existente) {
      existente.timestamp = Date.now();
      return;
    }
    state.conversations.unshift({
      id: rol + '-' + Date.now(),
      role: rol,
      title: primerMensaje.slice(0, 48),
      timestamp: Date.now(),
    });
    if (state.conversations.length > 30) state.conversations.pop();
  }

  function renderHistory() {
    els.historyList.innerHTML = '';
    if (!state.conversations.length) {
      els.historyEmpty.hidden = false;
      return;
    }
    els.historyEmpty.hidden = true;
    state.conversations.forEach((c) => {
      const li = document.createElement('li');
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'history-item' + (c.role === state.role ? ' active' : '');
      btn.innerHTML =
        '<span class="h-title"></span>' +
        '<span class="h-meta"><span class="h-role"></span><span class="h-time"></span></span>';
      btn.querySelector('.h-title').textContent = c.title || '(sin título)';
      btn.querySelector('.h-role').textContent = ROLES[c.role] ? ROLES[c.role].label.toUpperCase() : c.role;
      btn.querySelector('.h-time').textContent = formatTime(c.timestamp);
      btn.addEventListener('click', () => {
        switchMode('chat');
        switchRole(c.role);
      });
      li.appendChild(btn);
      els.historyList.appendChild(li);
    });
  }

  function formatTime(ts) {
    const diff = Date.now() - ts;
    if (diff < 60000) return 'ahora';
    if (diff < 3600000) return Math.floor(diff / 60000) + 'm';
    if (diff < 86400000) return Math.floor(diff / 3600000) + 'h';
    return new Date(ts).toLocaleDateString('es-CO', { day: '2-digit', month: '2-digit' });
  }

  function saveToStorage() {
    try {
      localStorage.setItem('eir_desktop_v1_state', JSON.stringify({
        conversations: state.conversations,
        historyByRole: state.historyByRole,
      }));
    } catch (e) { /* almacenamiento no disponible: se sigue sin persistencia */ }
  }

  function loadFromStorage() {
    try {
      const raw = localStorage.getItem('eir_desktop_v1_state');
      if (!raw) return;
      const data = JSON.parse(raw);
      if (data.conversations) state.conversations = data.conversations;
      if (data.historyByRole) state.historyByRole = Object.assign(state.historyByRole, data.historyByRole);
    } catch (e) { /* datos corruptos: se ignora y se arranca limpio */ }
  }

  // ─── Sesión cloud EIR (M-052) — el token nunca toca este webview ───
  function pintarSesion(s) {
    const ok = s && s.autenticado === true;
    els.sessionLoggedOut.hidden = ok;
    els.sessionLoggedIn.hidden = !ok;
    if (ok) {
      els.sessionUser.textContent = (s.nombre || s.email || 'Sesión EIR DR.');
      els.sessionCredito.textContent = s.credito_hoy !== undefined
        ? 'Crédito restante hoy: ' + s.credito_hoy
        : (s.tier ? 'Cuenta: ' + s.tier : 'Cuenta EIR DR.');
    } else {
      els.sessionUser.textContent = '—';
      els.sessionCredito.textContent = '';
    }
  }

  async function cargarSesion() {
    try {
      const resp = await fetch('/api/sesion', { cache: 'no-cache' });
      const data = await resp.json().catch(() => ({}));
      state.sesion = data || { autenticado: false };
    } catch (e) {
      state.sesion = { autenticado: false };
    }
    pintarSesion(state.sesion);
  }

  function abrirLogin() {
    els.loginError.hidden = true;
    els.loginModal.classList.remove('hidden');
    els.loginModal.classList.add('flex');
    els.loginEmail.focus();
  }

  function cerrarLogin() {
    els.loginModal.classList.add('hidden');
    els.loginModal.classList.remove('flex');
  }

  async function enviarLogin(e) {
    e.preventDefault();
    els.loginError.hidden = true;
    els.loginSubmitBtn.disabled = true;
    try {
      const resp = await fetch('/api/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: els.loginEmail.value.trim(), password: els.loginPassword.value }),
      });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok || !data.ok) {
        els.loginError.textContent = data.error || 'No se pudo iniciar sesión';
        els.loginError.hidden = false;
      } else {
        els.loginPassword.value = '';
        cerrarLogin();
        await cargarSesion();
      }
    } catch (err) {
      els.loginError.textContent = 'Backend local no disponible';
      els.loginError.hidden = false;
    } finally {
      els.loginSubmitBtn.disabled = false;
    }
  }

  // ─── Aprobación humana de acciones de alto riesgo (M-055 · HITL) ───
  function pedirAprobacion(solicitud) {
    // Devuelve el token si el doctor aprueba, o null si rechaza. Nada se
    // ejecuta mientras esta promesa no resuelva con un token.
    return new Promise((resolve) => {
      const modal = document.getElementById('approval-modal');
      const detalle = document.getElementById('approval-detail');
      const razones = document.getElementById('approval-reasons');
      const badge = document.getElementById('approval-risk');
      const errorEl = document.getElementById('approval-error');
      const btnOk = /** @type {HTMLButtonElement} */ (document.getElementById('approval-approve-btn'));
      const btnNo = /** @type {HTMLButtonElement} */ (document.getElementById('approval-reject-btn'));
      if (!modal) { resolve(null); return; }

      const riesgo = solicitud.riesgo || {};
      detalle.textContent = solicitud.resumen || solicitud.tool || '(sin detalle)';
      badge.textContent = 'riesgo ' + (riesgo.nivel || 'alto');
      razones.innerHTML = '';
      (riesgo.razones || []).forEach((r) => {
        const li = document.createElement('li');
        li.textContent = r;
        razones.appendChild(li);
      });
      errorEl.hidden = true;
      modal.classList.remove('hidden');
      modal.classList.add('flex');

      function cerrar(valor) {
        modal.classList.add('hidden');
        modal.classList.remove('flex');
        btnOk.removeEventListener('click', onOk);
        btnNo.removeEventListener('click', onNo);
        resolve(valor);
      }

      async function onOk() {
        btnOk.disabled = true;
        try {
          const resp = await fetch('/api/shell/aprobar', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              token: solicitud.token, tool: solicitud.tool, args: solicitud.args || {},
            }),
          });
          const data = await resp.json().catch(() => ({}));
          if (!resp.ok || !data.aprobado) {
            // Fail-closed: si el backend no confirma, no se aprueba nada.
            errorEl.textContent = 'No se pudo aprobar: ' + (data.motivo || data.error || ('HTTP ' + resp.status));
            errorEl.hidden = false;
            return;
          }
          cerrar(solicitud.token);
        } catch (e) {
          errorEl.textContent = 'Backend local no disponible';
          errorEl.hidden = false;
        } finally {
          btnOk.disabled = false;
        }
      }

      function onNo() { cerrar(null); }

      btnOk.addEventListener('click', onOk);
      btnNo.addEventListener('click', onNo);
    });
  }

  // ─── Aprobación de un PLAN completo (M-057/M-061) ───
  // Distinto de pedirAprobacion(): aquí se listan todos los pasos propuestos
  // y el "tool" firmado por el backend es literalmente "_plan" (ver
  // core/modo_plan.py::crear_solicitud_plan). Devuelve el token si el doctor
  // aprueba, o null si rechaza.
  function pedirAprobacionPlan(plan, solicitud) {
    return new Promise((resolve) => {
      const modal = document.getElementById('plan-modal');
      const pesoTotal = document.getElementById('plan-peso-total');
      const pasosEl = document.getElementById('plan-pasos');
      const errorEl = document.getElementById('plan-error');
      const btnOk = /** @type {HTMLButtonElement} */ (document.getElementById('plan-approve-btn'));
      const btnNo = /** @type {HTMLButtonElement} */ (document.getElementById('plan-reject-btn'));
      if (!modal || !plan) { resolve(null); return; }

      const pasos = plan.pasos || [];
      pesoTotal.textContent = String(plan.peso_total ?? '—');
      pasosEl.innerHTML = '';
      pasos.forEach((p) => {
        const li = document.createElement('li');
        li.textContent = `${p.tool} (peso ${p.peso}) — ${p.justificacion || 'sin justificación'}`;
        pasosEl.appendChild(li);
      });
      errorEl.hidden = true;
      modal.classList.remove('hidden');
      modal.classList.add('flex');

      function cerrar(valor) {
        modal.classList.add('hidden');
        modal.classList.remove('flex');
        btnOk.removeEventListener('click', onOk);
        btnNo.removeEventListener('click', onNo);
        resolve(valor);
      }

      async function onOk() {
        btnOk.disabled = true;
        try {
          // Reconstruye EXACTAMENTE la forma reducida que el backend firmó
          // (core/modo_plan.py::_reducir): tool/args/justificacion, sin peso.
          const pasosReducidos = pasos.map((p) => ({
            tool: p.tool, args: p.args || {}, justificacion: p.justificacion || '',
          }));
          const resp = await fetch('/api/shell/aprobar', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              token: solicitud.token, tool: '_plan', args: { pasos: pasosReducidos },
            }),
          });
          const data = await resp.json().catch(() => ({}));
          if (!resp.ok || !data.aprobado) {
            errorEl.textContent = 'No se pudo aprobar: ' + (data.motivo || data.error || ('HTTP ' + resp.status));
            errorEl.hidden = false;
            return;
          }
          cerrar(solicitud.token);
        } catch (e) {
          errorEl.textContent = 'Backend local no disponible';
          errorEl.hidden = false;
        } finally {
          btnOk.disabled = false;
        }
      }

      function onNo() { cerrar(null); }

      btnOk.addEventListener('click', onOk);
      btnNo.addEventListener('click', onNo);
    });
  }

  async function logout() {
    try { await fetch('/api/logout', { method: 'POST' }); } catch (e) { /* local */ }
    state.sesion = { autenticado: false };
    await cargarSesion();
  }

  // ─── Señal de versión / auto-update (M-052) ───
  async function cargarVersion() {
    try {
      const resp = await fetch('/api/version', { cache: 'no-cache' });
      const v = await resp.json().catch(() => ({}));
      if (v && v.ok && v.hay_actualizacion) {
        els.updateText.textContent = 'Nueva versión · v' + v.disponible + ' (tienes v' + v.actual + ')';
        els.updateLink.href = v.url_windows || '#';
        els.updateBanner.hidden = false;
      } else {
        els.updateBanner.hidden = true;
      }
    } catch (e) {
      els.updateBanner.hidden = true;
    }
  }

  // M-059/M-061 · motivos estables de OpenCodeServerManager.estado() → texto
  // en español para el doctor (pywebview no tiene consola visible: este
  // panel es el único lugar donde puede enterarse de por qué BYOK no responde).
  const MOTIVOS_OPENCODE = {
    opencode_no_instalado: 'OpenCode no está instalado',
    timeout_arranque: 'OpenCode tardó demasiado en arrancar',
    proceso_crasheo: 'OpenCode se cerró inesperadamente',
    fallo_persistente: 'OpenCode no pudo arrancar tras varios intentos',
    error_arranque: 'Error al iniciar OpenCode',
    error_interno: 'No se pudo consultar el estado de OpenCode',
  };

  async function checkOpencodeEstado() {
    // Diagnóstico honesto, no crítico: si falla o el endpoint no existe en
    // esta versión del backend, el status genérico de /health ya pintado
    // por checkConnection() se queda tal cual (no se sobreescribe con nada).
    try {
      const resp = await fetch('/api/opencode/estado', { cache: 'no-cache' });
      const d = await resp.json().catch(() => null);
      if (!d) return;
      if (d.disponible === false && d.motivo) {
        els.statusText.textContent = MOTIVOS_OPENCODE[d.motivo] || ('OpenCode: ' + d.motivo);
      }
    } catch (e) { /* silencioso a propósito: es un dato adicional, no crítico */ }
  }

  // ─── Estado de conexión (honesto, sin inventar "Pro"/tiers) ───
  async function checkConnection() {
    try {
      const resp = await fetch('/health', { cache: 'no-cache' });
      if (resp.ok) {
        state.connection = 'online';
        els.statusDot.classList.remove('offline');
        els.statusDot.classList.add('online');
        els.statusText.textContent = 'Backend local activo';
        await checkOpencodeEstado();
        return;
      }
      throw new Error('health check fallo');
    } catch (e) {
      state.connection = 'offline';
      els.statusDot.classList.remove('online');
      els.statusDot.classList.add('offline');
      els.statusText.textContent = 'Backend no responde';
    }
  }

  document.addEventListener('DOMContentLoaded', init);
})();
