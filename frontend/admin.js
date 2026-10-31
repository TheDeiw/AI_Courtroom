// admin.js — owner: C
import { api, MOCK, formatDate, setText, el } from './common.js';

// ─── Tab management ───────────────────────────────────
let activeTab = 'settings';

window.switchTab = function(tab) {
  activeTab = tab;
  document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.sidebar-item').forEach(i => i.classList.remove('active'));
  document.getElementById(`tab-${tab}`).classList.add('active');
  document.querySelector(`[data-tab="${tab}"]`).classList.add('active');
};

// ─── Modal state ──────────────────────────────────────
let modalAppealId = null;

window.closeModal = function() {
  document.getElementById('modal-overlay').classList.add('hidden');
  modalAppealId = null;
};

function openDecisionModal(appeal) {
  modalAppealId = appeal.id;
  setText(document.getElementById('modal-title'), `Decision for @${appeal.username}`);
  document.getElementById('modal-appeal-text').textContent = appeal.text;
  document.getElementById('modal-note').value = '';
  document.getElementById('modal-overlay').classList.remove('hidden');
}

window.submitDecision = async function(decision) {
  if (!modalAppealId) return;
  const note = document.getElementById('modal-note').value.trim();
  const btns = [document.getElementById('btn-uphold'), document.getElementById('btn-overturn')];
  btns.forEach(b => b.disabled = true);
  try {
    await api(`/api/appeals/${modalAppealId}/decision`, {
      method: 'POST',
      body: { decision, note },
    });
    closeModal();
    refresh();
  } catch (err) {
    alert('Failed to submit decision: ' + (err.data?.detail || err.message));
  } finally {
    btns.forEach(b => b.disabled = false);
  }
};

// ─── Settings ─────────────────────────────────────────
async function loadSettings() {
  try {
    const ch = await api('/api/channels/main');
    document.getElementById('rules-textarea').value   = ch.rules || '';
    document.getElementById('context-textarea').value = ch.stream_context || '';
  } catch {}
}

window.saveSettings = async function() {
  const rules   = document.getElementById('rules-textarea').value;
  const context = document.getElementById('context-textarea').value;
  const btn     = document.getElementById('btn-save-settings');
  const status  = document.getElementById('save-status');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> Saving…';
  try {
    await api('/api/channels/main', { method: 'PUT', body: { rules, stream_context: context } });
    status.textContent = '✓ Saved successfully';
    setTimeout(() => { status.textContent = ''; }, 3000);
  } catch {
    status.style.color = 'var(--danger)';
    status.textContent = '✗ Save failed';
    setTimeout(() => { status.textContent = ''; status.style.color = 'var(--success)'; }, 3000);
  } finally {
    btn.disabled = false;
    btn.innerHTML = '<span>💾</span> Save Settings';
  }
};

// ─── Court Queue ──────────────────────────────────────
async function loadCourtQueue() {
  try {
    const appeals = await api('/api/appeals?status=court_queue');
    renderCourtQueue(appeals);
    updateBadge('badge-court', appeals.length);
  } catch {}
}

function renderCourtQueue(appeals) {
  const container = document.getElementById('court-queue-list');
  if (!appeals || appeals.length === 0) {
    container.innerHTML = `<div class="empty-state"><div class="empty-icon">⚖️</div><p>No cases in the court queue</p></div>`;
    return;
  }
  container.innerHTML = '';
  appeals.forEach(appeal => {
    const card = buildCourtCard(appeal);
    container.appendChild(card);
  });
}

function buildCourtCard(appeal) {
  const card = el('div', 'queue-card');

  // Header
  const header = el('div', 'queue-card-header');
  const userDiv = el('div', 'queue-user');
  const avatar  = el('div', 'queue-avatar');
  avatar.textContent = appeal.username?.[0]?.toUpperCase() || '?';
  const userInfo = el('div', 'queue-user-info');
  const username = el('div', 'queue-username');
  username.textContent = '@' + appeal.username;
  const meta = el('div', 'queue-meta');
  meta.textContent = `Attempt #${appeal.attempt_no} · ${formatDate(appeal.created_at)}`;
  userInfo.appendChild(username);
  userInfo.appendChild(meta);
  userDiv.appendChild(avatar);
  userDiv.appendChild(userInfo);
  header.appendChild(userDiv);

  // Badges
  const badges = el('div', 'queue-badges');
  const actionBadge = el('span', `badge badge-${appeal.incident?.action === 'ban' ? 'danger' : 'warning'}`);
  actionBadge.textContent = (appeal.incident?.action || '').toUpperCase();
  const ruleBadge = el('span', 'badge badge-muted');
  ruleBadge.textContent = appeal.incident?.rule || '';
  badges.appendChild(actionBadge);
  badges.appendChild(ruleBadge);
  header.appendChild(badges);
  card.appendChild(header);

  // Appeal text
  const textBlock = el('div', 'appeal-text-block');
  textBlock.textContent = appeal.text || '';
  card.appendChild(textBlock);

  // Triage info
  if (appeal.triage) {
    const t = appeal.triage;
    const stars = '★'.repeat(Math.round((t.interest_score || 0) / 2)) +
                  '☆'.repeat(5 - Math.round((t.interest_score || 0) / 2));

    const triageDiv = el('div');

    const r1 = el('div', 'triage-row');
    r1.innerHTML = '';
    const l1 = el('span', 'triage-label'); l1.textContent = 'Interest score';
    const s1 = el('span', 'stars'); s1.textContent = stars;
    const v1 = el('span', 'text-muted'); v1.textContent = ` ${t.interest_score}/10`;
    r1.appendChild(l1); r1.appendChild(s1); r1.appendChild(v1);

    const r2 = el('div', 'triage-row');
    const l2 = el('span', 'triage-label'); l2.textContent = 'AI summary';
    const v2 = el('span'); v2.textContent = t.summary || '';
    r2.appendChild(l2); r2.appendChild(v2);

    const r3 = el('div', 'triage-row');
    const l3 = el('span', 'triage-label'); l3.textContent = 'AI reason';
    const v3 = el('span', 'text-muted'); v3.textContent = t.reason || '';
    r3.appendChild(l3); r3.appendChild(v3);

    triageDiv.appendChild(r1);
    triageDiv.appendChild(r2);
    triageDiv.appendChild(r3);
    card.appendChild(triageDiv);
  }

  // Case status
  if (appeal.case) {
    const caseRow = el('div', 'case-status-row');
    const caseLabel = el('span', 'case-status-label'); caseLabel.textContent = 'Case status:';
    const caseVal = el('span');

    const s = appeal.case.status;
    if (s === 'preparing') {
      caseVal.innerHTML = '<span class="spinner" style="width:12px;height:12px;margin-right:6px"></span> Preparing case…';
    } else if (s === 'ready') {
      caseVal.innerHTML = '<span class="badge badge-success">✓ Ready</span>';
    } else if (s === 'failed') {
      caseVal.innerHTML = '<span class="badge badge-danger">✗ Failed</span>';
    } else if (s === 'confirmed') {
      caseVal.innerHTML = '<span class="badge badge-info">✓ Confirmed</span>';
    }

    caseRow.appendChild(caseLabel);
    caseRow.appendChild(caseVal);
    card.appendChild(caseRow);
  }

  // Actions
  const actions = el('div', 'queue-actions');

  if (appeal.case?.status === 'ready') {
    const startBtn = el('button', 'btn btn-primary');
    startBtn.innerHTML = '▶ Start Court';
    startBtn.onclick = () => window.open(`court.html?case=${appeal.case.id}`);
    actions.appendChild(startBtn);
  }

  if (appeal.case?.status === 'failed') {
    const regenBtn = el('button', 'btn btn-ghost btn-sm');
    regenBtn.innerHTML = '🔄 Regenerate';
    regenBtn.onclick = async () => {
      regenBtn.disabled = true;
      regenBtn.textContent = 'Regenerating…';
      try {
        await api(`/api/court/${appeal.id}/prepare`, { method: 'POST' });
        await refresh();
      } catch { regenBtn.disabled = false; regenBtn.innerHTML = '🔄 Regenerate'; }
    };
    actions.appendChild(regenBtn);
  }

  const resolveBtn = el('button', 'btn btn-ghost btn-sm');
  resolveBtn.innerHTML = '⚡ Resolve without court';
  resolveBtn.onclick = () => openDecisionModal(appeal);
  actions.appendChild(resolveBtn);

  card.appendChild(actions);
  return card;
}

// ─── Support Queue ────────────────────────────────────
async function loadSupportQueue() {
  try {
    const appeals = await api('/api/appeals?status=support_queue');
    renderSupportQueue(appeals);
    updateBadge('badge-support', appeals.length);
  } catch {}
}

function renderSupportQueue(appeals) {
  const container = document.getElementById('support-queue-list');
  if (!appeals || appeals.length === 0) {
    container.innerHTML = `<div class="empty-state"><div class="empty-icon">👥</div><p>No cases in support queue</p></div>`;
    return;
  }
  container.innerHTML = '';
  appeals.forEach(appeal => {
    const card = buildSupportCard(appeal);
    container.appendChild(card);
  });
}

function buildSupportCard(appeal) {
  const card = el('div', 'queue-card');

  // Header
  const header = el('div', 'queue-card-header');
  const userDiv = el('div', 'queue-user');
  const avatar  = el('div', 'queue-avatar');
  avatar.style.background = 'linear-gradient(135deg, var(--info), var(--success))';
  avatar.textContent = appeal.username?.[0]?.toUpperCase() || '?';
  const userInfo = el('div', 'queue-user-info');
  const username = el('div', 'queue-username');
  username.textContent = '@' + appeal.username;
  const meta = el('div', 'queue-meta');
  meta.textContent = `Attempt #${appeal.attempt_no} · ${formatDate(appeal.created_at)}`;
  userInfo.appendChild(username);
  userInfo.appendChild(meta);
  userDiv.appendChild(avatar);
  userDiv.appendChild(userInfo);
  header.appendChild(userDiv);

  const ruleBadge = el('span', 'badge badge-warning');
  ruleBadge.textContent = appeal.incident?.rule || '';
  header.appendChild(ruleBadge);
  card.appendChild(header);

  // Incident summary
  if (appeal.incident) {
    const incRow = el('div', 'triage-row');
    const incLabel = el('span', 'triage-label'); incLabel.textContent = 'Incident';
    const incVal   = el('span', 'text-secondary'); incVal.textContent = appeal.incident.reason || '';
    incRow.appendChild(incLabel);
    incRow.appendChild(incVal);
    card.appendChild(incRow);
  }

  // AI triage reason
  if (appeal.triage?.reason) {
    const r = el('div', 'triage-row');
    const l = el('span', 'triage-label'); l.textContent = 'AI triage';
    const v = el('span', 'text-muted'); v.textContent = appeal.triage.reason;
    r.appendChild(l); r.appendChild(v);
    card.appendChild(r);
  }

  // Appeal text
  const textBlock = el('div', 'appeal-text-block');
  textBlock.textContent = appeal.text || '';
  card.appendChild(textBlock);

  // Actions
  const actions = el('div', 'queue-actions');

  const upholdBtn = el('button', 'btn btn-danger btn-sm');
  upholdBtn.innerHTML = '⛔ Uphold';
  upholdBtn.onclick = async () => {
    upholdBtn.disabled = true;
    try {
      await api(`/api/appeals/${appeal.id}/decision`, { method: 'POST', body: { decision: 'uphold', note: '' } });
      refresh();
    } catch { upholdBtn.disabled = false; }
  };

  const overturnBtn = el('button', 'btn btn-success btn-sm');
  overturnBtn.innerHTML = '✅ Overturn (Unban)';
  overturnBtn.onclick = () => openDecisionModal(appeal);

  actions.appendChild(upholdBtn);
  actions.appendChild(overturnBtn);
  card.appendChild(actions);
  return card;
}

// ─── Incidents ────────────────────────────────────────
async function loadIncidents() {
  try {
    const incidents = await api('/api/incidents?channel=main');
    renderIncidents(incidents);
    const active = incidents.filter(i => i.active).length;
    updateBadge('badge-incidents', active);
  } catch {}
}

function renderIncidents(incidents) {
  const tbody = document.getElementById('incidents-tbody');
  if (!incidents || incidents.length === 0) {
    tbody.innerHTML = `<tr><td colspan="7" class="table-empty">No incidents yet</td></tr>`;
    return;
  }
  tbody.innerHTML = '';
  incidents.forEach(inc => {
    const tr = document.createElement('tr');

    const username = el('td');
    const userSpan = el('span'); userSpan.style.fontWeight = '600';
    userSpan.textContent = '@' + inc.username;
    username.appendChild(userSpan);

    const action = el('td');
    const actionBadge = el('span', `badge badge-${inc.action === 'ban' ? 'danger' : 'warning'}`);
    actionBadge.textContent = inc.action.toUpperCase();
    if (inc.action === 'timeout' && inc.duration_min) {
      actionBadge.textContent += ` ${inc.duration_min}m`;
    }
    action.appendChild(actionBadge);

    const rule = el('td');
    const ruleBadge = el('span', 'badge badge-muted');
    ruleBadge.textContent = inc.rule;
    rule.appendChild(ruleBadge);

    const reason = el('td', 'text-secondary');
    reason.style.maxWidth = '200px';
    reason.style.fontSize = '12px';
    reason.textContent = inc.reason;

    const conf = el('td');
    const pct = Math.round((inc.confidence || 0) * 100);
    const pill = el('div', 'confidence-pill');
    const bar = el('div', 'conf-bar-mini');
    const fill = el('div', 'conf-bar-mini-fill');
    fill.style.width = pct + '%';
    bar.appendChild(fill);
    const label = el('span'); label.textContent = pct + '%';
    pill.appendChild(bar);
    pill.appendChild(label);
    conf.appendChild(pill);

    const date = el('td', 'text-muted');
    date.style.fontSize = '12px';
    date.textContent = formatDate(inc.created_at);

    const status = el('td');
    if (inc.active) {
      status.innerHTML = '<span class="badge badge-danger">Active</span>';
    } else if (inc.revoked_at) {
      status.innerHTML = '<span class="badge badge-success">Revoked</span>';
    } else {
      status.innerHTML = '<span class="badge badge-muted">Expired</span>';
    }

    tr.append(username, action, rule, reason, conf, date, status);
    tbody.appendChild(tr);
  });
}

// ─── Statistics ───────────────────────────────────────
async function loadStats() {
  try {
    const s = await api('/api/stats');
    renderStats(s);
  } catch {}
}

function renderStats(s) {
  setText(document.getElementById('stat-messages'),   s.messages_total   ?? '—');
  setText(document.getElementById('stat-flagged'),    s.prefilter_flagged ?? '—');
  setText(document.getElementById('stat-acquitted'),  s.deep_acquitted    ?? '—');
  setText(document.getElementById('stat-timeouts'),   s.timeouts          ?? '—');
  setText(document.getElementById('stat-bans'),       s.bans              ?? '—');
  setText(document.getElementById('stat-precedents'), s.precedents_total  ?? '—');

  // Overturn rate
  if (s.overturn_rate !== undefined) {
    const pct = Math.round(s.overturn_rate * 100);
    setText(document.getElementById('metric-overturn'), pct + '%');
    document.getElementById('metric-overturn-bar').style.width = pct + '%';
  }
  if (s.avg_resolution_min !== undefined) {
    setText(document.getElementById('metric-resolution'), s.avg_resolution_min.toFixed(1) + ' min');
  }

  // Appeals breakdown
  const breakdown = document.getElementById('appeals-breakdown');
  breakdown.innerHTML = '';
  if (s.appeals) {
    const total = Object.values(s.appeals).reduce((a, b) => a + b, 0) || 1;
    const labels = {
      rejected_auto:       '🤖 Auto-rejected',
      support_queue:       '👥 Support queue',
      court_queue:         '⚖️ Court queue',
      resolved_upheld:     '⛔ Resolved: upheld',
      resolved_overturned: '✅ Resolved: overturned',
    };
    const colors = {
      rejected_auto:       'var(--text-muted)',
      support_queue:       'var(--info)',
      court_queue:         'var(--accent)',
      resolved_upheld:     'var(--danger)',
      resolved_overturned: 'var(--success)',
    };
    Object.entries(s.appeals).forEach(([key, val]) => {
      const row = el('div', 'breakdown-item');
      const label = el('span', 'breakdown-label'); label.textContent = labels[key] || key;
      const barWrap = el('div', 'breakdown-bar-wrap');
      const bar = el('div', 'breakdown-bar');
      bar.style.width = (val / total * 100) + '%';
      bar.style.background = colors[key] || 'var(--accent)';
      barWrap.appendChild(bar);
      const count = el('span', 'breakdown-count'); count.textContent = val;
      row.appendChild(label);
      row.appendChild(barWrap);
      row.appendChild(count);
      breakdown.appendChild(row);
    });
  }
}

// ─── Helpers ──────────────────────────────────────────
function updateBadge(id, count) {
  const b = document.getElementById(id);
  if (!b) return;
  setText(b, count);
  b.classList.toggle('visible', count > 0);
}

// ─── Polling / Refresh ────────────────────────────────
async function refresh() {
  await Promise.allSettled([
    loadSettings(),
    loadCourtQueue(),
    loadSupportQueue(),
    loadIncidents(),
    loadStats(),
  ]);
}

// Initial load & set up polling
refresh();
setInterval(refresh, 3000);
