//
// Version: 1.1
// Updated: 2026-10-11
//
// v1.0 - 2026-10-11 - Split out of index.html: Stream selection, theme, layout & paging, live detection, alerts
// v1.1 - 2026-10-11 - Mobile: pager stays hidden; arrows reach every camera in a group
//
// Loaded as a classic script (shared global scope) in the order listed in
// index.html. Don't convert to an ES module without resolving globals.
//

// ── Stream state ─────────────────────────────────────────────────────────────

let activeStream = 'main';

// Streams go through PTZLite's authenticated /go2rtc/ws proxy (same origin as
// the page), never straight to go2rtc — which is bound to localhost only.
function _wsBase() { return `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}`; }

function streamSrc(camId) {
  return `${_wsBase()}/go2rtc/ws?src=${camId}_${activeStream}`;
}

function setStream(stream) {
  if (stream === activeStream) return;
  activeStream = stream;
  document.querySelectorAll('#stream-btns button').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.stream === stream);
  });
  renderGrid();
}

// ── Theme ─────────────────────────────────────────────────────────────────────
const MOON_ICON = `<circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/>`;
const SUN_ICON  = `<path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>`;

function applyTheme(light) {
  document.body.classList.toggle('light', light);
  document.getElementById('theme-icon').innerHTML = light ? SUN_ICON : MOON_ICON;
  document.getElementById('theme-btn').title = light ? 'Switch to dark mode' : 'Switch to light mode';
}

function toggleTheme() {
  const light = !document.body.classList.contains('light');
  localStorage.setItem('theme', light ? 'light' : 'dark');
  applyTheme(light);
}

document.getElementById('stream-btns').addEventListener('click', e => {
  const btn = e.target.closest('button[data-stream]');
  if (btn) setStream(btn.dataset.stream);
});

// ── Layout & pagination ───────────────────────────────────────────────────────

const LAYOUTS = [
  { label: '1×1', cols: 1, rows: 1 },
  { label: '2×1', cols: 2, rows: 1 },
  { label: '2×2', cols: 2, rows: 2 },
  { label: '3×2', cols: 3, rows: 2 },
  { label: '3×3', cols: 3, rows: 3 },
  { label: '4×4', cols: 4, rows: 4 },
];

let _layout        = LAYOUTS[2];
let _page          = 0;
let _cameras       = [];
let _pageGroups    = [];
let _groupIdx      = 0;
let _useNamedPages = false;
let _labelsVisible = true;
let _keepLive = false;

function perPage() { return _layout.cols * _layout.rows; }

function computePageGroups(cams) {
  const map = new Map();
  cams.forEach(cam => {
    const key = cam.page || '';
    if (!map.has(key)) map.set(key, { name: cam.page || null, cameras: [] });
    map.get(key).cameras.push(cam);
  });
  return [...map.values()];
}

function currentCamList() {
  if (_useNamedPages && _pageGroups.length > 0) return _pageGroups[_groupIdx].cameras;
  return _cameras;
}

function pageCount() { return Math.max(1, Math.ceil(currentCamList().length / perPage())); }

function renderGrid() {
  const grid = document.getElementById('grid');
  grid.innerHTML = '';
  grid.style.gridTemplateColumns = `repeat(${_layout.cols}, 1fr)`;
  grid.style.gridTemplateRows    = `repeat(${_layout.rows}, 1fr)`;

  const cams  = currentCamList();
  const start = _page * perPage();
  const slice = cams.slice(start, start + perPage());
  slice.forEach((cam, i) => grid.appendChild(buildCard(cam, i * 500)));

  for (let i = slice.length; i < perPage(); i++) {
    const empty = document.createElement('div');
    empty.className = 'cam-wrap';
    grid.appendChild(empty);
  }

  const navEl = document.getElementById('nav-btns');
  const pc = pageCount();

  if (_useNamedPages) {
    navEl.style.display = '';
    const opts = _pageGroups.map((g, i) =>
      `<option value="${i}"${i === _groupIdx ? ' selected' : ''}>${g.name || 'All'}</option>`
    ).join('');
    const sub = pc > 1
      ? `<button id="prev-btn" onclick="changePage(-1)" ${_page === 0 ? 'disabled' : ''}>&#8249;</button>` +
        `<span id="page-info" style="font-size:0.72rem;color:var(--text-muted);min-width:36px;text-align:center">${_page + 1}/${pc}</span>` +
        `<button id="next-btn" onclick="changePage(1)" ${_page >= pc - 1 ? 'disabled' : ''}>&#8250;</button>`
      : '';
    navEl.innerHTML = `<select onchange="selectGroup(parseInt(this.value))">${opts}</select>` + sub;
  } else {
    navEl.style.display = pc > 1 ? '' : 'none';
    navEl.innerHTML =
      `<button id="prev-btn" onclick="changePage(-1)" ${_page === 0 ? 'disabled' : ''}>&#8249;</button>` +
      `<span id="page-info" style="font-size:0.72rem;color:var(--text-muted);min-width:36px;text-align:center">${_page + 1} / ${pc}</span>` +
      `<button id="next-btn" onclick="changePage(1)" ${_page >= pc - 1 ? 'disabled' : ''}>&#8250;</button>`;
  }

  // Phones use the bottom bar instead; renderGrid must not re-show the desktop pager.
  if (window._isMobile) navEl.style.setProperty('display', 'none', 'important');

  _syncMobileNav();
  _startDetectionPolling();
}

// ── Live detection ────────────────────────────────────────────────────────────
// The server runs one batched poller and pushes the full state map over SSE
// whenever it changes. We drop the connection while the tab is hidden, which
// lets the server stop polling the NVR when nobody is watching.
let _detectSource = null;
let _detectStates = {};
const _DETECT_LABELS = { people: 'Person', vehicle: 'Vehicle', dog_cat: 'Animal', face: 'Face', package: 'Package' };

function _applyDetection() {
  document.querySelectorAll('.cam-wrap[data-cam-id]').forEach(wrap => {
    const s = _detectStates[wrap.dataset.camId];
    const badge = wrap.querySelector('.detect-badge');
    const ai = s && s.alerts && s.alerts.length;
    if (!s || (!ai && !s.motion)) {
      wrap.classList.remove('alerting', 'ai');
      return;
    }
    wrap.classList.add('alerting');
    wrap.classList.toggle('ai', !!ai);
    if (badge) {
      badge.classList.toggle('motion', !ai);
      badge.textContent = ai ? s.alerts.map(t => _DETECT_LABELS[t] || t).join(' · ') : 'Motion';
    }
  });
}

function _connectDetection() {
  if (_detectSource || document.hidden) return;
  _detectSource = new EventSource('/detect/stream');
  _detectSource.onmessage = e => {
    const prev = _detectStates;
    try { _detectStates = JSON.parse(e.data) || {}; } catch { return; }
    _applyDetection();
    _onDetectionChange(prev, _detectStates);
  };
  // EventSource reconnects by itself after network blips.
}

// ── Alerts: chime + header chip ───────────────────────────────────────────────
let _chimeOn = localStorage.getItem('chime') === '1';
let _audioCtx = null;
const _aiAlerting = s => !!(s && s.alerts && s.alerts.length);

function toggleChime() {
  _chimeOn = !_chimeOn;
  localStorage.setItem('chime', _chimeOn ? '1' : '0');
  _syncChimeBtn();
  if (_chimeOn) _playChime();   // the click unlocks audio and previews the sound
}

function _syncChimeBtn() {
  const b = document.getElementById('chime-btn');
  if (!b) return;
  b.classList.toggle('active', _chimeOn);
  b.title = _chimeOn ? 'Detection chime on (click to mute)'
                     : 'Play a chime when a camera sees a person or vehicle';
}

function _playChime() {
  try {
    _audioCtx = _audioCtx || new (window.AudioContext || window.webkitAudioContext)();
    const t0 = _audioCtx.currentTime;
    [880, 1320].forEach((f, i) => {            // short two-tone "ding-dong"
      const o = _audioCtx.createOscillator(), g = _audioCtx.createGain();
      o.frequency.value = f; o.type = 'sine';
      g.gain.setValueAtTime(0.0001, t0 + i * 0.18);
      g.gain.exponentialRampToValueAtTime(0.25, t0 + i * 0.18 + 0.02);
      g.gain.exponentialRampToValueAtTime(0.0001, t0 + i * 0.18 + 0.35);
      o.connect(g).connect(_audioCtx.destination);
      o.start(t0 + i * 0.18); o.stop(t0 + i * 0.18 + 0.4);
    });
  } catch { /* audio unavailable */ }
}

function _camName(id) { return (_cameras.find(c => c.id === id) || {}).name || id; }

function _onDetectionChange(prev, next) {
  const alerting = Object.keys(next).filter(id => _aiAlerting(next[id]));
  const fresh = alerting.filter(id => !_aiAlerting(prev[id]));
  if (fresh.length && _chimeOn) _playChime();
  const chip = document.getElementById('alert-chip');
  if (!chip) return;
  chip.classList.toggle('show', alerting.length > 0);
  chip.textContent = alerting.length ? '● ' + alerting.map(_camName).join(', ') : '';
  chip.dataset.cam = alerting[0] || '';
}

// Click the chip: go to the page holding the first alerting camera and fullscreen it.
function _jumpToAlert() {
  const camId = document.getElementById('alert-chip').dataset.cam;
  if (!camId) return;
  if (_useNamedPages) {
    const gi = _pageGroups.findIndex(g => g.cameras.some(c => c.id === camId));
    if (gi >= 0) _groupIdx = gi;
  }
  const idx = currentCamList().findIndex(c => c.id === camId);
  if (idx >= 0) _page = Math.floor(idx / perPage());
  renderGrid();
  const wrap = document.querySelector(`.cam-wrap[data-cam-id="${CSS.escape(camId)}"]`);
  if (wrap) _enterCamFullscreen(wrap);
}

function _disconnectDetection() {
  if (_detectSource) { _detectSource.close(); _detectSource = null; }
}

function _startDetectionPolling() {   // called after every grid render
  _connectDetection();
  _applyDetection();                  // re-badge freshly built cards immediately
}

document.addEventListener('visibilitychange', () => {
  if (document.hidden) _disconnectDetection(); else _connectDetection();
});

function setLayout(layout) {
  _layout = layout;
  _page   = 0;
  renderGrid();
  document.querySelectorAll('#layout-btns button').forEach(b => {
    b.classList.toggle('active', b.dataset.layout === layout.label);
  });
}

function changePage(delta) {
  _page = Math.max(0, Math.min(pageCount() - 1, _page + delta));
  renderGrid();
}

function selectGroup(idx) {
  _groupIdx = idx;
  _page = 0;
  renderGrid();
}

// Bottom-bar arrows: step through the pages of the current group first, then move
// to the next/previous group (landing on its first/last page).
function mobileNav(dir) {
  const next = _page + dir;
  if (next >= 0 && next < pageCount()) { changePage(dir); return; }
  if (!_useNamedPages) return;
  const ni = _groupIdx + dir;
  if (ni < 0 || ni >= _pageGroups.length) return;
  _groupIdx = ni;
  _page = dir > 0 ? 0 : pageCount() - 1;
  renderGrid();
}

function _syncMobileNav() {
  const prev = document.getElementById('mob-prev');
  const next = document.getElementById('mob-next');
  const info = document.getElementById('mob-page-info');
  if (!prev) return;
  if (_useNamedPages) {
    const pc = pageCount();
    prev.disabled = _groupIdx <= 0 && _page <= 0;
    next.disabled = _groupIdx >= _pageGroups.length - 1 && _page >= pc - 1;
    const name = _pageGroups[_groupIdx]?.name || 'All';
    info.textContent = pc > 1 ? `${name} · ${_page + 1}/${pc}` : name;
  } else {
    const pc = pageCount();
    prev.disabled = _page <= 0;
    next.disabled = _page >= pc - 1;
    info.textContent = pc > 1 ? `${_page + 1} / ${pc}` : '';
  }
}

function defaultLayout(n) {
  if (n <= 1) return LAYOUTS[0];
  if (n <= 2) return LAYOUTS[1];
  if (n <= 4) return LAYOUTS[2];
  if (n <= 6) return LAYOUTS[3];
  if (n <= 9) return LAYOUTS[4];
  return LAYOUTS[5];
}

