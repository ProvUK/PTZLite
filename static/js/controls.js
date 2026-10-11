//
// Version: 1.0
// Updated: 2026-10-11
//
// v1.0 - 2026-10-11 - Split out of index.html: Toasts/POST helper, camera controls popover, two-way talk
//
// Loaded as a classic script (shared global scope) in the order listed in
// index.html. Don't convert to an ES module without resolving globals.
//

// ── Feedback: toasts + POST helper ─────────────────────────────────────────────
// Toasts attach to the fullscreen element when one is active (otherwise they'd be
// invisible behind it).
function _toast(msg, ok = false) {
  const parent = document.fullscreenElement || document.webkitFullscreenElement || document.body;
  let host = parent.querySelector(':scope > .toast-host');
  if (!host) { host = document.createElement('div'); host.className = 'toast-host'; parent.appendChild(host); }
  const t = document.createElement('div');
  t.className = 'toast' + (ok ? ' ok' : '');
  t.textContent = msg;
  host.appendChild(t);
  setTimeout(() => t.classList.add('out'), 3800);
  setTimeout(() => t.remove(), 4300);
}

// POST JSON; never throws. On failure shows "<what> failed: <reason>" using the
// server's own message, and returns {ok:false}.
async function _postJSON(url, body, what) {
  try {
    const res = await fetch(url, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body ?? {}),
    });
    let data = {};
    try { data = await res.json(); } catch { /* non-JSON body */ }
    if (res.ok && data.ok !== false) return { ...data, ok: true };
    let why = data.detail || data.error;
    if (!why) why = res.ok ? 'the camera rejected the command' : `server error ${res.status}`;
    if (what) _toast(`${what} failed: ${typeof why === 'string' ? why : JSON.stringify(why)}`);
    return { ...data, ok: false };
  } catch {
    if (what) _toast(`${what} failed: PTZLite server unreachable`);
    return { ok: false };
  }
}

// ── Camera controls popover: absolute zoom, auto-track, guard (Reolink) ────────
// State is fetched lazily when the panel opens (each GET hits the NVR), so the
// grid load isn't slowed. Unsupported features are simply omitted.
async function _openControls(cam, wrap) {
  const panel = document.createElement('div');
  panel.className = 'ctrl-panel';
  panel.dataset.cam = cam.id;
  // Swallow events so interacting with the panel never drives PTZ/zoom/fullscreen.
  ['click', 'mousedown', 'touchstart', 'wheel', 'dblclick'].forEach(ev =>
    panel.addEventListener(ev, e => e.stopPropagation(), { passive: false }));
  panel.innerHTML = '<div class="ctrl-note">Loading…</div>';
  wrap.appendChild(panel);

  const j = (u) => fetch(u).then(r => r.json()).catch(() => ({ supported: false }));
  const [zoom, ai, guard, light, siren] = await Promise.all([
    j(`/zoom/${cam.id}/pos`), j(`/ai/${cam.id}`), j(`/guard/${cam.id}`),
    j(`/light/${cam.id}`), j(`/siren/${cam.id}`),
  ]);
  if (!wrap.contains(panel)) return;   // user closed it while loading
  panel.innerHTML = '';

  // Zoom slider
  if (zoom.supported && zoom.max > zoom.min) {
    const box = document.createElement('div');
    box.style.cssText = 'display:flex;flex-direction:column;gap:5px;';
    const head = document.createElement('div');
    head.className = 'ctrl-row';
    const lab = document.createElement('label'); lab.textContent = 'Zoom';
    const val = document.createElement('span'); val.className = 'ctrl-note';
    const slider = document.createElement('input');
    slider.type = 'range'; slider.min = zoom.min; slider.max = zoom.max; slider.value = zoom.pos;
    const pct = () => Math.round((slider.value - zoom.min) / (zoom.max - zoom.min) * 100);
    val.textContent = pct() + '%';
    slider.addEventListener('input', () => { val.textContent = pct() + '%'; });
    slider.addEventListener('change', () => {
      _postJSON(`/zoom/${cam.id}/pos`, { pos: Number(slider.value) }, 'Zoom');
    });
    head.appendChild(lab); head.appendChild(val);
    box.appendChild(head); box.appendChild(slider);
    panel.appendChild(box);
  }

  // Generic on/off toggle row backed by a POST endpoint
  function toggleRow(labelText, url, initialOn) {
    const row = document.createElement('div'); row.className = 'ctrl-row';
    const lab = document.createElement('label'); lab.textContent = labelText;
    const tog = document.createElement('button');
    tog.className = 'ctrl-tog' + (initialOn ? ' on' : '');
    tog.textContent = initialOn ? 'On' : 'Off';
    tog.addEventListener('click', async () => {
      const want = !tog.classList.contains('on');
      tog.disabled = true;
      const r = await _postJSON(url, { enable: want }, labelText);
      tog.disabled = false;
      const on = (r && r.ok) ? want : tog.classList.contains('on');
      tog.classList.toggle('on', on);
      tog.textContent = on ? 'On' : 'Off';
    });
    row.appendChild(lab); row.appendChild(tog);
    return { row, tog };
  }

  // Auto-tracking
  if (ai.supported) {
    panel.appendChild(toggleRow('Auto-track', `/ai/${cam.id}`, ai.enabled).row);
  }

  // Guard return-to-position
  if (guard.supported) {
    const { row, tog } = toggleRow('Guard return', `/guard/${cam.id}`, guard.enabled);
    panel.appendChild(row);
    const setRow = document.createElement('div'); setRow.className = 'ctrl-row';
    const note = document.createElement('span'); note.className = 'ctrl-note';
    note.textContent = guard.has_pos ? `Returns after ${guard.timeout}s` : 'No position set yet';
    const setBtn = document.createElement('button'); setBtn.className = 'ctrl-mini';
    setBtn.textContent = 'Set here';
    setBtn.title = 'Save the current view as the guard position';
    setBtn.addEventListener('click', async () => {
      setBtn.disabled = true; setBtn.textContent = 'Saving…';
      const r = await _postJSON(`/guard/${cam.id}`, { save_pos: true, enable: true }, 'Guard position');
      setBtn.disabled = false;
      setBtn.textContent = (r && r.ok) ? 'Saved ✓' : 'Failed';
      if (r && r.ok) { tog.classList.add('on'); tog.textContent = 'On'; note.textContent = `Returns after ${guard.timeout}s`; }
      setTimeout(() => { setBtn.textContent = 'Set here'; }, 1500);
    });
    setRow.appendChild(note); setRow.appendChild(setBtn);
    panel.appendChild(setRow);
  }

  // Floodlight (white light) — on/off + brightness
  if (light.supported && light.white) {
    const box = document.createElement('div');
    box.style.cssText = 'display:flex;flex-direction:column;gap:5px;';
    const head = document.createElement('div'); head.className = 'ctrl-row';
    const lab = document.createElement('label'); lab.textContent = 'Floodlight';
    const tog = document.createElement('button');
    tog.className = 'ctrl-tog' + (light.white.on ? ' on' : '');
    tog.textContent = light.white.on ? 'On' : 'Off';
    const slider = document.createElement('input');
    slider.type = 'range'; slider.min = 0; slider.max = 100; slider.value = light.white.bright;
    const postWhite = (body) => _postJSON(`/light/${cam.id}/white`, body, 'Floodlight');
    tog.addEventListener('click', async () => {
      const want = !tog.classList.contains('on');
      tog.disabled = true;
      const r = await postWhite({ enable: want, bright: Number(slider.value) });
      tog.disabled = false;
      const on = (r && r.ok) ? want : tog.classList.contains('on');
      tog.classList.toggle('on', on); tog.textContent = on ? 'On' : 'Off';
    });
    slider.addEventListener('change', () => {
      if (tog.classList.contains('on')) postWhite({ enable: true, bright: Number(slider.value) });
    });
    head.appendChild(lab); head.appendChild(tog);
    box.appendChild(head); box.appendChild(slider);
    panel.appendChild(box);
  }

  // Night vision (IR) — Auto / On / Off
  if (light.supported && light.ir) {
    const row = document.createElement('div'); row.className = 'ctrl-row';
    const lab = document.createElement('label'); lab.textContent = 'Night vision';
    const seg = document.createElement('div'); seg.style.cssText = 'display:flex;gap:3px;';
    ['Auto', 'On', 'Off'].forEach(st => {
      const b = document.createElement('button');
      b.className = 'ctrl-seg' + (light.ir.state === st ? ' on' : '');
      b.textContent = st;
      b.addEventListener('click', async () => {
        const r = await _postJSON(`/light/${cam.id}/ir`, { state: st }, 'Night vision');
        if (r && r.ok) seg.querySelectorAll('.ctrl-seg').forEach(x => x.classList.toggle('on', x === b));
      });
      seg.appendChild(b);
    });
    row.appendChild(lab); row.appendChild(seg);
    panel.appendChild(row);
  }

  // Siren (momentary hold) — only where supported
  if (siren.supported) {
    const row = document.createElement('div'); row.className = 'ctrl-row';
    const lab = document.createElement('label'); lab.textContent = 'Siren';
    const btn = document.createElement('button');
    btn.className = 'ctrl-tog'; btn.textContent = 'Hold';
    btn.title = 'Hold to sound the siren';
    const fire = (on) => _postJSON(`/siren/${cam.id}`, { on }, on ? 'Siren' : null);
    const start = e => { e.preventDefault(); btn.classList.add('on'); fire(true); };
    const stop  = () => { btn.classList.remove('on'); fire(false); };
    btn.addEventListener('mousedown', start);
    btn.addEventListener('touchstart', start, { passive: false });
    ['mouseup', 'mouseleave', 'touchend', 'touchcancel'].forEach(ev => btn.addEventListener(ev, stop));
    row.appendChild(lab); row.appendChild(btn);
    panel.appendChild(row);
  }

  // Two-way talk (push-to-talk to the camera speaker) — Reolink cameras
  if ((cam.brand || 'reolink').toLowerCase() === 'reolink') {
    const row = document.createElement('div'); row.className = 'ctrl-row';
    const lab = document.createElement('label'); lab.textContent = 'Talk';
    const btn = document.createElement('button'); btn.className = 'ctrl-tog';
    btn.textContent = _talkEls[cam.id] ? '● Talking (tap to stop)' : 'Talk';
    if (_talkEls[cam.id]) btn.classList.add('on');
    btn.title = 'Talk through the camera speaker (needs a mic; app opened via localhost/https)';
    btn.addEventListener('click', () => {
      if (_talkEls[cam.id]) _stopTalk(cam.id, btn);
      else _startTalk(cam, btn);
    });
    row.appendChild(lab); row.appendChild(btn);
    panel.appendChild(row);
  }

  // ── "More" sub-menu: minor controls (patrol, save preset) ──
  if (cam.ptz) {
    const moreBtn = document.createElement('button');
    moreBtn.className = 'ctrl-more';
    moreBtn.textContent = 'More ▾';
    const more = document.createElement('div');
    more.style.cssText = 'display:none;flex-direction:column;gap:9px;border-top:1px solid rgba(255,255,255,.12);padding-top:9px;';
    moreBtn.addEventListener('click', () => {
      const open = more.style.display === 'flex';
      more.style.display = open ? 'none' : 'flex';
      moreBtn.textContent = open ? 'More ▾' : 'More ▴';
    });

    // Patrol / auto-cruise
    const pRow = document.createElement('div'); pRow.className = 'ctrl-row';
    const pLab = document.createElement('label'); pLab.textContent = 'Cruise';
    const pTog = document.createElement('button'); pTog.className = 'ctrl-tog'; pTog.textContent = 'Off';
    pTog.addEventListener('click', async () => {
      const want = !pTog.classList.contains('on');
      const r = await _postJSON(`/patrol/${cam.id}`, { start: want }, 'Cruise');
      const on = (r && r.ok) ? want : pTog.classList.contains('on');
      pTog.classList.toggle('on', on); pTog.textContent = on ? 'On' : 'Off';
    });
    pRow.appendChild(pLab); pRow.appendChild(pTog);
    more.appendChild(pRow);

    // Save current view as a preset: name it inline; the server picks the next free slot.
    const sRow = document.createElement('div'); sRow.className = 'ctrl-row';
    const sName = document.createElement('input');
    sName.type = 'text'; sName.className = 'ctrl-input'; sName.maxLength = 31;
    sName.placeholder = 'New preset name';
    const sBtn = document.createElement('button'); sBtn.className = 'ctrl-mini'; sBtn.textContent = 'Save';
    const savePreset = async () => {
      sBtn.disabled = true; sBtn.textContent = 'Saving…';
      const r = await _postJSON(`/preset/${cam.id}/save`, { name: sName.value.trim() }, 'Save preset');
      sBtn.disabled = false; sBtn.textContent = 'Save';
      if (r.ok) {
        _toast(`Saved "${r.name}" (slot ${r.id})`, true);
        sName.value = '';
        _loadPresetButtons(cam, wrap);
      }
    };
    sBtn.addEventListener('click', savePreset);
    sName.addEventListener('keydown', e => { e.stopPropagation(); if (e.key === 'Enter') savePreset(); });
    sRow.appendChild(sName); sRow.appendChild(sBtn);
    more.appendChild(sRow);

    panel.appendChild(moreBtn);
    panel.appendChild(more);
  }

  if (!panel.children.length) {
    panel.innerHTML = '<div class="ctrl-note">No extra controls available for this camera.</div>';
  }
}

// ── Two-way talk (push-to-talk) ───────────────────────────────────────────────
// Opens a mic-only WebRTC connection to the camera's backchannel-enabled _talk
// stream; go2rtc routes the microphone to the camera's own speaker.
const _talkEls = {};   // camId → hidden <video-rtc> while talking
function _startTalk(cam, btn) {
  if (_talkEls[cam.id]) return;
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    // getUserMedia needs a secure context (localhost or https), not a LAN-IP http page
    btn.textContent = 'Mic needs localhost/https';
    setTimeout(() => { if (!_talkEls[cam.id]) btn.textContent = 'Talk'; }, 2500);
    return;
  }
  const el = document.createElement('video-rtc');
  el.media = 'microphone';        // send mic only → go2rtc → camera backchannel
  el.background = true;           // stay connected while hidden
  el.style.display = 'none';
  document.body.appendChild(el);
  el.src = `${_wsBase()}/go2rtc/ws?src=${cam.id}_talk`;
  _talkEls[cam.id] = el;
  btn.classList.add('on');
  btn.textContent = '● Talking (tap to stop)';
}
function _stopTalk(camId, btn) {
  const el = _talkEls[camId];
  if (el) {
    // Stop the mic track so the browser's hot-mic indicator clears, then tear down.
    try { el.pc && el.pc.getSenders().forEach(s => { try { s.track && s.track.stop(); } catch (e) {} }); } catch (e) {}
    el.remove();                  // disconnectedCallback closes the ws + pc
    delete _talkEls[camId];
  }
  if (btn) { btn.classList.remove('on'); btn.textContent = 'Talk'; }
}
function _stopAllTalk() { Object.keys(_talkEls).forEach(id => _stopTalk(id)); }
document.addEventListener('visibilitychange', () => { if (document.hidden) _stopAllTalk(); });

