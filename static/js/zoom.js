//
// Version: 1.1
// Updated: 2026-10-11
//
// v1.0 - 2026-10-11 - Split out of index.html: Digital zoom/pan and optical zoom
// v1.1 - 2026-10-11 - Zoomed-in clicks use the shared double-click-to-centre handler
//
// Loaded as a classic script (shared global scope) in the order listed in
// index.html. Don't convert to an ES module without resolving globals.
//

// ── Zoom ─────────────────────────────────────────────────────────────────────

const _digitalZoom  = {};
const _opticalQueue = {};
const _audioMuted   = {}; // camId → bool; default true (muted)


function _applyZoomTransform(overlay, vsEl, camId, scale, tx, ty) {
  // Store the overlay's LAYOUT dimensions alongside tx/ty so that if the overlay
  // later resizes (e.g. tile → fullscreen), we can scale the pixel offsets to match.
  _digitalZoom[camId] = { scale, tx, ty, w: overlay.offsetWidth, h: overlay.offsetHeight };
  vsEl.style.transformOrigin = 'center center';
  vsEl.style.transform = scale === 1 ? '' : `translate(${tx}px, ${ty}px) scale(${scale})`;
  overlay.style.cursor = scale > 1 ? 'grab' : 'crosshair';
  vsEl.parentElement?.classList.toggle('dz-pixelated', scale > 2);
}

function applyDigitalZoom(e, overlay, vsEl, camId) {
  const factor = e.deltaY < 0 ? 1.35 : (1 / 1.35);
  const state  = _digitalZoom[camId] || { scale: 1, tx: 0, ty: 0 };
  const next   = Math.max(1.0, state.scale * factor);
  if (next === state.scale) return;

  const rect = overlay.getBoundingClientRect();
  const cx = e.clientX - rect.left  - rect.width  / 2;
  const cy = e.clientY - rect.top   - rect.height / 2;

  const ratio = next / state.scale;
  let tx = cx * (1 - ratio) + state.tx * ratio;
  let ty = cy * (1 - ratio) + state.ty * ratio;

  const maxX = (next - 1) * rect.width  / 2;
  const maxY = (next - 1) * rect.height / 2;
  tx = Math.max(-maxX, Math.min(maxX, tx));
  ty = Math.max(-maxY, Math.min(maxY, ty));

  _applyZoomTransform(overlay, vsEl, camId, next, tx, ty);
}

function digitalPanStart(e, overlay, vsEl, camId, hasPtz = true) {
  if (e.button !== undefined && e.button !== 0) return;
  e.preventDefault();
  const { clientX: startX, clientY: startY } = _evClientXY(e);
  const startCoords = { clientX: startX, clientY: startY };
  const initial = _digitalZoom[camId] || { scale: 1, tx: 0, ty: 0 };
  const baseTx  = initial.tx;
  const baseTy  = initial.ty;
  let moved = false;
  overlay.style.cursor = 'grabbing';

  function onMove(ev) {
    ev.preventDefault();
    const { clientX, clientY } = _evClientXY(ev);
    const dx = clientX - startX;
    const dy = clientY - startY;
    if (!moved && Math.hypot(dx, dy) < 6) return;
    moved = true;
    const z     = _digitalZoom[camId] || initial;
    const rect  = overlay.getBoundingClientRect();
    const maxX  = (z.scale - 1) * rect.width  / 2;
    const maxY  = (z.scale - 1) * rect.height / 2;
    const tx    = Math.max(-maxX, Math.min(maxX, baseTx + dx));
    const ty    = Math.max(-maxY, Math.min(maxY, baseTy + dy));
    _applyZoomTransform(overlay, vsEl, camId, z.scale, tx, ty);
    overlay.style.cursor = 'grabbing';
  }

  function onUp() {
    document.removeEventListener('mousemove', onMove);
    document.removeEventListener('mouseup',   onUp);
    document.removeEventListener('touchmove', onMove);
    document.removeEventListener('touchend',  onUp);
    overlay.style.cursor = 'grab';
    if (!moved) _camClick(startCoords, overlay, camId, hasPtz);   // same gestures as unzoomed
  }

  document.addEventListener('mousemove', onMove);
  document.addEventListener('mouseup',   onUp);
  document.addEventListener('touchmove', onMove, { passive: false });
  document.addEventListener('touchend',  onUp);
}

function queueOpticalZoom(e, camId) {
  const dir = e.deltaY < 0 ? 1 : -1;
  if (!_opticalQueue[camId]) _opticalQueue[camId] = { dir, steps: 0, timer: null };
  const q = _opticalQueue[camId];
  if (q.dir !== dir) { q.dir = dir; q.steps = 0; }
  q.steps++;
  clearTimeout(q.timer);
  q.timer = setTimeout(async () => {
    const { dir, steps } = _opticalQueue[camId];
    delete _opticalQueue[camId];
    try {
      await fetch(`/zoom/${camId}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ direction: dir, steps }),
      });
    } catch (err) { console.error('Zoom error:', err); }
  }, 120);
}

function handleScroll(e, overlay, vsEl, camId) {
  e.preventDefault();
  if (e.buttons & 2) queueOpticalZoom(e, camId);
  else applyDigitalZoom(e, overlay, vsEl, camId);
}

