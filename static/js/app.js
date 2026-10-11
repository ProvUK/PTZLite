//
// Version: 1.2
// Updated: 2026-10-11
//
// v1.0 - 2026-10-11 - Split out of index.html: Server-offline overlay, go2rtc health, init, mobile UI
// v1.1 - 2026-10-11 - ?ui=desktop forces the desktop layout (tablets, previews)
// v1.2 - 2026-10-11 - Mobile: chime button moves into the menu
//
// Loaded as a classic script (shared global scope) in the order listed in
// index.html. Don't convert to an ES module without resolving globals.
//

// ── Server-offline detection ──────────────────────────────────────────────────

let _serverOfflineCount = 0;    // consecutive network-error failures
let _serverOfflineTimer = null; // auto-retry interval while overlay is shown

function _showServerOffline() {
  document.getElementById('server-offline').classList.add('visible');
  document.getElementById('server-offline-status').textContent = 'Checking every 5 seconds…';
  if (!_serverOfflineTimer) {
    _serverOfflineTimer = setInterval(retryServerConnection, 5000);
  }
}

function _hideServerOffline() {
  document.getElementById('server-offline').classList.remove('visible');
  if (_serverOfflineTimer) { clearInterval(_serverOfflineTimer); _serverOfflineTimer = null; }
  _serverOfflineCount = 0;
}

async function retryServerConnection() {
  const statusEl = document.getElementById('server-offline-status');
  if (statusEl) statusEl.textContent = 'Checking…';
  try {
    await fetch('/api/auth/status', { cache: 'no-store' });
    // Server responded — reload to get a clean fresh state
    location.reload();
  } catch {
    if (statusEl) statusEl.textContent = 'Still offline — retrying…';
  }
}

// Immediate check when user returns to the tab while overlay is showing
document.addEventListener('visibilitychange', () => {
  if (!document.hidden && document.getElementById('server-offline').classList.contains('visible')) {
    retryServerConnection();
  }
});

// ── go2rtc health & stream monitoring ────────────────────────────────────────

async function checkGo2rtcHealth() {
  const dot = document.getElementById('rtc-status');
  try {
    const h = await fetch('/api/go2rtc/health').then(r => r.json());
    // Server responded — clear any offline state
    _serverOfflineCount = 0;
    _hideServerOffline();
    if (h.alive && h.api_ok) {
      dot.className = 'ok';
      dot.title = `Stream engine running — ${h.streams.length} stream(s) registered`;
    } else {
      dot.className = 'err';
      const reason = !h.alive ? 'process not running' : 'API not responding';
      dot.title = `Stream engine offline (${reason}) — click to restart`;
      // Auto-restart with 60s cooldown shared across all tabs via localStorage,
      // preventing a restart storm when multiple tabs are open simultaneously.
      const now = Date.now();
      const lastRestart = parseInt(localStorage.getItem('go2rtcLastRestart') || '0');
      if (now - lastRestart > 60000) {
        localStorage.setItem('go2rtcLastRestart', String(now));
        console.warn('[go2rtc] dead/unresponsive — auto-restarting');
        await fetch('/api/go2rtc/restart', { method: 'POST' }).catch(() => {});
        await new Promise(r => setTimeout(r, 2500));
        renderGrid();
        await checkGo2rtcHealth();
        return h;
      }
    }
    if (h.recent_errors && h.recent_errors.length) {
      console.warn('[go2rtc errors]', h.recent_errors.join('\n'));
    }
    if (h.log_tail && h.log_tail.length) {
      console.debug('[go2rtc log]', h.log_tail.join('\n'));
    }
    return h;
  } catch (e) {
    // TypeError = network-level failure (server not running / connection refused)
    // Two consecutive failures required to avoid a false alarm on a single blip
    if (e instanceof TypeError) {
      _serverOfflineCount++;
      if (_serverOfflineCount >= 2) _showServerOffline();
    } else {
      dot.className = '';
      dot.title = 'Stream engine status unknown';
    }
    return null;
  }
}

async function reconnectStreams() {
  const dot = document.getElementById('rtc-status');
  dot.className = '';
  dot.title = 'Restarting…';
  try {
    await fetch('/api/go2rtc/restart', { method: 'POST' });
  } catch (e) { /* ignore */ }
  // Give go2rtc 2s to come back up then re-render to reconnect video-rtc elements
  await new Promise(r => setTimeout(r, 2500));
  renderGrid();
  await checkGo2rtcHealth();
}

// Per-camera reconnect: bounce just this stream's src unless the engine is dead,
// in which case fall back to a full go2rtc restart. Avoids dropping everyone else's
// streams when a single camera has a network hiccup.
async function reconnectCamera(vs) {
  const savedSrc = vs.src;
  try {
    const h = await fetch('/api/go2rtc/health').then(r => r.json());
    if (!h.alive || !h.api_ok) {
      reconnectStreams();
    } else {
      vs.src = null;
      setTimeout(() => { if (vs.isConnected) vs.src = savedSrc; }, 400);
    }
  } catch {
    reconnectStreams();
  }
}

function monitorCard(wrap, vs) {
  const timer = setInterval(() => {
    if (!wrap.isConnected) { clearInterval(timer); return; }
    const video = vs.querySelector('video');
    const hasVideo = video && video.videoWidth > 0;
    const existing = wrap.querySelector('.stream-offline');
    if (hasVideo) {
      existing?.remove();
    } else if (!existing) {
      const div = document.createElement('div');
      div.className = 'stream-offline';
      const offlineMsg = document.createElement('span');
      offlineMsg.textContent = '⚠ Stream offline';
      const reconnBtn = document.createElement('button');
      reconnBtn.textContent = 'Reconnect';
      reconnBtn.addEventListener('click', () => { div.remove(); reconnectCamera(vs); });
      div.appendChild(offlineMsg);
      div.appendChild(reconnBtn);
      wrap.appendChild(div);
    }
  }, 5000);
}

// ── Init ─────────────────────────────────────────────────────────────────────

async function init() {
  await customElements.whenDefined('video-rtc');
  applyTheme(localStorage.getItem('theme') === 'light');
  fetch('/api/version').then(r => r.json()).then(d => {
    document.getElementById('app-version').textContent = `v${d.version}`;
  }).catch(() => {});
  const camResp = await fetch('/cameras');
  if (camResp.status === 401) {
    // Session expired (e.g. server restarted) — browser may have served cached HTML.
    // Redirect to login and come back here after re-auth.
    location.href = '/login.html?next=' + encodeURIComponent(location.pathname + location.search);
    return;
  }
  const cameras = await camResp.json();
  const n = cameras.length;

  document.getElementById('cam-count').textContent = `${n} camera${n !== 1 ? 's' : ''}`;

  // Build layout buttons
  const layoutContainer = document.getElementById('layout-btns');
  LAYOUTS.forEach(layout => {
    const btn = document.createElement('button');
    btn.textContent = layout.label;
    btn.dataset.layout = layout.label;
    btn.addEventListener('click', () => setLayout(layout));
    layoutContainer.appendChild(btn);
  });

  _cameras       = cameras;
  _pageGroups    = computePageGroups(cameras);
  _useNamedPages = cameras.some(c => c.page);
  _groupIdx      = 0;
  const firstGroupSize = _useNamedPages ? (_pageGroups[0]?.cameras.length ?? n) : n;
  _layout = defaultLayout(firstGroupSize);

  document.querySelectorAll('#layout-btns button').forEach(b => {
    b.classList.toggle('active', b.dataset.layout === _layout.label);
  });

  _labelsVisible = localStorage.getItem('labels') !== '0';
  if (!_labelsVisible) {
    document.getElementById('grid').classList.add('hide-labels');
    const btn = document.getElementById('labels-btn');
    if (btn) { btn.title = 'Show camera labels'; btn.style.opacity = '0.5'; }
  }

  _syncChimeBtn();
  _keepLive = localStorage.getItem('keepLive') === '1';
  if (_keepLive) {
    const btn = document.getElementById('keeplive-btn');
    if (btn) {
      btn.classList.add('active');
      btn.title = 'Streams stay live when tab is hidden (click to disable)';
    }
  }

  renderGrid();

  if (window._isMobile) {
    setLayout(LAYOUTS[0]);
    applyMobileUI();  // re-run after layout buttons exist so stream-btns sizes apply
  }

  await checkGo2rtcHealth();
  setInterval(checkGo2rtcHealth, 15000);
}

init().catch(console.error);

// ── Mobile UI ─────────────────────────────────────────────────────────────────

function toggleMobileMenu() {
  const menu    = document.getElementById('mob-menu');
  const overlay = document.getElementById('mob-menu-overlay');
  const open    = !menu.classList.contains('open');
  menu.classList.toggle('open', open);
  overlay.classList.toggle('open', open);
  if (open) _syncMobLayoutBtns();
}

function _syncMobLayoutBtns() {
  const container = document.getElementById('mob-layout-btns');
  if (!container) return;
  container.innerHTML = '';
  LAYOUTS.forEach(layout => {
    const btn = document.createElement('button');
    btn.textContent = layout.label;
    btn.classList.toggle('active', layout.label === _layout.label);
    btn.onclick = () => { setLayout(layout); toggleMobileMenu(); };
    container.appendChild(btn);
  });
}

function applyMobileUI() {
  // Show hamburger
  const hamburger = document.getElementById('mob-hamburger');
  if (hamburger) hamburger.style.setProperty('display', 'flex', 'important');

  // Show bottom nav
  const mobileNav = document.getElementById('mobile-nav');
  if (mobileNav) mobileNav.style.setProperty('display', 'flex', 'important');

  // Shrink body height so grid doesn't extend behind the fixed bottom nav
  document.body.style.paddingBottom = 'calc(64px + env(safe-area-inset-bottom, 0px))';

  // Hide desktop-only header items
  ['cam-count', 'layout-btns', 'nav-btns', 'rtc-status',
   'fullscreen-grid-btn', 'labels-btn', 'keeplive-btn', 'chime-btn',
   'theme-btn', 'settings-btn']
    .forEach(id => {
      const el = document.getElementById(id);
      if (el) el.style.setProperty('display', 'none', 'important');
    });

  // Compact header and stream buttons for mobile
  const header = document.querySelector('header');
  if (header) { header.style.padding = '6px 10px'; header.style.gap = '8px'; }
  document.querySelectorAll('#stream-btns button').forEach(b => {
    b.style.padding = '5px 12px'; b.style.fontSize = '0.8rem';
  });
}

(function detectMobile() {
  // In standalone (app window / PWA) mode, always use the desktop UI layout
  if (window.matchMedia('(display-mode: standalone)').matches) return;
  if (new URLSearchParams(location.search).get('ui') === 'desktop') return;   // force desktop layout
  const isMobile = 'ontouchstart' in window
    || navigator.maxTouchPoints > 0
    || window.screen.width < 1200
    || window.innerWidth < 1000;
  if (!isMobile) return;
  window._isMobile = true;
  applyMobileUI();
})();
