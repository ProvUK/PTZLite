//
// Version: 1.0
// Updated: 2026-10-11
//
// v1.0 - 2026-10-11 - Split out of index.html: Camera cards, fullscreen, presets bar
//
// Loaded as a classic script (shared global scope) in the order listed in
// index.html. Don't convert to an ES module without resolving globals.
//

// ── Build camera card ─────────────────────────────────────────────────────────

const FS_ICON = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
  <polyline points="15 3 21 3 21 9"/><polyline points="9 21 3 21 3 15"/>
  <line x1="21" y1="3" x2="14" y2="10"/><line x1="3" y1="21" x2="10" y2="14"/>
</svg>`;

const SNAP_ICON = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/><circle cx="12" cy="13" r="4"/></svg>`;

const CALIB_ICON = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="8"/><line x1="12" y1="1" x2="12" y2="5"/><line x1="12" y1="19" x2="12" y2="23"/><line x1="1" y1="12" x2="5" y2="12"/><line x1="19" y1="12" x2="23" y2="12"/><circle cx="12" cy="12" r="1.5" fill="currentColor"/></svg>`;

const CTRL_ICON = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="4" y1="21" x2="4" y2="14"/><line x1="4" y1="10" x2="4" y2="3"/><line x1="12" y1="21" x2="12" y2="12"/><line x1="12" y1="8" x2="12" y2="3"/><line x1="20" y1="21" x2="20" y2="16"/><line x1="20" y1="12" x2="20" y2="3"/><line x1="1" y1="14" x2="7" y2="14"/><line x1="9" y1="8" x2="15" y2="8"/><line x1="17" y1="16" x2="23" y2="16"/></svg>`;

const MUTE_ICON   = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><line x1="23" y1="9" x2="17" y2="15"/><line x1="17" y1="9" x2="23" y2="15"/></svg>`;
const UNMUTE_ICON = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><path d="M19.07 4.93a10 10 0 0 1 0 14.14M15.54 8.46a5 5 0 0 1 0 7.07"/></svg>`;

// Fullscreen state — track which element is fullscreened and what it was playing
// so the in-fullscreen stream toggle can restore on exit.
let _fsVsEl    = null;   // the video-rtc element currently in fullscreen
let _fsOrigSrc = null;   // stream URL it was playing when fullscreen was entered

/**
 * Switch a video-rtc element to a new stream URL without triggering the 15-second
 * reconnect timer.
 *
 * Root cause: ondisconnect() sets wsState=CLOSED and calls ws.close() (async).
 * video-rtc also listens to visibilitychange and calls connectedCallback() when the
 * document becomes visible — which happens on fullscreen entry on some systems.
 * connectedCallback() calls onconnect(), which sets wsState=CONNECTING and opens a
 * new WebSocket. When the OLD socket's close event then fires, onclose() sees
 * wsState=CONNECTING (not CLOSED), skips its guard, nulls this.ws (killing the new
 * connection) and schedules a 15-second reconnect timer.
 *
 * Fix: after ondisconnect(), immediately blank wsURL. Any spurious onconnect() calls
 * (from connectedCallback or anywhere else) fail the !this.wsURL guard and bail out.
 * We wait for the old socket's close event (at which point wsState is still CLOSED
 * and onclose() bails correctly), then set the new src — clean, no races, no timers.
 */
function _switchVideoStream(vsEl, newSrc) {
  // Cancel any stale reconnect or delayed-disconnect timers
  if (vsEl.reconnectTID)  { clearTimeout(vsEl.reconnectTID);  vsEl.reconnectTID  = 0; }
  if (vsEl.disconnectTID) { clearTimeout(vsEl.disconnectTID); vsEl.disconnectTID = 0; }

  const oldWs = vsEl.ws;   // save ref BEFORE ondisconnect() clears it
  vsEl.ondisconnect();     // wsState=CLOSED, oldWs?.close(), ws=null, pc=null

  // Blank wsURL so any spurious onconnect() (e.g. from visibilitychange →
  // connectedCallback) fails the !this.wsURL guard and does nothing.
  vsEl.wsURL = '';

  const doConnect = () => {
    // Clear any disconnectTID that may have been scheduled while we were waiting
    if (vsEl.disconnectTID) { clearTimeout(vsEl.disconnectTID); vsEl.disconnectTID = 0; }
    if (vsEl.isConnected) vsEl.src = newSrc;  // sets wsURL and calls onconnect()
  };

  if (oldWs && oldWs.readyState !== WebSocket.CLOSED) {
    // Old socket still open/closing — wait for its close event.
    // video-rtc's onclose fires first (bails because wsState=CLOSED),
    // then doConnect fires and starts the new connection cleanly.
    oldWs.addEventListener('close', doConnect, { once: true });
  } else {
    // Already closed or no prior connection — connect immediately.
    doConnect();
  }
}

// Update the [Main]/[Sub] toggle button active states to reflect what vs is playing.
// Called on fullscreen entry and after each toggle click.
function _syncFsToggle(toggle, vs) {
  const camId   = vs.dataset.camId;
  const mainSrc = `${_wsBase()}/go2rtc/ws?src=${camId}_main`;
  const onMain  = vs.wsURL === mainSrc;
  toggle.querySelector('.fs-main-btn').classList.toggle('active',  onMain);
  toggle.querySelector('.fs-sub-btn' ).classList.toggle('active', !onMain);
}

function _enterCamFullscreen(wrap) {
  // Request fullscreen FIRST — must be in the synchronous user-gesture call stack.
  if (wrap.requestFullscreen)            wrap.requestFullscreen().catch(() => {});
  else if (wrap.webkitRequestFullscreen) wrap.webkitRequestFullscreen();

  // Track which element entered fullscreen and what stream it's on.
  // The stream does NOT switch automatically — whatever is playing keeps playing.
  // The in-fullscreen toggle buttons let the user switch if they want to.
  const vs = wrap.querySelector('video-rtc');
  if (vs) {
    _fsVsEl    = vs;
    _fsOrigSrc = vs.wsURL || streamSrc(vs.dataset.camId);
  }
}

function _exitFullscreen() {
  const fs = document.fullscreenElement || document.webkitFullscreenElement;
  if (fs) (document.exitFullscreen || document.webkitExitFullscreen).call(document).catch(() => {});
}

// On fullscreen entry: set the stream toggle to reflect what's currently playing.
// On fullscreen exit: if the user switched streams via the toggle, restore the
// original stream so the tile goes back to whatever was active before.
function _onFullscreenChange() {
  if (document.fullscreenElement || document.webkitFullscreenElement) {
    // ── Entering fullscreen ──────────────────────────────────────────────────
    // Sync the toggle buttons to show the stream that's actually playing.
    const wrap = _fsVsEl?.parentElement;
    if (wrap?._fsToggle && _fsVsEl) _syncFsToggle(wrap._fsToggle, _fsVsEl);
  } else {
    // ── Exiting fullscreen ───────────────────────────────────────────────────
    // If the user toggled to a different stream while in fullscreen, restore.
    if (_fsVsEl && _fsOrigSrc && _fsVsEl.wsURL !== _fsOrigSrc) {
      _switchVideoStream(_fsVsEl, _fsOrigSrc);
    }
    _fsOrigSrc = null;
    // Exiting fullscreen cancels any in-progress aim calibration for this camera.
    const exitCamId = _fsVsEl?.dataset.camId;
    if (exitCamId && _autoCal[exitCamId]) cancelAutoCalibration(exitCamId);
    if (exitCamId && _calib[exitCamId]) {
      stopCalibration(exitCamId);
      _fsVsEl?.parentElement?._calibBtn?.classList.remove('active');
    } else if (exitCamId && !_autoCal[exitCamId]) {
      // Tidy up a lingering calibrate menu/result banner left open on exit.
      const w = _fsVsEl?.parentElement;
      w?.querySelector('.calib-banner')?.remove();
      w?._calibBtn?.classList.remove('active');
    }
    // Close the controls popover if it was left open.
    const cw = _fsVsEl?.parentElement;
    if (cw?.querySelector('.ctrl-panel') && exitCamId) _stopTalk(exitCamId);
    cw?.querySelector('.ctrl-panel')?.remove();
    cw?._ctrlBtn?.classList.remove('active');
    // NOTE: do NOT null _fsVsEl yet — the zoom block below still needs it on exit.
  }

  // Rescale zoom-pan to the overlay's NEW size.  Runs for both entry and exit:
  // the stored w/h is from the OLD size, ov.offsetWidth/Height is the NEW size.
  const camId = _fsVsEl?.dataset.camId;
  const z     = camId ? _digitalZoom[camId] : null;
  const ov    = _fsVsEl?.parentElement?._camOverlay;
  if (z && z.scale > 1 && ov && z.w && z.h) {
    _applyZoomTransform(ov, _fsVsEl, camId, z.scale,
      z.tx * (ov.offsetWidth  / z.w),
      z.ty * (ov.offsetHeight / z.h));
  }

  // Clear _fsVsEl after the zoom block has finished with it.
  if (!(document.fullscreenElement || document.webkitFullscreenElement)) {
    _fsVsEl = null;
  }
}
document.addEventListener('fullscreenchange',       _onFullscreenChange);
document.addEventListener('webkitfullscreenchange', _onFullscreenChange);

function toggleFullscreenGrid() {
  if (!document.fullscreenElement) {
    document.documentElement.requestFullscreen().catch(() => {});
  } else {
    document.exitFullscreen();
  }
}

function toggleLabels() {
  _labelsVisible = !_labelsVisible;
  localStorage.setItem('labels', _labelsVisible ? '1' : '0');
  document.getElementById('grid').classList.toggle('hide-labels', !_labelsVisible);
  const btn = document.getElementById('labels-btn');
  btn.title = _labelsVisible ? 'Hide camera labels' : 'Show camera labels';
  btn.style.opacity = _labelsVisible ? '' : '0.5';
}

function toggleKeepLive() {
  _keepLive = !_keepLive;
  localStorage.setItem('keepLive', _keepLive ? '1' : '0');
  document.querySelectorAll('video-rtc').forEach(vs => { vs.background = _keepLive; });
  const btn = document.getElementById('keeplive-btn');
  btn.classList.toggle('active', _keepLive);
  btn.title = _keepLive ? 'Streams stay live when tab is hidden (click to disable)' : 'Keep streams live when tab is hidden';
}

function saveSnapshot(vs, camName) {
  const video = vs.querySelector('video');
  if (!video || !video.videoWidth) return;
  const canvas = document.createElement('canvas');
  canvas.width  = video.videoWidth;
  canvas.height = video.videoHeight;
  canvas.getContext('2d').drawImage(video, 0, 0);
  canvas.toBlob(blob => {
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    const ts = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
    a.download = `${camName.replace(/[^a-z0-9_-]/gi, '_')}_${ts}.png`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }, 'image/png');
}

function buildCard(cam, streamDelay = 0) {
  const wrap = document.createElement('div');
  wrap.className = 'cam-wrap' + (cam.crop ? ' cam-crop' : '');
  wrap.dataset.camId = cam.id;

  const vs = document.createElement('video-rtc');
  vs.dataset.camId = cam.id;
  vs.background = _keepLive;
  vs.RECONNECT_TIMEOUT = 2000;
  // Use WebRTC with MSE as fallback.  HLS and MJPEG are excluded — they add
  // latency or bandwidth overhead not needed on a local network.  Cameras that
  // can't negotiate WebRTC (e.g. H.265 on some browsers) will fall back to MSE.
  vs.mode = 'webrtc,mse';
  wrap.appendChild(vs);
  if (streamDelay === 0) {
    vs.src = streamSrc(cam.id);
  } else {
    setTimeout(() => { if (wrap.isConnected) vs.src = streamSrc(cam.id); }, streamDelay);
  }
  monitorCard(wrap, vs);

  const label = document.createElement('div');
  label.className = 'cam-label';
  label.textContent = cam.name;
  wrap.appendChild(label);

  // Live detection badge (shown by the detection poller when this camera alerts)
  const detectBadge = document.createElement('div');
  detectBadge.className = 'detect-badge';
  wrap.appendChild(detectBadge);

  // Fullscreen button (top-right, revealed on hover)
  const fsBtn = document.createElement('button');
  fsBtn.className = 'fullscreen-btn';
  fsBtn.title = 'Full screen (Esc to exit)';
  fsBtn.innerHTML = FS_ICON;
  fsBtn.addEventListener('click', e => {
    e.stopPropagation();
    if (!document.fullscreenElement) _enterCamFullscreen(wrap);
    else document.exitFullscreen();
  });
  wrap.appendChild(fsBtn);

  // ── Double-click / double-right-click helpers (local to this card) ──────────
  let _lastRightClick = 0;
  function _onCardRightClick() {
    const now = Date.now();
    if (now - _lastRightClick < 400) _exitFullscreen();
    _lastRightClick = now;
  }

  if (cam.ptz) {
    const badge = document.createElement('div');
    badge.className = 'ptz-badge';  // CSS already places it at right:44px
    badge.textContent = 'PTZ';
    wrap.appendChild(badge);

    const reticle = document.createElement('div');
    reticle.className = 'reticle';
    wrap.appendChild(reticle);

    const overlay = document.createElement('div');
    overlay.className = 'ptz-overlay';
    wrap._camOverlay = overlay;   // stored for fullscreen zoom-pan rescaling
    overlay.addEventListener('mousedown', e => {
      const z = _digitalZoom[cam.id];
      if (z && z.scale > 1) digitalPanStart(e, overlay, vs, cam.id);
      else joystickStart(e, overlay, cam.id);
    });
    overlay.addEventListener('touchstart', e => {
      e.preventDefault();
      const z = _digitalZoom[cam.id];
      if (z && z.scale > 1) digitalPanStart(e, overlay, vs, cam.id);
      else joystickStart(e, overlay, cam.id);
    }, { passive: false });
    overlay.addEventListener('wheel', e => handleScroll(e, overlay, vs, cam.id), { passive: false });
    overlay.addEventListener('contextmenu', e => { e.preventDefault(); _onCardRightClick(); });
    wrap.appendChild(overlay);

    // Preset bar holds presets plus the calibrate & controls buttons (bottom-left)
    buildPresetBar(cam, wrap);
  } else {
    // Non-PTZ cameras get a cover overlay that catches wheel/mouse events for
    // digital zoom and pan, while still showing double-click → fullscreen.
    const cover = document.createElement('div');
    cover.style.cssText = 'position:absolute;inset:0;z-index:3;cursor:crosshair;';
    wrap._camOverlay = cover;   // stored for fullscreen zoom-pan rescaling
    wrap.appendChild(cover);

    // Scroll wheel → digital zoom
    cover.addEventListener('wheel', e => handleScroll(e, cover, vs, cam.id), { passive: false });

    // Mousedown / touchstart → digital pan when zoomed (no PTZ for non-PTZ cams)
    cover.addEventListener('mousedown', e => {
      const z = _digitalZoom[cam.id];
      if (z && z.scale > 1) { e.preventDefault(); digitalPanStart(e, cover, vs, cam.id, false); }
    });
    cover.addEventListener('touchstart', e => {
      const z = _digitalZoom[cam.id];
      if (z && z.scale > 1) { e.preventDefault(); digitalPanStart(e, cover, vs, cam.id, false); }
    }, { passive: false });

    // Double left-click → fullscreen; double right-click → exit fullscreen
    cover.addEventListener('dblclick',    e => { e.stopPropagation(); _enterCamFullscreen(wrap); });
    cover.addEventListener('contextmenu', e => { e.preventDefault(); _onCardRightClick(); });

    // Non-PTZ Reolink cams still get a controls button (floodlight / IR / siren).
    if ((cam.brand || 'reolink').toLowerCase() === 'reolink') {
      const bar = document.createElement('div');
      bar.className = 'preset-bar';
      bar.appendChild(_makeCtrlButton(cam, wrap));
      wrap.appendChild(bar);
    }
  }

  // ── In-fullscreen stream toggle ──────────────────────────────────────────────
  // Visible only when this card is fullscreened.  Lets the user switch between
  // main and sub without leaving fullscreen.  On exit the original stream restores.
  const fsToggle = document.createElement('div');
  fsToggle.className = 'fs-stream-toggle';
  wrap._fsToggle = fsToggle;

  const fsMainBtn = document.createElement('button');
  fsMainBtn.className = 'fs-stream-btn fs-main-btn';
  fsMainBtn.textContent = 'Main';
  fsMainBtn.addEventListener('click', e => {
    e.stopPropagation();
    const src = `${_wsBase()}/go2rtc/ws?src=${cam.id}_main`;
    if (vs.wsURL === src) return;
    _switchVideoStream(vs, src);
    fsMainBtn.classList.add('active');
    fsSubBtn.classList.remove('active');
  });

  const fsSubBtn = document.createElement('button');
  fsSubBtn.className = 'fs-stream-btn fs-sub-btn';
  fsSubBtn.textContent = 'Sub';
  fsSubBtn.addEventListener('click', e => {
    e.stopPropagation();
    const src = `${_wsBase()}/go2rtc/ws?src=${cam.id}_sub`;
    if (vs.wsURL === src) return;
    _switchVideoStream(vs, src);
    fsSubBtn.classList.add('active');
    fsMainBtn.classList.remove('active');
  });

  fsToggle.appendChild(fsMainBtn);
  fsToggle.appendChild(fsSubBtn);
  wrap.appendChild(fsToggle);

  const snapBtn = document.createElement('button');
  snapBtn.className = 'snap-btn';
  snapBtn.title = 'Save snapshot';
  snapBtn.innerHTML = SNAP_ICON;
  snapBtn.addEventListener('click', e => { e.stopPropagation(); saveSnapshot(vs, cam.name); });
  wrap.appendChild(snapBtn);

  // Initialise audio state — default muted, survive view switches via _audioMuted map
  if (_audioMuted[cam.id] === undefined) _audioMuted[cam.id] = true;
  const isMuted = _audioMuted[cam.id];

  const audioBtn = document.createElement('button');
  audioBtn.className = 'audio-btn';
  audioBtn.innerHTML = isMuted ? MUTE_ICON : UNMUTE_ICON;
  audioBtn.title = isMuted ? 'Unmute audio' : 'Mute audio';
  audioBtn.addEventListener('click', e => {
    e.stopPropagation();
    const nowMuted = !_audioMuted[cam.id];
    _audioMuted[cam.id] = nowMuted;
    const vid = vs.querySelector('video');
    if (vid) vid.muted = nowMuted;
    audioBtn.innerHTML = nowMuted ? MUTE_ICON : UNMUTE_ICON;
    audioBtn.title = nowMuted ? 'Unmute audio' : 'Mute audio';
  });
  wrap.appendChild(audioBtn);

  // MutationObserver: fires once when VideoRTC appends its internal <video>.
  // Sets controls=false, applies the persisted mute state, and attaches a live-edge
  // enforcer to prevent the stall → fast-catchup pattern.
  //
  // Root cause of stalling: the browser accumulates a playback buffer that drifts
  // behind the live edge of the WebRTC stream.  The video appears frozen until the
  // buffer is flushed, then jumps forward.  Fix: on every timeupdate, if currentTime
  // has fallen more than 500 ms behind buffered.end, seek back to near-live.
  const _vidObs = new MutationObserver(() => {
    const vid = vs.querySelector('video');
    if (!vid) return;
    vid.controls = false;
    vid.muted = _audioMuted[cam.id] !== false;

    // Stall recovery for WebRTC mode.
    //
    // In WebRTC mode video.srcObject is a MediaStream — video.buffered is always
    // empty so timeupdate-based buffer checks are useless.  Instead we watch for
    // the 'waiting' event (fires when the decoder pipeline backs up) and reattach
    // srcObject, which tells the video element to discard queued frames and
    // restart from the latest one received.  A 400 ms debounce avoids false
    // triggers on normal keyframe gaps.
    let _stallTID = null;
    vid.addEventListener('waiting', () => {
      clearTimeout(_stallTID);
      _stallTID = setTimeout(() => {
        if (vid.paused) return;
        const stream = vid.srcObject;
        if (stream instanceof MediaStream) {
          // Reattach to flush the stale frame pipeline and resume at live edge
          vid.srcObject = null;
          vid.srcObject = stream;
          vid.play().catch(() => {});
        } else if (vid.buffered.length) {
          // MSE fallback: seek to near live edge
          vid.currentTime = vid.buffered.end(vid.buffered.length - 1) - 0.05;
        }
      }, 400);
    });
    vid.addEventListener('playing', () => clearTimeout(_stallTID));

    _vidObs.disconnect();
  });
  _vidObs.observe(vs, { childList: true, subtree: true });

  return wrap;
}

// Creates the camera-controls (⋮≡) button; used on both PTZ and non-PTZ Reolink cards.
function _makeCtrlButton(cam, wrap) {
  const ctrlBtn = document.createElement('button');
  ctrlBtn.className = 'ctrl-btn';
  ctrlBtn.title = 'Camera controls';
  ctrlBtn.innerHTML = CTRL_ICON;
  ctrlBtn.addEventListener('click', e => {
    e.stopPropagation();
    const open = wrap.querySelector('.ctrl-panel');
    if (open) { open.remove(); ctrlBtn.classList.remove('active'); _stopTalk(cam.id); }
    else { ctrlBtn.classList.add('active'); _openControls(cam, wrap); }
  });
  wrap._ctrlBtn = ctrlBtn;
  return ctrlBtn;
}

function buildPresetBar(cam, wrap) {
  // Built synchronously so the calibrate/controls buttons appear immediately;
  // presets are fetched after and inserted to the right of them.
  const bar = document.createElement('div');
  bar.className = 'preset-bar';

  // ── Left: calibrate + controls icon buttons ──
  const calibBtn = document.createElement('button');
  calibBtn.className = 'calib-btn';
  calibBtn.title = 'Calibrate click-to-centre aim';
  calibBtn.innerHTML = CALIB_ICON;
  calibBtn.addEventListener('click', e => {
    e.stopPropagation();
    if (_autoCal[cam.id]) { cancelAutoCalibration(cam.id); }
    else if (_calib[cam.id]) { stopCalibration(cam.id); calibBtn.classList.remove('active'); }
    else { _openCalibMenu(cam, wrap); }
  });
  wrap._calibBtn = calibBtn;
  bar.appendChild(calibBtn);
  bar.appendChild(_makeCtrlButton(cam, wrap));

  wrap.appendChild(bar);

  // ── Right: presets (loaded async, appended after the icon buttons) ──
  _loadPresetButtons(cam, wrap);
}

// (Re)load the preset buttons in a card's preset bar; also called after saving one.
function _loadPresetButtons(cam, wrap) {
  const bar = wrap.querySelector('.preset-bar');
  if (!bar) return;
  async function clickPreset(btn, presetId, label) {
    btn.classList.remove('failed');
    const r = await _postJSON(`/preset/${cam.id}/${presetId}`, {}, `Preset "${label}"`);
    if (r.ok) return;
    void btn.offsetWidth;
    btn.classList.add('failed');
    btn.addEventListener('animationend', () => btn.classList.remove('failed'), { once: true });
  }
  fetch(`/presets/${cam.id}`).then(r => r.json()).then(presets => {
    bar.querySelectorAll('.preset-btn').forEach(b => b.remove());
    (presets || []).slice(0, 4).forEach(p => {
      const btn = document.createElement('button');
      btn.className = 'preset-btn';
      btn.textContent = p.name;
      btn.title = `Go to preset: ${p.name}`;
      btn.addEventListener('click', e => { e.stopPropagation(); clickPreset(btn, p.id, p.name); });
      bar.appendChild(btn);
    });
    if (cam.monitor_preset != null) {
      const btn = document.createElement('button');
      btn.className = 'preset-btn monitor';
      btn.textContent = 'Monitor';
      btn.title = 'Return to monitor position';
      btn.addEventListener('click', e => { e.stopPropagation(); clickPreset(btn, cam.monitor_preset, 'Monitor'); });
      bar.appendChild(btn);
    }
  }).catch(() => {});
}

