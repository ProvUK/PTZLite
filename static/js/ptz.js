//
// Version: 1.1
// Updated: 2026-10-11
//
// v1.0 - 2026-10-11 - Split out of index.html: PTZ joystick and click-to-centre
// v1.1 - 2026-10-11 - Double-click to centre (was single click); single click is inert
//
// Loaded as a classic script (shared global scope) in the order listed in
// index.html. Don't convert to an ES module without resolving globals.
//

// ── PTZ Joystick ─────────────────────────────────────────────────────────────

const JOYSTICK_MAX_PX   = 60;
const JOYSTICK_DEAD_PX  =  8;
const JOYSTICK_CLICK_PX =  6;
const JOYSTICK_RATE_MS  = 80;

const _joysticks     = {};
const _dblClickState = {};   // camId → { t, x, y } of the last click, for double-click-to-centre

// Normalise mouse and touch events to a plain {clientX, clientY} object.
function _evClientXY(e) {
  const t = e.touches?.[0] ?? e.changedTouches?.[0];
  return t ? { clientX: t.clientX, clientY: t.clientY } : { clientX: e.clientX, clientY: e.clientY };
}

function joystickStart(e, overlay, camId) {
  if (e.button !== undefined && e.button !== 0) return;  // ignore right/middle mouse; touch has no .button
  e.preventDefault();

  const { clientX, clientY } = _evClientXY(e);
  const rect = overlay.getBoundingClientRect();
  const ox = clientX - rect.left;
  const oy = clientY - rect.top;

  const ring = document.createElement('div');
  ring.className = 'joystick-ring';
  ring.style.left = `${ox}px`;
  ring.style.top  = `${oy}px`;
  overlay.appendChild(ring);

  const knob = document.createElement('div');
  knob.className = 'joystick-knob';
  knob.style.left = `${ox}px`;
  knob.style.top  = `${oy}px`;
  overlay.appendChild(knob);

  const state = {
    active: true,
    originX: clientX, originY: clientY,
    overlayLeft: rect.left, overlayTop: rect.top,
    ox, oy,
    ring, knob,
    moved: false,
    wasMoving: false,
    lastX: 0, lastY: 0,
    timer: null,
    startCoords: { clientX, clientY },
  };
  _joysticks[camId] = state;

  function onMove(ev) {
    ev.preventDefault?.();
    if (state.active) joystickMove(_evClientXY(ev), state, camId);
  }
  function onUp() {
    document.removeEventListener('mousemove', onMove);
    document.removeEventListener('mouseup',   onUp);
    document.removeEventListener('touchmove', onMove);
    document.removeEventListener('touchend',  onUp);
    joystickEnd(overlay, state, camId);
  }
  document.addEventListener('mousemove', onMove);
  document.addEventListener('mouseup',   onUp);
  document.addEventListener('touchmove', onMove, { passive: false });
  document.addEventListener('touchend',  onUp);
}

function joystickMove({ clientX, clientY }, state, camId) {
  const dx = clientX - state.originX;
  const dy = clientY - state.originY;
  const dist = Math.hypot(dx, dy);

  if (dist > JOYSTICK_CLICK_PX) state.moved = true;

  const clamped = Math.min(dist, JOYSTICK_MAX_PX);
  const angle   = Math.atan2(dy, dx);
  state.knob.style.left = `${state.ox + Math.cos(angle) * clamped}px`;
  state.knob.style.top  = `${state.oy + Math.sin(angle) * clamped}px`;

  if (dist < JOYSTICK_DEAD_PX) {
    state.lastX = 0; state.lastY = 0;
    if (state.wasMoving) {
      state.wasMoving = false;
      fetch(`/ptz/${camId}/stop`, { method: 'POST' }).catch(() => {});
    }
    return;
  }

  const mag  = clamped / JOYSTICK_MAX_PX;
  state.lastX = Math.cos(angle) * mag;
  state.lastY = Math.sin(angle) * mag;
  state.wasMoving = true;

  if (state.timer) return;
  state.timer = setTimeout(() => {
    state.timer = null;
    if (state.active && state.wasMoving) {
      fetch(`/ptz/${camId}/start`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ x: state.lastX, y: state.lastY }),
      }).catch(() => {});
    }
  }, JOYSTICK_RATE_MS);
}

function joystickEnd(overlay, state, camId) {
  state.active = false;
  if (state.timer) { clearTimeout(state.timer); state.timer = null; }
  state.ring.remove();
  state.knob.remove();
  delete _joysticks[camId];

  // Always stop if the joystick was pulsing — even on a click, in-flight /start
  // requests can arrive at the camera after the click-to-move command, leaving it running.
  if (state.wasMoving) {
    fetch(`/ptz/${camId}/stop`, { method: 'POST' }).catch(() => {});
  }
  if (!state.moved) _camClick(state.startCoords, overlay, camId, true);
}

// Click gestures on a camera (drags are handled separately as joystick / pan):
//   double-click → centre the camera on that point (PTZ cameras)
//   single click → nothing, so a stray click never moves a camera
// Exception: manual calibration's "click the object" steps take single clicks.
// Fullscreen is on right-click (see buildCard).
const _DBL_MS = 350, _DBL_PX = 24;
function _camClick(coords, overlay, camId, hasPtz) {
  if (_calib[camId]) { handlePtzClick(coords, overlay, camId); return; }
  if (!hasPtz) return;
  const now = Date.now();
  const ds = _dblClickState[camId] || (_dblClickState[camId] = { t: 0, x: 0, y: 0 });
  const near = Math.hypot(coords.clientX - ds.x, coords.clientY - ds.y) <= _DBL_PX;
  if (now - ds.t < _DBL_MS && near) {
    ds.t = 0;                                   // second click: move now
    handlePtzClick(coords, overlay, camId);
  } else {
    ds.t = now; ds.x = coords.clientX; ds.y = coords.clientY;   // first click: remember
  }
}

// ── PTZ click ─────────────────────────────────────────────────────────────────

function showRipple(overlay, x, y) {
  const r = document.createElement('div');
  r.className = 'ripple';
  r.style.left = `${x * 100}%`;
  r.style.top  = `${y * 100}%`;
  overlay.appendChild(r);
  r.addEventListener('animationend', () => r.remove(), { once: true });
}

const _ptzBusy = {};  // camId → bool; true while a click-to-centre move is in flight

async function handlePtzClick(e, overlay, camId) {
  const rect = overlay.getBoundingClientRect();
  const x = (e.clientX - rect.left) / rect.width;
  const y = (e.clientY - rect.top)  / rect.height;

  // In calibration mode, clicks drive the guided two-click flow instead of a move
  if (_calib[camId]) { handleCalibClick(camId, overlay, x, y); return; }

  if (_ptzBusy[camId]) return;   // ignore all clicks until current move completes
  _ptzBusy[camId] = true;
  showRipple(overlay, x, y);
  try {
    await fetch(`/ptz/${camId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ x, y }),
    });
  } catch (err) { console.error('PTZ error:', err); }
  finally { _ptzBusy[camId] = false; }
}

