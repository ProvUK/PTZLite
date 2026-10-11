//
// Version: 1.0
// Updated: 2026-10-11
//
// v1.0 - 2026-10-11 - Split out of index.html: Manual and automatic click-to-centre calibration
//
// Loaded as a classic script (shared global scope) in the order listed in
// index.html. Don't convert to an ES module without resolving globals.
//

// ── PTZ aim calibration ───────────────────────────────────────────────────────
// Guided two-click flow: click a fixed object, the camera centres on it, then
// click the same object where it actually landed. The residual is sent to
// /calibrate which scales the per-axis gain so a repeat lands dead centre.
const _calib = {};           // camId → { phase, before } while calibrating
const CALIB_SETTLE_MS = 1800; // wait for the gimbal to stop before the 2nd click

function startCalibration(cam, wrap) {
  if (!document.fullscreenElement && !document.webkitFullscreenElement) {
    _enterCamFullscreen(wrap);   // bigger view = more accurate clicks
  }
  _calib[cam.id] = { phase: 'before', before: null };
  const banner = _ensureCalibBanner(wrap, cam.id);
  _setCalibMsg(cam.id, 'Click a fixed object near an edge of the frame', false);
}

function stopCalibration(camId) {
  delete _calib[camId];
  document.querySelector(`.calib-banner[data-cam="${CSS.escape(camId)}"]`)?.remove();
  document.querySelector(`.cam-wrap[data-cam-id="${CSS.escape(camId)}"] .calib-btn`)
    ?.classList.remove('active');
}

// Shared banner shell: a message span plus one or more action buttons.
// `buttons` is a list of { label, onClick }.
function _makeBanner(wrap, camId, buttons) {
  wrap.querySelector('.calib-banner')?.remove();
  const b = document.createElement('div');
  b.className = 'calib-banner';
  b.dataset.cam = camId;
  const msg = document.createElement('span');
  msg.className = 'calib-msg';
  b.appendChild(msg);
  for (const spec of buttons) {
    const btn = document.createElement('button');
    btn.className = 'calib-done';
    btn.textContent = spec.label;
    btn.addEventListener('click', e => { e.stopPropagation(); spec.onClick(); });
    b.appendChild(btn);
  }
  wrap.appendChild(b);
  return b;
}

function _ensureCalibBanner(wrap, camId) {
  return _makeBanner(wrap, camId, [{
    label: 'Done',
    onClick: () => { stopCalibration(camId); wrap._calibBtn?.classList.remove('active'); },
  }]);
}

function _setCalibMsg(camId, text, busy) {
  const b = document.querySelector(`.calib-banner[data-cam="${CSS.escape(camId)}"]`);
  if (!b) return;
  b.querySelector('.calib-msg').textContent = text;
  b.classList.toggle('busy', !!busy);
}

async function handleCalibClick(camId, overlay, x, y) {
  const cal = _calib[camId];
  if (!cal || cal.phase === 'moving' || cal.phase === 'saving') return;
  showRipple(overlay, x, y);

  if (cal.phase === 'before') {
    cal.before = { x, y };
    cal.phase  = 'moving';
    _setCalibMsg(camId, 'Moving — wait for the camera to settle…', true);
    try {
      await fetch(`/ptz/${camId}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ x, y }),
      });
    } catch (err) { console.error('Calib move error:', err); }
    setTimeout(() => {
      if (_calib[camId]?.phase === 'moving') {
        _calib[camId].phase = 'after';
        _setCalibMsg(camId, 'Now click that SAME object where it ended up', false);
      }
    }, CALIB_SETTLE_MS);
    return;
  }

  // phase === 'after'
  cal.phase = 'saving';
  let res = null;
  try {
    res = await fetch(`/ptz/${camId}/calibrate`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ before: cal.before, after: { x, y } }),
    }).then(r => r.json());
  } catch (err) { console.error('Calibrate error:', err); }

  if (_calib[camId]) {           // user may have hit Done mid-request
    _calib[camId].before = null;
    _calib[camId].phase  = 'before';
    _setCalibMsg(camId, _calibResultText(res), false);
  }
}

function _calibResultText(res) {
  if (!res || res.ok === false) {
    return 'Calibration failed — click a fixed edge object to try again.';
  }
  if (res.updated && res.updated.length) {
    const parts = res.updated.map(u => `${u.axis} ${u.old}→${u.new}`);
    const off = Math.round(Math.max(...res.updated.map(u => Math.abs(u.residual))) * 100);
    return `Saved ${parts.join(', ')} · landed ${off}% off centre. `
         + 'Click an edge object to refine, or Done.';
  }
  return 'Not enough travel to measure — pick an object nearer an edge, then re-centre.';
}

// ── Automatic PTZ aim calibration ─────────────────────────────────────────────
// Runs progressively larger timed moves and measures how far the scene actually
// shifts by 1-D image cross-correlation, then least-squares-fits a per-axis motor
// model  duration = degrees/dps + lead.  `lead` captures motor acceleration, so
// both small and large moves land accurately. No clicking — nothing leaves frame.
const _autoCal = {};   // camId → { cancel } while an auto run is in progress

function cancelAutoCalibration(camId) {
  const st = _autoCal[camId];
  if (st) st.cancel = true;
}

function _sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

async function _rawMove(camId, axis, dir, seconds) {
  try {
    await fetch(`/ptz/${camId}/raw_move`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ axis, dir, seconds }),
    });
  } catch (e) { /* a dropped probe is simply skipped */ }
}

// Downsampled, zero-mean grayscale matrix of the frame, for 2-D block matching.
// Keeping the full 2-D image (not a 1-D strip) means unique objects — cars, a
// skip, houses — anchor the match, so a scene that merely has a repeating TEXTURE
// (paving, decking) in one direction is no longer mistaken for an ambiguous one.
function _grayMat(vid, ctx, gw, gh) {
  ctx.drawImage(vid, 0, 0, gw, gh);
  const d = ctx.getImageData(0, 0, gw, gh).data;
  const m = new Float32Array(gw * gh);
  let mean = 0;
  for (let p = 0, i = 0; p < gw * gh; p++, i += 4) {
    const v = 0.299 * d[i] + 0.587 * d[i + 1] + 0.114 * d[i + 2];
    m[p] = v; mean += v;
  }
  mean /= (gw * gh);
  for (let p = 0; p < m.length; p++) m[p] -= mean;
  return m;
}

// Coarse grayscale snapshot (subsampled) — used to detect when the picture has
// stopped changing after a move (motion settled AND stream latency caught up).
function _grayFrame(vid, ctx, cw, ch) {
  ctx.drawImage(vid, 0, 0, cw, ch);
  const d = ctx.getImageData(0, 0, cw, ch).data;
  const out = [];
  for (let y = 0; y < ch; y += 8) {
    for (let x = 0; x < cw; x += 8) {
      const i = (y * cw + x) * 4;
      out.push(0.299 * d[i] + 0.587 * d[i + 1] + 0.114 * d[i + 2]);
    }
  }
  return out;
}

// Wait until the picture has stopped changing. `minMs` is an initial wait used
// after a move to let the (latency-delayed) stream actually START showing the new
// view — otherwise the not-yet-updated frame looks "stable" and we'd lock onto the
// stale pre-move image (measuring zero movement). Then watch for two stable reads.
async function _settle(vid, ctx, cw, ch, minMs = 0, maxMs = 2800) {
  if (minMs) await _sleep(minMs);
  let prev = _grayFrame(vid, ctx, cw, ch);
  const t0 = Date.now();
  let stable = 0;
  while (Date.now() - t0 < maxMs) {
    await _sleep(140);
    const cur = _grayFrame(vid, ctx, cw, ch);
    let diff = 0;
    for (let i = 0; i < cur.length; i++) diff += Math.abs(cur[i] - prev[i]);
    diff /= cur.length;
    prev = cur;
    if (diff < 3.0) { stable += 140; if (stable >= 280) return; }
    else stable = 0;
  }
}

// 2-D normalised cross-correlation over a search window. Returns the sub-pixel
// shift (dx,dy), peak confidence (-1..1), and `ratio` = best peak ÷ the strongest
// SEPARATE local-maximum peak. A genuinely repeating scene yields a rival peak
// (ratio≈1); a unique scene does not, regardless of any repeating texture.
function _xcorr2d(ref, cur, gw, gh, maxDx, maxDy) {
  const W = 2 * maxDx + 1, H = 2 * maxDy + 1;
  const grid = new Float32Array(W * H);
  let best = -2, bdx = 0, bdy = 0;
  for (let dy = -maxDy; dy <= maxDy; dy++) {
    const y0 = Math.max(0, -dy), y1 = Math.min(gh, gh - dy);
    for (let dx = -maxDx; dx <= maxDx; dx++) {
      const x0 = Math.max(0, -dx), x1 = Math.min(gw, gw - dx);
      let s = 0, na = 0, nb = 0;
      for (let y = y0; y < y1; y++) {
        let r = y * gw + x0, c = (y + dy) * gw + x0 + dx;
        for (let x = x0; x < x1; x++, r++, c++) {
          const a = ref[r], b = cur[c];
          s += a * b; na += a * a; nb += b * b;
        }
      }
      const cc = (na > 1e-6 && nb > 1e-6) ? s / Math.sqrt(na * nb) : 0;
      grid[(dy + maxDy) * W + (dx + maxDx)] = cc;
      if (cc > best) { best = cc; bdx = dx; bdy = dy; }
    }
  }
  const sepx = Math.max(2, Math.floor(W * 0.12)), sepy = Math.max(2, Math.floor(H * 0.12));
  let second = -2;
  for (let iy = 1; iy < H - 1; iy++) {
    for (let ix = 1; ix < W - 1; ix++) {
      if (Math.abs(ix - maxDx - bdx) < sepx && Math.abs(iy - maxDy - bdy) < sepy) continue;
      const c = grid[iy * W + ix];
      if (c >= grid[iy * W + ix - 1] && c >= grid[iy * W + ix + 1] &&
          c >= grid[(iy - 1) * W + ix] && c >= grid[(iy + 1) * W + ix] && c > second) second = c;
    }
  }
  const ratio = second > 0.05 ? best / second : 9;
  const at = (dx, dy) => (dx >= -maxDx && dx <= maxDx && dy >= -maxDy && dy <= maxDy)
    ? grid[(dy + maxDy) * W + (dx + maxDx)] : null;
  let sdx = bdx, sdy = bdy;
  const xm = at(bdx - 1, bdy), xp = at(bdx + 1, bdy);
  if (xm !== null && xp !== null) { const dd = xm - 2 * best + xp; if (dd !== 0) sdx = bdx + 0.5 * (xm - xp) / dd; }
  const ym = at(bdx, bdy - 1), yp = at(bdx, bdy + 1);
  if (ym !== null && yp !== null) { const dd = ym - 2 * best + yp; if (dd !== 0) sdy = bdy + 0.5 * (ym - yp) / dd; }
  return { dx: sdx, dy: sdy, conf: best, ratio };
}

// One probe: settle, snapshot, move `t`s in `dir`, settle, measure the 2-D shift.
// Searches the full move axis but only a small cross-axis band. `cap` (pixels)
// limits the move-axis search once a speed estimate exists, to reject aliasing.
// Does NOT return to start — the caller pairs +dir/-dir probes so drift cancels.
async function _probe(camId, axis, t, dir, vid, ctx, gw, gh, state, cap) {
  await _settle(vid, ctx, gw, gh);          // confirm the starting view is steady
  if (state.cancel) return null;
  const ref = _grayMat(vid, ctx, gw, gh);
  await _rawMove(camId, axis, dir, t);
  await _settle(vid, ctx, gw, gh, 600);     // 600ms latency bridge, THEN wait for steady
  if (state.cancel) return null;
  const cur = _grayMat(vid, ctx, gw, gh);
  const mainSpan = Math.floor((axis === 'pan' ? gw : gh) * 0.45);
  const maxMain  = cap ? Math.max(3, Math.min(mainSpan, Math.ceil(cap))) : mainSpan;
  const maxCross = Math.max(3, Math.floor((axis === 'pan' ? gh : gw) * 0.08));
  const maxDx = axis === 'pan' ? maxMain : maxCross;
  const maxDy = axis === 'tilt' ? maxMain : maxCross;
  const r = _xcorr2d(ref, cur, gw, gh, maxDx, maxDy);
  const comp = axis === 'pan' ? r.dx / gw : r.dy / gh;
  return { normShift: Math.abs(comp), conf: r.conf, ratio: r.ratio };
}

async function _calibrateAxisAuto(camId, fov, vid, axis, state, setMsg) {
  const vw = vid.videoWidth, vh = vid.videoHeight;
  // Small grid keeps 2-D matching fast; enough resolution for sub-pixel shifts.
  const gw = 96, gh = Math.max(40, Math.round(96 * vh / vw));
  const canvas = document.createElement('canvas');
  canvas.width = gw; canvas.height = gh;
  const ctx = canvas.getContext('2d', { willReadFrequently: true });

  // Exact camera rotation for a normalised screen shift: a shift of `ns` (fraction
  // of the frame) means a feature moved to 2·ns of the half-frame, i.e. the same
  // geometry a click uses — tan(θ)=2·ns·tan(FOV/2). Using atan (not the small-angle
  // linear form) avoids over-reading bigger shifts and keeps cal consistent with clicks.
  const tanHalf    = Math.tan(fov * Math.PI / 360);
  const degOf      = (ns) => Math.atan(2 * ns * tanHalf) * 180 / Math.PI;
  const degPerNorm = 2 * tanHalf * 180 / Math.PI;   // small-angle slope, for window sizing only
  const base = axis === 'pan' ? gw : gh;
  const MIN_CONF  = 0.3;    // a genuine image match
  const MIN_RATIO = 1.12;   // dominant peak — rejects a genuinely repeating scene
  const TARGET    = 0.22;   // aim each probe at ~22% of the frame — a "serious" move with unique structure
  const ratios    = [];     // collected degrees-per-second samples
  let est = 0;              // running dps estimate; seeds the anti-alias search window
  // ±window (in correlation lag) expected for a move, from the current estimate.
  const capFor = (t) => est ? Math.max(4, (est * t / degPerNorm) * base * 1.6) : 0;

  // ── Phase 1: range-find a clean base move time ──
  // Grow/shrink the move until one probe lands a confident, unambiguous 2-D match
  // of ~TARGET size:
  //  • featureless / travel-limit (low confidence, e.g. tilt into sky) → flip direction
  //  • genuinely repeating scene (rival peak) → shrink (more overlap shares more
  //    unique structure); too big (frames barely overlap) → shrink too
  //  • the first clean shift seeds `est`, and every later probe is locked to a
  //    search window around the expected position, so a wrong match can't sneak in.
  let t = 0.18, tBase = 0;
  let dir = axis === 'tilt' ? -1 : 1;   // tilt: probe DOWN first (toward ground, away from sky)
  for (let attempt = 0; attempt < 11 && !tBase; attempt++) {
    if (state.cancel) return null;
    setMsg(`Finding ${axis} range… (${attempt + 1})`, true);
    const p = await _probe(camId, axis, t, dir, vid, ctx, gw, gh, state, capFor(t));
    if (!p || state.cancel) return null;
    await _rawMove(camId, axis, -dir, t);   // return to start
    await _settle(vid, ctx, gw, gh, 600);
    if (p.conf >= MIN_CONF && p.ratio >= MIN_RATIO && p.normShift >= 0.03 && p.normShift <= 0.4) {
      tBase = t;
      est = degOf(p.normShift) / t;
      ratios.push(est);
    } else if (p.conf < MIN_CONF || p.normShift < 0.012) {
      dir = -dir;                                  // featureless or hit a limit — try the other way
    } else if (p.ratio < MIN_RATIO || p.normShift > 0.4) {
      t = Math.max(0.05, t * 0.6);                 // repeating or too big — smaller, more-overlap move
    } else {
      const grow = p.normShift > 0.02 ? TARGET / p.normShift : 2.2;
      t = Math.min(2.5, t * Math.min(grow, 5));    // too small — grow toward the target shift
    }
  }
  if (!tBase) return { error: `couldn't track ${axis} cleanly (no usable view either way)` };
  const probeDir = dir;   // the direction that had texture — lead with it in phase 2

  // ── Phase 2: repeated bidirectional probes around the base ──
  const sizes = [0.75, 1.25];
  let n = 0; const total = sizes.length * 2;
  for (const f of sizes) {
    const tp = Math.max(0.05, Math.min(2.5, tBase * f));
    for (const d of [probeDir, -probeDir]) {   // good direction first; -dir returns to start
      if (state.cancel) return null;
      setMsg(`Measuring ${axis}… (${++n}/${total})`, true);
      const p = await _probe(camId, axis, tp, d, vid, ctx, gw, gh, state, capFor(tp));
      if (!p) return null;
      if (p.conf >= MIN_CONF && p.ratio >= MIN_RATIO &&
          p.normShift > 0.02 && p.normShift < 0.5) {
        const r = degOf(p.normShift) / tp;
        ratios.push(r);
        est = est ? est * 0.7 + r * 0.3 : r;   // smooth estimate for the next window
      }
    }
  }

  if (ratios.length < 3) return { error: `${axis}: too few clean samples (scene too repetitive)` };

  // ── Robust aggregate: median, then mean of samples within ±20% of it ──
  const sorted = ratios.slice().sort((a, b) => a - b);
  const median = sorted[Math.floor(sorted.length / 2)];
  const keep = ratios.filter(r => Math.abs(r - median) <= 0.2 * median);
  const dps = keep.reduce((s, r) => s + r, 0) / keep.length;
  if (!(dps > 1)) return { error: `${axis} fit failed` };
  const spread = (Math.max(...keep) - Math.min(...keep)) / dps;
  return { dps, quality: spread < 0.15 ? 'good' : 'rough' };
}

async function startAutoCalibration(cam, wrap) {
  const camId = cam.id;
  if (_autoCal[camId]) return;
  if (!document.fullscreenElement && !document.webkitFullscreenElement) {
    _enterCamFullscreen(wrap);
  }
  wrap._calibBtn?.classList.add('active');
  _makeBanner(wrap, camId, [{ label: 'Cancel', onClick: () => cancelAutoCalibration(camId) }]);
  const setMsg = (txt, busy) => _setCalibMsg(camId, txt, busy);

  const vid = wrap.querySelector('video-rtc')?.querySelector('video');
  if (!vid || !vid.videoWidth) {
    _finishAutoCal(cam, wrap, 'No live video to measure — is the stream up?');
    return;
  }

  const state = { cancel: false };
  _autoCal[camId] = state;

  // FOV isn't in the lightweight /cameras payload — pull the full record.
  let fovH = 90, fovV = 55;
  try {
    const all = await fetch('/api/cameras').then(r => r.json());
    const full = all.find(c => c.id === camId);
    if (full) { fovH = full.fov_h || 90; fovV = full.fov_v || 55; }
  } catch (e) { /* fall back to defaults */ }

  const out = [];
  try {
    for (const [axis, fov] of [['pan', fovH], ['tilt', fovV]]) {
      if (state.cancel) break;
      const res = await _calibrateAxisAuto(camId, fov, vid, axis, state, setMsg);
      if (state.cancel || !res) break;
      if (res.error) { out.push(res.error); continue; }
      await _postJSON(`/ptz/${camId}/set_axis_cal`, { axis, dps: res.dps }, `Saving ${axis} calibration`);
      out.push(`${axis} ${res.dps.toFixed(1)}°/s${res.quality === 'rough' ? ' (rough)' : ''}`);
    }
  } catch (err) {
    console.error('Auto-cal error:', err);
    out.push('error reading video frames');
  }

  if (state.cancel) { _finishAutoCal(cam, wrap, 'Calibration cancelled.'); return; }
  _finishAutoCal(cam, wrap, out.length ? ('Saved: ' + out.join('  ·  ')) : 'Nothing measured.');
}

function _finishAutoCal(cam, wrap, msg) {
  delete _autoCal[cam.id];
  wrap._calibBtn?.classList.remove('active');
  const banner = _makeBanner(wrap, cam.id, [
    { label: 'Manual', onClick: () => { wrap.querySelector('.calib-banner')?.remove();
                                        startCalibration(cam, wrap);
                                        wrap._calibBtn?.classList.add('active'); } },
    { label: 'Close',  onClick: () => wrap.querySelector('.calib-banner')?.remove() },
  ]);
  banner.querySelector('.calib-msg').textContent = msg;
}

// Entry menu for the ⊕ button — pick Auto, Manual, or Reset-to-defaults.
function _openCalibMenu(cam, wrap) {
  if (!document.fullscreenElement && !document.webkitFullscreenElement) {
    _enterCamFullscreen(wrap);
  }
  wrap._calibBtn?.classList.add('active');
  const close = () => {
    wrap.querySelector('.calib-banner')?.remove();
    wrap._calibBtn?.classList.remove('active');
  };
  const banner = _makeBanner(wrap, cam.id, [
    { label: 'Auto',   onClick: () => { wrap.querySelector('.calib-banner')?.remove();
                                        startAutoCalibration(cam, wrap); } },
    { label: 'Manual', onClick: () => { wrap.querySelector('.calib-banner')?.remove();
                                        startCalibration(cam, wrap);
                                        wrap._calibBtn?.classList.add('active'); } },
    { label: 'Reset',  onClick: () => _resetCalibration(cam) },
    { label: 'Close',  onClick: close },
  ]);
  banner.querySelector('.calib-msg').textContent = 'Calibrate aim:';
}

async function _resetCalibration(cam) {
  _setCalibMsg(cam.id, 'Resetting to defaults…', true);
  try {
    const r = await fetch(`/ptz/${cam.id}/reset_cal`, { method: 'POST' });
    _setCalibMsg(cam.id, r.ok ? 'Aim calibration reset to defaults.'
                              : 'Reset failed — check the server.', false);
  } catch (e) {
    _setCalibMsg(cam.id, 'Reset failed — check the server.', false);
  }
}

