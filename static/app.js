// ---------- Estado ----------
let appMode = 'cliente';        // 'cliente' | 'interno'
let internalTab = 'dash';       // 'dash' | 'handoff' | 'contato'
let activeChannel = null;       // null | 'site' | 'whatsapp'
let authToken = null;           // RF005 — token de SSO silencioso, emitido ao entrar no canal
let chartCanais = null;         // instância do Chart.js do dashboard (mensagens por canal)

// Precisa ser IDÊNTICA à constante NPS_PROMPT_TEXT em main.py — usada só para
// reconhecer, ao recarregar o histórico, se a pesquisa já foi perguntada e
// ainda não respondida (ver loadHistory).
const NPS_PROMPT_TEXT = (
  "Fico feliz em ajudar! De 0 a 10, o quanto você recomendaria o atendimento da Claro " +
  "para um amigo ou familiar?"
);

function currentCustomer() {
  return document.getElementById('customerSelect').value;
}

// ---------- Toasts de erro (tratamento de erro visível no front) ----------
let _lastToastMsg = null;
let _lastToastAt = 0;

function showToast(message, level = 'error') {
  // Evita toast repetido a cada polling (4s) quando o servidor fica fora do ar
  // por um tempo — sem isso, a tela fica poluída de avisos idênticos.
  const now = Date.now();
  if (message === _lastToastMsg && now - _lastToastAt < 8000) return;
  _lastToastMsg = message;
  _lastToastAt = now;

  const container = document.getElementById('toast-container');
  if (!container) return;
  const toast = document.createElement('div');
  toast.className = `toast toast-${level}`;
  const icon = level === 'warning' ? '⚠️' : '⛔';
  toast.innerHTML = `
    <span class="toast-icon">${icon}</span>
    <span class="toast-body">${message}</span>
    <span class="toast-close" onclick="this.parentElement.remove()">✕</span>`;
  container.appendChild(toast);
  setTimeout(() => toast.remove(), 6000);
}

// ---------- Wrapper central de fetch: toda chamada à API passa por aqui. Se a
// rede cair, o servidor não responder, ou vier um erro HTTP, o usuário vê um
// toast — nunca fica olhando pra tela travada sem explicação. ----------
async function apiFetch(url, options) {
  let res;
  try {
    res = await fetch(url, options);
  } catch (err) {
    showToast('Não foi possível conectar ao servidor. Verifique sua conexão e tente novamente.');
    throw err;
  }
  if (!res.ok) {
    let msg = `Erro ao processar sua solicitação (HTTP ${res.status}).`;
    try {
      const body = await res.clone().json();
      if (body && body.error) msg = body.error;
    } catch (_) { /* corpo não era JSON — mantém a mensagem genérica */ }
    showToast(msg, res.status >= 500 ? 'error' : 'warning');
    throw new Error(msg);
  }
  return res;
}

// ---------- Carregar clientes do banco (nada mais hardcoded no HTML) ----------
async function loadCustomerSelect() {
  try {
    const res = await apiFetch('/api/admin/clientes?limit=100');
    const data = await res.json();
    const select = document.getElementById('customerSelect');
    select.innerHTML = data.items.map(c => `<option value="${c.id}">${c.nome} — ${c.id}</option>`).join('');
  } catch (_) { /* toast já mostrado pelo apiFetch */ }
}
loadCustomerSelect();

// ---------- Alternar entre área do cliente e área interna da Claro ----------
function switchMode(mode) {
  appMode = mode;
  document.querySelectorAll('.mode-btn').forEach(b => b.classList.toggle('active', b.dataset.mode === mode));
  document.getElementById('area-cliente').style.display = mode === 'cliente' ? '' : 'none';
  document.getElementById('area-interno').style.display = mode === 'interno' ? '' : 'none';
  if (mode === 'cliente') {
    if (activeChannel) loadHistory();
  } else {
    if (internalTab === 'dash') loadDashboard();
    if (internalTab === 'handoff') loadHandoff();
    if (internalTab === 'contato') loadContatoLista();
  }
}

function switchInternalTab(tab) {
  internalTab = tab;
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.toggle('active', b.dataset.tab === tab));
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  document.getElementById('panel-' + tab).classList.add('active');
  if (tab === 'dash') loadDashboard();
  if (tab === 'handoff') loadHandoff();
  if (tab === 'contato') loadContatoLista();
}

document.getElementById('customerSelect').addEventListener('change', () => {
  if (appMode === 'cliente' && activeChannel) loadHistory();
  if (appMode === 'interno' && internalTab === 'dash') loadDashboard();
  if (appMode === 'interno' && internalTab === 'handoff') loadHandoff();
});

// ---------- Seleção de canal (fluxo do cliente) ----------
async function selectChannel(channel) {
  activeChannel = channel;
  document.getElementById('channel-picker').style.display = 'none';
  const chatEl = document.getElementById('active-chat');
  chatEl.style.display = '';
  chatEl.classList.add('fade-in');
  setTimeout(() => chatEl.classList.remove('fade-in'), 350);
  applyChannelChrome(channel);
  await silentAuth(channel);
  loadHistory();
}

async function silentAuth(channel) {
  // RF005 — autenticação silenciosa: dispara sozinho, sem pedir login, e emite um
  // token que passa a identificar o cliente nas próximas chamadas.
  const cid = currentCustomer();
  try {
    const res = await apiFetch('/api/auth/silent', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ customer_id: cid, channel }),
    });
    const data = await res.json();
    authToken = data.token;
  } catch (_) {
    authToken = null; // sem token, /api/chat ainda funciona via fallback do customer_id no corpo
  }
}

function showChannelPicker() {
  document.getElementById('active-chat').style.display = 'none';
  document.getElementById('channel-picker').style.display = '';
}

function applyChannelChrome(channel) {
  const frame = document.getElementById('chat-frame');
  const header = document.getElementById('chat-header');
  const name = document.getElementById('chat-name');
  const proactiveBtn = document.getElementById('proactive-btn');
  const quickReplies = document.getElementById('quick-replies');
  const migrateText = document.getElementById('migrate-text');
  const migrateBtn = document.getElementById('migrate-btn');

  frame.className = 'chat-frame ' + (channel === 'site' ? 'chat-site' : 'chat-wa');
  name.textContent = channel === 'site' ? 'Assistente ClaroUnify' : 'ClaroUnify (WhatsApp Business)';
  proactiveBtn.style.display = channel === 'whatsapp' ? '' : 'none';

  if (channel === 'site') {
    quickReplies.innerHTML = `
      <button class="quick-btn" onclick="sendQuick('Minha internet está caindo toda hora')">Internet caindo</button>
      <button class="quick-btn" onclick="sendQuick('Quero conhecer planos com mais dados')">Ver planos</button>
      <button class="quick-btn" onclick="sendQuick('Quero a segunda via da minha fatura')">2ª via fatura</button>
      <button class="quick-btn" onclick="sendQuick('Quero cancelar meu plano')">Cancelar plano</button>`;
    migrateText.textContent = 'Prefere continuar essa conversa no WhatsApp?';
    migrateBtn.textContent = 'Continuar no WhatsApp →';
  } else {
    quickReplies.innerHTML = `
      <button class="quick-btn" onclick="sendQuick('Continua aqui?')">Continua aqui?</button>
      <button class="quick-btn" onclick="sendQuick('Quero falar com atendente')">Falar com atendente</button>`;
    migrateText.textContent = 'Prefere continuar essa conversa no Site Web?';
    migrateBtn.textContent = 'Continuar no Site →';
  }
}

function migrateChannel() {
  const from = activeChannel;
  const to = from === 'site' ? 'whatsapp' : 'site';
  activeChannel = to;
  const chatEl = document.getElementById('active-chat');
  chatEl.classList.add('fade-in');
  setTimeout(() => chatEl.classList.remove('fade-in'), 350);
  applyChannelChrome(to);
  silentAuth(to);  // RF005 — nova "sessão" no canal, sem pedir login de novo
  loadHistory(true, from, to);
}

// ---------- Chat ----------
function bubbleHtml(author, text, originChannel, viewingChannel) {
  const cls = author === 'cliente' ? 'cliente' : 'bot';
  const safe = text.replace(/</g, '&lt;');
  let tag = '';
  if (originChannel && originChannel !== viewingChannel) {
    const label = originChannel === 'site' ? 'via Site Web' : 'via WhatsApp';
    tag = `<div class="origin-tag">↳ recebido ${label}</div>`;
  }
  return `<div class="bubble ${cls}">${tag}${safe}</div>`;
}

async function loadHistory(justMigrated, fromChannel, toChannel) {
  if (!activeChannel) return;
  const cid = currentCustomer();
  try {
    const res = await apiFetch(`/api/session/${cid}`);
    const data = await res.json();
    renderHistory(data.session.history, data.session.status, justMigrated, fromChannel, toChannel);
    const pending = data.session.history.length > 0 && !data.session.consent_given && data.session.status !== 'handoff';
    if (pending) showConsentPrompt();

    // Se a pesquisa já foi perguntada (última mensagem do bot) mas o cliente ainda
    // não respondeu — reexibe o prompt ao trocar de canal ou recarregar a página,
    // em vez de perder a pergunta.
    const last = data.session.history[data.session.history.length - 1];
    const npsPendente = data.session.nps === null && last && last.author === 'bot' && last.text === NPS_PROMPT_TEXT;
    if (npsPendente) showNpsPrompt();
  } catch (_) {
    renderChatError();
  }
}

function renderChatError() {
  const body = document.getElementById('chat-body');
  if (!body || body.querySelector('.bubble-error')) return; // não empilha o mesmo aviso repetidamente
  body.innerHTML += '<div class="bubble system bubble-error">⚠️ Não foi possível carregar a conversa agora. Tentando novamente em instantes...</div>';
  body.scrollTop = body.scrollHeight;
}

function renderHistory(history, status, justMigrated, fromChannel, toChannel) {
  const body = document.getElementById('chat-body');
  body.innerHTML = '';

  if (history.length === 0) {
    body.innerHTML = '<div class="bubble system">Nova conversa — diga "oi" para começar.</div>';
  }

  history.forEach(m => {
    body.innerHTML += bubbleHtml(m.author, m.text, m.channel, activeChannel);
  });

  if (justMigrated) {
    const label = toChannel === 'whatsapp' ? 'WhatsApp' : 'Site Web';
    body.innerHTML += `<div class="bubble system migrate-note">🔀 Você migrou para o ${label} — o Context Manager já trouxe todo o histórico, sem repetir nada.</div>`;
  }

  if (status === 'handoff') {
    body.innerHTML += '<div class="bubble system">🔴 Conversa encaminhada para atendimento humano — veja o Painel de Handoff (área interna Claro).</div>';
  }

  body.scrollTop = body.scrollHeight;
  document.getElementById('chat-status').textContent = status === 'handoff' ? 'aguardando atendente' : 'online';
}

async function sendMessage() {
  const input = document.getElementById('chat-input');
  const text = input.value.trim();
  if (!text) return;
  input.value = '';
  await postMessage(text);
}

async function sendQuick(text) {
  await postMessage(text);
}

async function postMessage(text) {
  const cid = currentCustomer();
  try {
    const res = await apiFetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ customer_id: cid, channel: activeChannel, text, auth_token: authToken }),
    });
    const data = await res.json();
    await loadHistory();
    if (data.needs_consent) showConsentPrompt();
    if (data.ask_nps) showNpsPrompt();
  } catch (_) {
    renderChatError();
  }
}

function showConsentPrompt() {
  const body = document.getElementById('chat-body');
  const box = document.createElement('div');
  box.className = 'bubble system';
  box.innerHTML = `
    <div style="margin-top:6px;">
      <button class="quick-btn" onclick="respondConsent(true)">Aceito</button>
      <button class="quick-btn" onclick="respondConsent(false)" style="border-color:#6B6B70;color:#6B6B70;">Não aceito</button>
    </div>`;
  body.appendChild(box);
  body.scrollTop = body.scrollHeight;
}

async function respondConsent(accepted) {
  const cid = currentCustomer();
  try {
    await apiFetch('/api/consent', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ customer_id: cid, accepted }),
    });
  } catch (_) { /* toast já exibido */ }
  await loadHistory();
}

// ---------- RF006: pesquisa de satisfação (NPS) ----------
function showNpsPrompt() {
  const body = document.getElementById('chat-body');
  if (document.getElementById('nps-box')) return; // evita duplicar se o usuário mandar 2 mensagens rápido
  const box = document.createElement('div');
  box.id = 'nps-box';
  box.className = 'bubble system';
  const botoes = Array.from({ length: 11 }, (_, n) =>
    `<button class="quick-btn nps-btn" onclick="respondNps(${n})">${n}</button>`
  ).join('');
  box.innerHTML = `<div class="nps-scale" style="margin-top:6px; display:flex; flex-wrap:wrap; gap:4px;">${botoes}</div>`;
  body.appendChild(box);
  body.scrollTop = body.scrollHeight;
}

async function respondNps(score) {
  const cid = currentCustomer();
  try {
    await apiFetch('/api/nps', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ customer_id: cid, score, channel: activeChannel }),
    });
  } catch (_) { /* toast já exibido */ }
  await loadHistory();
}

async function dispararCampanha() {
  const cid = currentCustomer();
  try {
    await apiFetch(`/api/campanha-proativa/${cid}`, { method: 'POST' });
    await loadHistory();
  } catch (_) { /* toast já exibido */ }
}

// ---------- Dashboard ----------
async function loadDashboard() {
  let kpis, evts, alertasData, cap;
  try {
    const [kpisRes, eventsRes, alertasRes, capRes] = await Promise.all([
      apiFetch('/api/kpis'), apiFetch('/api/events?limit=25'), apiFetch('/api/alertas'), apiFetch('/api/capacidade'),
    ]);
    kpis = await kpisRes.json();
    evts = await eventsRes.json();
    alertasData = await alertasRes.json();
    cap = await capRes.json();
  } catch (_) {
    document.getElementById('kpi-grid').innerHTML = '<div class="loading-inline">⚠️ Não foi possível carregar o dashboard agora.</div>';
    return;
  }

  document.getElementById('kpi-grid').innerHTML = `
    ${kpiCard('Conversas ativas', kpis.conversas_ativas)}
    ${kpiCard('Total de conversas', kpis.total_conversas)}
    ${kpiCard('Taxa de resolução automática', kpis.taxa_resolucao_pct + '%')}
    ${kpiCard('Volume de handoff', kpis.volume_handoff_pct + '%')}
    ${kpiCard('NPS conversacional médio', kpis.nps_medio !== null ? kpis.nps_medio : '—')}
    ${kpiCard('Custo/atendimento (estimado)', kpis.custo_por_atendimento_estimado !== null ? 'R$ ' + kpis.custo_por_atendimento_estimado.toFixed(2) : '—')}
  `;

  document.getElementById('kpi-canais').innerHTML = `
    ${kpiCard('Site Web', kpis.mensagens_por_canal.site || 0, true)}
    ${kpiCard('WhatsApp', kpis.mensagens_por_canal.whatsapp || 0, true)}
    ${kpiCard('Total de eventos', kpis.total_eventos, true)}
  `;
  renderChartCanais(kpis.mensagens_por_canal);

  const rpc = kpis.taxa_resolucao_por_canal || {};
  document.getElementById('kpi-resolucao-canal').innerHTML = `
    ${kpiCard('Resolução — Site Web', rpc.site !== null && rpc.site !== undefined ? rpc.site + '%' : '—', true)}
    ${kpiCard('Resolução — WhatsApp', rpc.whatsapp !== null && rpc.whatsapp !== undefined ? rpc.whatsapp + '%' : '—', true)}
  `;

  const r001 = cap.rnf001_tempo_resposta || {};
  const okClass = r001.dentro_da_meta ? 'rnf-ok' : 'rnf-fora';
  const okLabel = r001.amostras_medidas === 0 ? 'sem amostras ainda' : (r001.dentro_da_meta ? '✅ dentro da meta' : '⚠️ fora da meta');
  document.getElementById('rnf001-box').innerHTML = `
    <div class="rnf-line ${okClass}">
      <b>RNF001 (P95 ≤ 2000ms):</b> P95 medido = ${r001.p95_ms !== null ? r001.p95_ms + 'ms' : '—'}
      (${r001.amostras_medidas} amostras reais) — ${okLabel}
    </div>`;

  const alertBox = document.getElementById('alertas-box');
  if (alertasData.alertas.length === 0) {
    alertBox.innerHTML = '<div class="alert-ok">✅ Nenhum alerta ativo no momento.</div>';
  } else {
    alertBox.innerHTML = alertasData.alertas.map(a => `
      <div class="alert-item alert-${a.severidade}">
        <b>${a.tipo.replace(/_/g, ' ')}</b> — ${a.mensagem}
      </div>`).join('');
  }

  document.getElementById('event-log').innerHTML = evts.map(e => eventRow(e)).join('') || '<div class="event-row">Nenhum evento ainda.</div>';
}

// ---------- Gráfico de mensagens por canal (Chart.js) ----------
function renderChartCanais(porCanal) {
  const canvas = document.getElementById('chart-canais');
  if (!canvas) return;
  if (typeof Chart === 'undefined') {
    // Chart.js agora é servido localmente (/static/chart.umd.js) — isso não
    // deveria mais depender de internet. Se ainda assim não carregou, avisa
    // em vez de deixar a área em branco sem explicação nenhuma.
    canvas.parentElement.innerHTML = '<div class="loading-inline">⚠️ Não foi possível carregar a biblioteca do gráfico.</div>';
    return;
  }

  const dados = {
    labels: ['Site Web', 'WhatsApp'],
    datasets: [{
      label: 'Mensagens recebidas',
      data: [porCanal.site || 0, porCanal.whatsapp || 0],
      backgroundColor: ['#E8290B', '#075E54'],
      borderRadius: 6,
      maxBarThickness: 64,
    }],
  };

  if (chartCanais) {
    chartCanais.data = dados;
    chartCanais.update();
    return;
  }

  chartCanais = new Chart(canvas.getContext('2d'), {
    type: 'bar',
    data: dados,
    options: {
      responsive: true,
      plugins: { legend: { display: false } },
      scales: { y: { beginAtZero: true, ticks: { precision: 0 } } },
    },
  });
}

function kpiCard(label, value, small) {
  return `<div class="kpi-card"><div class="label">${label}</div><div class="value ${small ? 'small' : ''}">${value}</div></div>`;
}

function eventRow(e) {
  const time = new Date(e.ts * 1000).toLocaleTimeString('pt-BR');
  return `<div class="event-row">
    <span class="event-tag ${e.type}">${e.type}</span>
    <span>${time} · cliente ${e.customer_id} — ${describeEvent(e)}</span>
  </div>`;
}

function describeEvent(e) {
  switch (e.type) {
    case 'mensagem_cliente': return `"${e.text}" (${e.channel})`;
    case 'resposta_bot': return `${e.agent} respondeu (intenção: ${e.intent}, motor: ${e.nlu_source || 'keywords'})`;
    case 'handoff': return `escalado — ${e.motivo}`;
    case 'handoff_assumido': return 'atendente assumiu a conversa';
    case 'nps': return `avaliação NPS = ${e.score}`;
    case 'campanha_proativa': return 'notificação proativa disparada';
    case 'consentimento': return `consentimento LGPD: ${e.aceito ? 'aceito' : 'recusado'}`;
    case 'rate_limit_bloqueado': return 'bloqueado por rate limit';
    default: return '';
  }
}

// ---------- Handoff ----------
async function loadHandoff() {
  const el = document.getElementById('handoff-content');
  let queue;
  try {
    const res = await apiFetch('/api/handoff-queue');
    queue = await res.json();
  } catch (_) {
    el.innerHTML = '<div class="handoff-empty">⚠️ Não foi possível carregar a fila de handoff agora.</div>';
    return;
  }

  if (queue.length === 0) {
    el.innerHTML = '<div class="handoff-empty">Nenhuma conversa em fila de handoff no momento.</div>';
    return;
  }
  el.innerHTML = queue.map(item => handoffCard(item)).join('');
}

function handoffCard(item) {
  const transcript = item.history.slice(-8).map(m =>
    `<div class="line"><b>${m.author === 'cliente' ? 'Cliente' : 'Bot'}:</b> ${m.text}</div>`
  ).join('');
  const acoes = suggestedActions(item.last_intent);

  return `
  <div class="handoff-card">
    <div class="handoff-col">
      <h3>${item.nome}</h3>
      <div class="meta">${item.plano} · cliente desde ${item.cliente_desde} · NPS histórico ${item.nps_historico}</div>
      <div class="meta">Intenção detectada: <b>${item.last_intent}</b></div>
      <div class="transcript">${transcript}</div>
    </div>
    <div class="handoff-col">
      <h3>Ações sugeridas</h3>
      <div class="suggested-actions">${acoes.map(a => `<button>${a}</button>`).join('')}</div>
      <button class="assume-btn" onclick="assumirHandoff('${item.customer_id}')">Assumir atendimento</button>
      <div class="note-box">O contexto completo foi transferido automaticamente. O cliente não precisará repetir informações.</div>
    </div>
  </div>`;
}

function suggestedActions(intent) {
  if (intent === 'cancelamento') return ['Oferecer desconto de retenção', 'Verificar histórico de reclamações', 'Propor upgrade de plano'];
  if (intent === 'handoff_explicito') return ['Confirmar motivo do contato', 'Verificar histórico recente'];
  return ['Verificar plano atual', 'Checar histórico de atendimentos'];
}

async function assumirHandoff(customerId) {
  try {
    await apiFetch(`/api/handoff-queue/${customerId}/assumir`, { method: 'POST' });
    await loadHandoff();
  } catch (_) { /* toast já exibido */ }
}

// ---------- Iniciar Contato (Painel Claro escolhe o cliente) ----------
let contatoFiltro = { q: '', fatura_status: '', status_rede: '' };
let contatoPagina = 0;
const CONTATO_POR_PAGINA = 8;

async function loadContatoLista() {
  const params = new URLSearchParams({
    q: contatoFiltro.q,
    fatura_status: contatoFiltro.fatura_status,
    status_rede: contatoFiltro.status_rede,
    limit: CONTATO_POR_PAGINA,
    offset: contatoPagina * CONTATO_POR_PAGINA,
  });
  try {
    const res = await apiFetch(`/api/admin/clientes?${params}`);
    const data = await res.json();

    document.getElementById('contato-lista').innerHTML = data.items.map(c => contatoCard(c)).join('')
      || '<div class="handoff-empty">Nenhum cliente bate com esse filtro.</div>';

    renderPaginacao(data.total);
    document.getElementById('contato-total-filtrado').textContent =
      `${data.total} cliente${data.total === 1 ? '' : 's'} nesse filtro`;
  } catch (_) {
    document.getElementById('contato-lista').innerHTML = '<div class="loading-inline">⚠️ Não foi possível carregar a lista de clientes.</div>';
  }
}

function renderPaginacao(total) {
  const totalPaginas = Math.max(1, Math.ceil(total / CONTATO_POR_PAGINA));
  const el = document.getElementById('contato-paginacao');
  const inicio = total === 0 ? 0 : contatoPagina * CONTATO_POR_PAGINA + 1;
  const fim = Math.min(total, (contatoPagina + 1) * CONTATO_POR_PAGINA);
  el.innerHTML = `
    <button class="quick-btn" ${contatoPagina === 0 ? 'disabled' : ''} onclick="mudarPagina(-1)">← Anterior</button>
    <span class="pagina-info">${inicio}-${fim} de ${total}</span>
    <button class="quick-btn" ${contatoPagina >= totalPaginas - 1 ? 'disabled' : ''} onclick="mudarPagina(1)">Próxima →</button>
  `;
}

function mudarPagina(delta) {
  contatoPagina = Math.max(0, contatoPagina + delta);
  loadContatoLista();
}

function aplicarFiltroContato() {
  contatoFiltro.q = document.getElementById('contato-busca').value;
  contatoPagina = 0;
  loadContatoLista();
}

function aplicarSegmento(fatura_status, status_rede) {
  contatoFiltro.fatura_status = fatura_status;
  contatoFiltro.status_rede = status_rede;
  contatoPagina = 0;
  document.querySelectorAll('.segmento-chip').forEach(b => b.classList.remove('active'));
  document.getElementById(`chip-${fatura_status || status_rede || 'todos'}`).classList.add('active');
  loadContatoLista();
}

function contatoCard(c) {
  const faturaLabel = c.fatura_status === 'atrasada' ? '🔴 atrasada'
    : c.fatura_status === 'em_aberto' ? '🟡 em aberto' : '🟢 paga';
  const redeLabel = c.status_rede === 'instabilidade' ? '🔴 instabilidade' : '🟢 normal';

  return `
  <div class="contato-card">
    <div class="contato-info">
      <div class="contato-nome">${c.nome}</div>
      <div class="contato-meta">${c.plano} · ${c.id} · fatura ${faturaLabel} · rede ${redeLabel}</div>
    </div>
    <div class="contato-form">
      <select id="canal-${c.id}">
        <option value="whatsapp">WhatsApp</option>
        <option value="site">Site Web</option>
      </select>
      <select id="motivo-${c.id}" onchange="toggleMensagemPersonalizada('${c.id}')">
        <option value="fatura_vencendo">Fatura vencendo</option>
        <option value="retencao">Retenção</option>
        <option value="upgrade_oferta">Oferta de upgrade</option>
        <option value="outro">Outro assunto</option>
      </select>
      <input type="text" id="msg-${c.id}" placeholder="Mensagem personalizada..." style="display:none;">
      <button class="send-btn" onclick="iniciarContato('${c.id}')">Iniciar contato</button>
    </div>
    <div class="contato-resultado" id="resultado-${c.id}"></div>
  </div>`;
}

function toggleMensagemPersonalizada(customerId) {
  const motivo = document.getElementById(`motivo-${customerId}`).value;
  document.getElementById(`msg-${customerId}`).style.display = motivo === 'outro' ? '' : 'none';
}

async function iniciarContato(customerId) {
  const channel = document.getElementById(`canal-${customerId}`).value;
  const motivo = document.getElementById(`motivo-${customerId}`).value;
  const mensagem_personalizada = document.getElementById(`msg-${customerId}`).value;
  const resultadoEl = document.getElementById(`resultado-${customerId}`);

  try {
    const res = await apiFetch('/api/admin/iniciar-contato', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ customer_id: customerId, channel, motivo, mensagem_personalizada }),
    });
    const data = await res.json();
    if (data.ok) {
      resultadoEl.innerHTML = `<div class="contato-ok">✅ Enviado via ${channel}: "${data.mensagem}"</div>`;
    } else {
      resultadoEl.innerHTML = `<div class="contato-erro">⚠️ ${data.error}</div>`;
    }
  } catch (_) {
    resultadoEl.innerHTML = '<div class="contato-erro">⚠️ Não foi possível enviar o contato agora.</div>';
  }
}

// ---------- Contato em massa por segmento ----------
function toggleMensagemPersonalizadaLote() {
  const motivo = document.getElementById('lote-motivo').value;
  document.getElementById('lote-msg').style.display = motivo === 'outro' ? '' : 'none';
}

async function iniciarContatoLote() {
  const channel = document.getElementById('lote-canal').value;
  const motivo = document.getElementById('lote-motivo').value;
  const mensagem_personalizada = document.getElementById('lote-msg').value;
  const resultadoEl = document.getElementById('lote-resultado');

  const confirmado = confirm(
    `Isso vai iniciar contato com TODOS os clientes que batem com o filtro atual ` +
    `(${document.getElementById('contato-total-filtrado').textContent}). Confirmar?`
  );
  if (!confirmado) return;

  try {
    const res = await apiFetch('/api/admin/iniciar-contato-lote', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        channel, motivo, mensagem_personalizada,
        q: contatoFiltro.q, fatura_status: contatoFiltro.fatura_status, status_rede: contatoFiltro.status_rede,
      }),
    });
    const data = await res.json();
    if (data.ok) {
      resultadoEl.innerHTML = `<div class="contato-ok">✅ Contato iniciado com ${data.total_enviado} de ${data.total_filtrado} clientes do filtro.</div>`;
    } else {
      resultadoEl.innerHTML = `<div class="contato-erro">⚠️ ${data.error}</div>`;
    }
  } catch (_) {
    resultadoEl.innerHTML = '<div class="contato-erro">⚠️ Não foi possível iniciar o contato em massa agora.</div>';
  }
}

// ---------- Reset ----------
async function resetAll() {
  try {
    await apiFetch('/api/reset', { method: 'POST' });
  } catch (_) {
    return; // toast já mostrado — não desmonta a tela se o reset falhou de fato
  }
  showChannelPicker();
  activeChannel = null;
  if (appMode === 'interno') {
    if (internalTab === 'dash') loadDashboard();
    if (internalTab === 'handoff') loadHandoff();
    if (internalTab === 'contato') loadContatoLista();
  }
}

// ---------- Polling leve ----------
setInterval(() => {
  if (appMode === 'cliente' && activeChannel) loadHistory();
  if (appMode === 'interno' && internalTab === 'dash') loadDashboard();
  if (appMode === 'interno' && internalTab === 'handoff') loadHandoff();
}, 4000);
