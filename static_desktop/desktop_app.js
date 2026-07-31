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
  };

  const el = (id) => document.getElementById(id);

  const els = {
    modeBtns: () => Array.from(document.querySelectorAll('.mode-btn')),
    roleBtns: () => Array.from(document.querySelectorAll('.role-btn')),
    panelChat: el('panel-chat'),
    panelCowork: el('panel-cowork'),
    panelAuto: el('panel-auto'),
    coworkGeneric: el('cowork-generic'),
    coworkLab: el('cowork-lab'),
    messages: el('messages'),
    welcome: el('welcome-message'),
    chatForm: el('chat-form'),
    chatInput: el('chat-input'),
    sendBtn: el('send-btn'),
    tracePanel: el('trace-panel'),
    traceContent: el('trace-content'),
    labForm: el('lab-chat-form'),
    labInput: el('lab-chat-input'),
    labStream: el('lab-thought-stream'),
    statusDot: el('status-dot'),
    statusText: el('status-text'),
    historyList: el('history-list'),
    historyEmpty: el('history-empty'),
    clearHistoryBtn: el('clear-history-btn'),
    footerRoleLabel: el('footer-role-label'),
    settingsBtn: el('settings-btn'),
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
      nombre.textContent = (p.herramienta || '?') + (p.ok ? ' · OK' : ' · FAIL (' + (p.motivo || 'sin motivo') + ')');
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

  async function enviarMensaje(rol, mensaje, { destino }) {
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

    let resultado = null;
    let error = null;
    try {
      const resp = await fetch('/api/shell/conversar', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ rol, mensaje, historial: historialPrevio }),
      });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        error = data.error ? `${data.error}${data.detalle ? ': ' + data.detalle : ''}` : `HTTP ${resp.status}`;
      } else {
        resultado = data.resultado || {};
      }
    } catch (e) {
      error = 'backend no disponible (' + e.message + ')';
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

  // ─── Estado de conexión (honesto, sin inventar "Pro"/tiers) ───
  async function checkConnection() {
    try {
      const resp = await fetch('/health', { cache: 'no-cache' });
      if (resp.ok) {
        state.connection = 'online';
        els.statusDot.classList.remove('offline');
        els.statusDot.classList.add('online');
        els.statusText.textContent = 'Backend local activo';
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
