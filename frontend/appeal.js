// appeal.js — owner: C
import { api, MOCK, formatDate, setText, el } from './common.js';

// ─── State ─────────────────────────────────────────
let currentStep = 1;
let currentUsername = null;
let activeIncident = null;
let currentAppealId = null;
let pollTimer = null;

// ─── On page load: check URL params ────────────────
const params = new URLSearchParams(location.search);
const urlIncidentId = params.get('incident');
const urlUsername = params.get('username');

if (urlUsername) {
  // Pre-fill from URL (came from chat ban banner)
  const twitchField = document.getElementById('twitch-username');
  if (twitchField) twitchField.value = urlUsername;
}

// ─── Auth Tab Switch ────────────────────────────────
window.switchAuthTab = function (tab) {
  ['twitch', 'email'].forEach(t => {
    document.getElementById(`tab-${t}`).classList.toggle('active', t === tab);
    document.getElementById(`panel-${t}`).classList.toggle('hidden', t !== tab);
  });
};

// ─── Step Management ────────────────────────────────
function showStep(n) {
  currentStep = n;
  // Update step indicators
  for (let i = 1; i <= 4; i++) {
    const ind = document.getElementById(`step-indicator-${i}`);
    if (!ind) continue;
    ind.classList.remove('active', 'done');
    if (i < n) ind.classList.add('done');
    if (i === n) ind.classList.add('active');
  }
  // Show/hide sections
  const sections = ['step-login', 'step-ban-status', 'step-appeal-form', 'step-status'];
  sections.forEach((id, idx) => {
    const sec = document.getElementById(id);
    if (!sec) return;
    const isVisible = (idx + 1) === n;
    sec.classList.toggle('hidden', !isVisible);
    if (isVisible) sec.scrollIntoView({ behavior: 'smooth', block: 'start' });
  });
}

// ─── Connect Twitch ─────────────────────────────────
window.connectTwitch = async function () {
  const input = document.getElementById('twitch-username');
  const username = input.value.trim();
  if (!username) {
    showLoginError('Please enter your Twitch username.');
    return;
  }
  setLoginLoading(true);
  try {
    await loadBanStatus(username);
  } catch (err) {
    showLoginError(err.message || 'Failed to load account status.');
  } finally {
    setLoginLoading(false);
  }
};

window.connectEmail = async function () {
  const username = document.getElementById('email-username').value.trim();
  const email = document.getElementById('email-addr').value.trim();
  if (!username) {
    showLoginError('Please enter your Twitch username.');
    return;
  }
  if (!email || !email.includes('@')) {
    showLoginError('Please enter a valid email address.');
    return;
  }
  setLoginLoading(true);
  try {
    await loadBanStatus(username);
  } catch (err) {
    showLoginError(err.message || 'Failed to load account status.');
  } finally {
    setLoginLoading(false);
  }
};

function setLoginLoading(loading) {
  const btnT = document.getElementById('btn-twitch-connect');
  const btnE = document.getElementById('btn-email-connect');
  [btnT, btnE].forEach(b => { if (b) b.disabled = loading; });
  if (btnT) btnT.innerHTML = loading
    ? '<span class="spinner"></span> Verifying…'
    : '<span>🎮</span> Verify Twitch Account';
  if (btnE) btnE.innerHTML = loading
    ? '<span class="spinner"></span> Verifying…'
    : '<span>✉️</span> Verify with Email';
  hideLoginError();
}

function showLoginError(msg) {
  setText(document.getElementById('login-error-text'), msg);
  document.getElementById('login-error').classList.remove('hidden');
}
function hideLoginError() {
  document.getElementById('login-error').classList.add('hidden');
}

// ─── Load Ban Status ────────────────────────────────
async function loadBanStatus(username) {
  currentUsername = username;

  // In mock mode, simulate a ban on specific username
  let incidents = [];
  if (MOCK) {
    incidents = await api('/api/incidents?channel=main');
  } else {
    incidents = await api(`/api/incidents?channel=main&username=${encodeURIComponent(username)}`);
  }

  // Filter active incidents for this user
  let userIncidents = incidents.filter(i => i.username === username);

  // Show user badge
  const badgeArea = document.getElementById('user-badge-area');
  badgeArea.innerHTML = '';
  const badge = el('div', 'badge badge-info');
  badge.textContent = `@${username}`;
  badgeArea.appendChild(badge);

  // If came from URL with incident id, use that
  if (urlIncidentId) {
    try {
      const inc = await api(`/api/incidents/${urlIncidentId}`);
      activeIncident = inc;
      userIncidents = [inc, ...userIncidents.filter(i => i.id !== inc.id)];
    } catch { }
  }

  const active = userIncidents.find(i => i.active);
  activeIncident = active || null;

  // Populate ban status UI
  if (active) {
    populateActiveBan(active);
    populateBanHistory(userIncidents.filter(i => !i.active), 'ban-history-list-2');
    document.getElementById('status-active-ban').classList.remove('hidden');
    document.getElementById('status-no-ban').classList.add('hidden');
  } else {
    populateBanHistory(userIncidents, 'ban-history-list');
    document.getElementById('status-no-ban').classList.remove('hidden');
    document.getElementById('status-active-ban').classList.add('hidden');
  }

  showStep(2);
}

function populateActiveBan(inc) {
  setText(document.getElementById('ban-type-label'),
    inc.action === 'ban' ? 'You are banned' : `You have an active timeout (${inc.duration_min} min)`);
  setText(document.getElementById('ban-reason-text'), inc.reason);
  setText(document.getElementById('inc-action'), inc.action.toUpperCase());
  setText(document.getElementById('inc-rule'), inc.rule);
  setText(document.getElementById('inc-reason'), inc.reason);
  setText(document.getElementById('inc-date'), formatDate(inc.created_at));

  const expiresRow = document.getElementById('inc-expires-row');
  if (inc.expires_at) {
    setText(document.getElementById('inc-expires'), formatDate(inc.expires_at));
    expiresRow.classList.remove('hidden');
  } else {
    expiresRow.classList.add('hidden');
  }

  const pct = Math.round((inc.confidence || 0) * 100);
  document.getElementById('confidence-bar').style.width = pct + '%';
  setText(document.getElementById('inc-confidence'), pct + '%');
}

function populateBanHistory(incidents, listId) {
  const list = document.getElementById(listId);
  if (!list) return;
  list.innerHTML = '';

  if (!incidents || incidents.length === 0) {
    const empty = el('div', 'text-muted', 'No previous ban history.');
    empty.style.fontSize = '13px';
    empty.style.padding = '8px 0';
    list.appendChild(empty);
    return;
  }

  incidents.forEach(inc => {
    const item = el('div', 'history-item slide-in');
    const actionBadge = el('span', `badge badge-${inc.action === 'ban' ? 'danger' : 'warning'}`, inc.action.toUpperCase());
    const details = el('div', 'history-item-details');
    const reason = el('div', 'history-item-reason');
    reason.textContent = inc.reason;
    const date = el('div', 'history-item-date');
    date.textContent = formatDate(inc.created_at);
    details.appendChild(reason);
    details.appendChild(date);
    item.appendChild(actionBadge);
    item.appendChild(details);
    list.appendChild(item);
  });
}

// ─── Toggle history panel ────────────────────────────
window.toggleHistory = function () {
  const hist = document.getElementById('ban-history-list-2');
  const btn = document.getElementById('btn-show-history');
  hist.classList.toggle('hidden');
  btn.textContent = hist.classList.contains('hidden') ? '📋 View ban history' : '📋 Hide ban history';
};

// ─── Go to appeal form ────────────────────────────────
window.goToAppealForm = function () {
  if (!activeIncident) return;

  // Check if there are remaining appeal attempts
  const attemptNo = 1; // would come from API in real mode
  setText(document.getElementById('attempt-badge'), `Attempt #${attemptNo}`);

  showStep(3);
};

window.goBack = function () {
  if (currentStep > 1) showStep(currentStep - 1);
};

// ─── Character counter ────────────────────────────────
window.updateCharCount = function (textarea) {
  const n = textarea.value.length;
  const counter = textarea.closest('.form-group').querySelector('.char-counter');
  setText(counter.querySelector('#char-count'), n);
  counter.classList.remove('warn', 'danger');
  if (n > 1900) counter.classList.add('danger');
  else if (n > 1600) counter.classList.add('warn');

  const hint = document.getElementById('char-min-hint');
  if (hint) hint.classList.toggle('hidden', n >= 20);
};

// ─── Submit Appeal ────────────────────────────────────
window.submitAppeal = async function (e) {
  e.preventDefault();
  const text = document.getElementById('appeal-text').value.trim();
  const contact = document.getElementById('appeal-contact').value.trim();
  const btn = document.getElementById('btn-submit-appeal');

  if (text.length < 20) {
    showAppealError('Appeal text must be at least 20 characters.');
    return;
  }
  if (!activeIncident) {
    showAppealError('No active incident found. Please go back and re-verify your account.');
    return;
  }

  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> Submitting…';

  try {
    const result = await api('/api/appeals', {
      method: 'POST',
      body: {
        username: currentUsername,
        incident_id: activeIncident.id,
        text,
        contact: contact || undefined,
      },
    });

    currentAppealId = result.id;
    showStep(4);
    setText(document.getElementById('tl-submitted-time'), formatDate(new Date().toISOString()));
    setTrackerState('new');
    startPolling(result.id);

  } catch (err) {
    if (err.status === 409) {
      showAppealError('You already have an open appeal for this incident. Please wait for it to be resolved.');
    } else if (err.status === 429) {
      showAppealError('Maximum number of appeals (3) for this incident has been reached.');
    } else if (err.status === 400) {
      showAppealError(err.data?.detail || 'This incident is not eligible for an appeal.');
    } else {
      showAppealError('Failed to submit appeal. Please try again.');
    }
    btn.disabled = false;
    btn.innerHTML = '<span>🚀</span> Submit Appeal';
  }
};

function showAppealError(msg) {
  setText(document.getElementById('appeal-submit-error-text'), msg);
  document.getElementById('appeal-submit-error').classList.remove('hidden');
}

// ─── Status polling ───────────────────────────────────
function startPolling(appealId) {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(() => pollStatus(appealId), 2000);
}

async function pollStatus(appealId) {
  try {
    const data = await api(`/api/appeals/${appealId}`);
    setTrackerState(data.status, data);
    if (['rejected_auto', 'resolved_upheld', 'resolved_overturned'].includes(data.status)) {
      clearInterval(pollTimer);
    }
  } catch { }
}

const STATUS_MAP = {
  new: {
    icon: '🤖', title: 'AI is reviewing your appeal…',
    desc: 'This usually takes a few seconds. The page updates automatically.',
    tl: [false, true, false],
  },
  rejected_auto: {
    icon: '❌', title: 'Appeal was automatically rejected',
    desc: '',
    tl: [false, false, true],
    tlClass: 'done',
  },
  support_queue: {
    icon: '👥', title: 'Forwarded to moderators',
    desc: 'A human moderator will review your case shortly.',
    tl: [false, false, true],
    tlClass: 'done',
  },
  court_queue: {
    icon: '🎉', title: 'Your case was selected for AI Court!',
    desc: 'Your appeal will be heard live on stream. Get ready for your day in court!',
    tl: [false, false, true],
    tlClass: 'done',
  },
  resolved_overturned: {
    icon: '✅', title: 'Ban lifted — You have been unbanned!',
    desc: 'Your appeal was successful. Welcome back to chat!',
    tl: [false, false, true],
    tlClass: 'done',
  },
  resolved_upheld: {
    icon: '⛔', title: 'Punishment upheld',
    desc: 'After review, the moderators decided to keep the ban in place.',
    tl: [false, false, true],
    tlClass: 'done',
  },
};

function setTrackerState(status, data) {
  const cfg = STATUS_MAP[status] || STATUS_MAP['new'];

  const anim = document.getElementById('tracker-anim');
  const icon = document.getElementById('tracker-icon');
  const title = document.getElementById('tracker-title');
  const desc = document.getElementById('tracker-desc');
  const tlReview = document.getElementById('tl-review');
  const tlResult = document.getElementById('tl-result');
  const tlResultLabel = document.getElementById('tl-result-label');
  const tlResultDesc = document.getElementById('tl-result-desc');

  setText(icon, cfg.icon);
  setText(title, cfg.title);
  setText(desc, status === 'rejected_auto' && data?.public_reason ? data.public_reason : cfg.desc);

  icon.classList.toggle('spin-anim', status === 'new');

  // attempt badge
  if (data?.attempt_no) {
    setText(document.getElementById('attempt-no-badge'), `Attempt #${data.attempt_no}`);
  }

  // Timeline
  tlReview.classList.remove('active', 'done');
  tlResult.classList.remove('active', 'done');

  if (status === 'new') {
    tlReview.classList.add('active');
  } else {
    tlReview.classList.add('done');
    tlResult.classList.add(cfg.tlClass || 'done');

    const labels = {
      rejected_auto: 'Automatically rejected',
      support_queue: 'Forwarded to support',
      court_queue: '🎉 Selected for AI Court!',
      resolved_overturned: '✅ Ban lifted',
      resolved_upheld: '⛔ Punishment upheld',
    };
    setText(tlResultLabel, labels[status] || 'Decision');
  }

  // Color the animation area
  anim.className = 'tracker-animation';
  if (status === 'resolved_overturned') anim.classList.add('resolved-overturn');
  if (status === 'resolved_upheld') anim.classList.add('resolved-uphold');
}

// ─── Init: if came from URL with incident ────────────
if (urlIncidentId && urlUsername) {
  // Auto-proceed to ban status
  (async () => {
    try {
      await loadBanStatus(urlUsername);
    } catch { }
  })();
}
