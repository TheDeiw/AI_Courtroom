// chat.js — owner: C
import { api, MOCK, formatDate, setText, el } from './common.js';

// ─── Nick persistence ──────────────────────────────────
let nick = '';
try {
  nick = localStorage.getItem('chat_nick') || '';
} catch {}

const nickInput = document.getElementById('chat-nick');
if (nickInput && nick) nickInput.value = nick;

window.saveNick = function(v) {
  nick = v.trim();
  try { localStorage.setItem('chat_nick', nick); } catch {}
};

// ─── Char counter for message ──────────────────────────
document.getElementById('chat-message')?.addEventListener('input', function() {
  setText(document.getElementById('msg-char-count'), this.value.length + ' / 500');
});

// ─── State ─────────────────────────────────────────────
let autoScroll = true;
let lastMsgId  = 0;
let knownIncidentIds = new Set();

// Nick color hash
function nickColor(username) {
  let h = 0;
  for (const c of (username || '')) h = (h * 31 + c.charCodeAt(0)) & 0xfffff;
  return `nick-color-${h % 8}`;
}

// Time formatter
function msgTime(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  return d.getHours().toString().padStart(2,'0') + ':' + d.getMinutes().toString().padStart(2,'0');
}

// ─── Render chat messages ───────────────────────────────
function renderMessages(messages, incidents) {
  const container = document.getElementById('chat-messages');

  // Merge messages and incidents into a unified timeline
  const timeline = [];
  messages.forEach(m => timeline.push({ type: 'msg', ts: new Date(m.created_at).getTime(), data: m }));
  incidents.forEach(i => {
    if (!knownIncidentIds.has(i.id)) {
      timeline.push({ type: 'incident', ts: new Date(i.created_at).getTime(), data: i });
    }
  });
  timeline.sort((a, b) => a.ts - b.ts);

  // Full redraw (as per architecture spec)
  container.innerHTML = '';

  let checked = 0, removed = 0, timeouts = 0;

  timeline.forEach(item => {
    if (item.type === 'msg') {
      const m = item.data;
      const msgEl = buildMessageEl(m);
      container.appendChild(msgEl);

      if (['flagged','acquitted','removed'].includes(m.status)) checked++;
      if (m.status === 'removed') removed++;
    } else {
      const i = item.data;
      knownIncidentIds.add(i.id);
      const sysEl = buildSystemEl(i);
      container.appendChild(sysEl);
      if (i.action === 'timeout') timeouts++;
    }
  });

  // Update AI stats
  setText(document.getElementById('ai-checked'), checked);
  setText(document.getElementById('ai-removed'), removed);
  setText(document.getElementById('ai-timeouts'), timeouts);

  // Update message count badge
  setText(document.getElementById('chat-count'), messages.length + ' messages');

  // Auto-scroll
  if (autoScroll) {
    container.scrollTop = container.scrollHeight;
  }
}

function buildMessageEl(m) {
  const div = el('div', `msg msg-${m.status}`);

  const timeEl = el('span', 'msg-time');
  timeEl.textContent = msgTime(m.created_at);

  const body = el('div', 'msg-body');

  const nickEl = el('span', `msg-nick ${nickColor(m.username)}`);
  nickEl.textContent = m.username + ':';

  const textEl = el('span', 'msg-text');

  if (m.status === 'removed') {
    // Don't show removed message text — XSS safe, just show placeholder
    textEl.textContent = '';
  } else {
    textEl.textContent = ' ' + (m.text || '');
  }

  if (m.status === 'flagged') {
    const ind = el('span', 'msg-flagged-indicator');
    ind.textContent = ' ⏳';
    ind.title = 'AI is reviewing this message';
    body.appendChild(nickEl);
    body.appendChild(textEl);
    body.appendChild(ind);
  } else {
    body.appendChild(nickEl);
    body.appendChild(textEl);
  }

  div.appendChild(timeEl);
  div.appendChild(body);
  return div;
}

function buildSystemEl(incident) {
  const isTimeout = incident.action === 'timeout';
  const div = el('div', `msg-system ${incident.action}`);

  const icon = el('span', 'msg-system-icon');
  icon.textContent = isTimeout ? '⏱' : '🔨';

  const text = el('span', 'msg-system-text');
  const strong = el('strong');
  strong.textContent = incident.username;

  const detail = document.createTextNode(
    isTimeout
      ? ` received a ${incident.duration_min}-min timeout: ${incident.reason}`
      : ` was banned: ${incident.reason}`
  );

  text.appendChild(strong);
  text.appendChild(detail);
  div.appendChild(icon);
  div.appendChild(text);
  return div;
}

// ─── Poll & refresh ─────────────────────────────────────
async function refresh() {
  try {
    const [messages, incidents] = await Promise.all([
      api('/api/messages?channel=main&limit=50'),
      api('/api/incidents?channel=main'),
    ]);
    renderMessages(messages, incidents);

    // Check if current user is banned
    if (nick) {
      const myBan = incidents.find(i => i.username === nick && i.active);
      showBanBanner(myBan);
    }
  } catch (err) {
    console.warn('Chat refresh failed:', err);
  }
}

// ─── Ban banner ──────────────────────────────────────────
function showBanBanner(incident) {
  const banner = document.getElementById('ban-banner');
  if (!incident) {
    banner.classList.add('hidden');
    return;
  }
  banner.classList.remove('hidden');
  setText(document.getElementById('ban-banner-title'),
    incident.action === 'ban' ? 'You are banned from this chat' : `You have an active timeout (${incident.duration_min} min)`);
  setText(document.getElementById('ban-banner-reason'), incident.reason);

  const link = document.getElementById('ban-banner-appeal');
  link.href = `appeal.html?incident=${incident.id}&username=${encodeURIComponent(nick)}`;
}

// ─── Send message ────────────────────────────────────────
window.sendMessage = async function() {
  const msgEl = document.getElementById('chat-message');
  const text  = msgEl.value.trim();

  if (!nick) {
    nickInput?.focus();
    nickInput?.classList.add('shake');
    setTimeout(() => nickInput?.classList.remove('shake'), 400);
    return;
  }
  if (!text) return;

  const btn = document.getElementById('send-btn');
  btn.disabled = true;

  try {
    const result = await api('/api/messages', {
      method: 'POST',
      body: { channel: 'main', username: nick, text },
    });

    msgEl.value = '';
    setText(document.getElementById('msg-char-count'), '0 / 500');
    await refresh();

  } catch (err) {
    if (err.status === 403 && err.data?.detail) {
      const d = err.data.detail;
      showBanBanner({ ...d, username: nick, active: true });
    } else {
      console.error('Send failed:', err);
    }
  } finally {
    btn.disabled = false;
  }
};

window.handleChatKey = function(e) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    window.sendMessage();
  }
};

// ─── Auto-scroll toggle ──────────────────────────────────
window.toggleScroll = function() {
  autoScroll = !autoScroll;
  const btn = document.getElementById('scroll-toggle');
  if (btn) {
    btn.textContent = autoScroll ? '⬇ Auto-scroll' : '⏸ Paused';
    btn.classList.toggle('active', autoScroll);
  }
};

// Re-enable auto-scroll when user scrolls to bottom
document.getElementById('chat-messages')?.addEventListener('scroll', function() {
  const el = this;
  const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
  if (atBottom && !autoScroll) {
    autoScroll = true;
    const btn = document.getElementById('scroll-toggle');
    if (btn) { btn.textContent = '⬇ Auto-scroll'; btn.classList.add('active'); }
  }
});

// ─── Demo replay ─────────────────────────────────────────
let demoTimer = null;
let demoRunning = false;

window.startDemoReplay = async function() {
  if (demoRunning) return;

  let script;
  try {
    const r = await fetch('demo/chat_script.json');
    script = await r.json();
  } catch {
    alert('Could not load demo script.');
    return;
  }

  demoRunning = true;
  document.getElementById('btn-demo').style.display = 'none';
  document.getElementById('btn-stop-demo').style.display = '';

  // Add progress bar
  let progressEl = document.getElementById('demo-progress');
  if (!progressEl) {
    progressEl = el('div', 'demo-progress');
    progressEl.id = 'demo-progress';
    const totalDuration = script[script.length - 1]?.delay_ms || 1;
    progressEl.innerHTML = `<span>▶ Demo running…</span>
      <div class="demo-progress-bar-wrap">
        <div class="demo-progress-bar" id="demo-bar" style="width:0%"></div>
      </div>`;
    document.querySelector('.chat-input-area').before(progressEl);
  }

  const totalMs = script[script.length - 1]?.delay_ms || 1;
  const startTs = Date.now();

  const progressInterval = setInterval(() => {
    const elapsed = Date.now() - startTs;
    const pct = Math.min(elapsed / totalMs * 100, 100);
    const bar = document.getElementById('demo-bar');
    if (bar) bar.style.width = pct + '%';
  }, 100);

  for (const msg of script) {
    if (!demoRunning) break;
    await new Promise(r => { demoTimer = setTimeout(r, msg.delay_ms); });
    if (!demoRunning) break;
    try {
      await api('/api/messages', {
        method: 'POST',
        body: { channel: 'main', username: msg.username, text: msg.text },
      });
      await refresh();
    } catch {}
  }

  clearInterval(progressInterval);
  progressEl.remove();
  demoRunning = false;
  document.getElementById('btn-demo').style.display = '';
  document.getElementById('btn-stop-demo').style.display = 'none';
};

window.stopDemoReplay = function() {
  demoRunning = false;
  if (demoTimer) clearTimeout(demoTimer);
  const progressEl = document.getElementById('demo-progress');
  if (progressEl) progressEl.remove();
  document.getElementById('btn-demo').style.display = '';
  document.getElementById('btn-stop-demo').style.display = 'none';
};

// ─── Simulated viewer count wobble (for realism) ──────────
(function() {
  const base = 1247;
  function updateViewers() {
    const delta = Math.floor((Math.random() - 0.5) * 30);
    const count = Math.max(1000, base + delta);
    setText(document.getElementById('viewer-count'), count.toLocaleString('en'));
  }
  setInterval(updateViewers, 5000);
})();

// ─── Start polling ────────────────────────────────────────
refresh();
setInterval(refresh, 1500);
