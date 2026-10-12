#!/usr/bin/env python3
# Version: 5.45
# Updated: 2026-10-12
# v1.0 - 2026-04-28 - Initial stable release: go2rtc streaming, PTZ click-to-centre,
#                     sequential pan+tilt, optical zoom, presets, serialized NVR API
# v1.1 - 2026-04-28 - Add camera management API (GET/POST/PUT/DELETE /api/cameras)
# v1.2 - 2026-04-29 - Fix stream quality: delete+re-add go2rtc stream on update so
#                     sub/main change actually takes effect
# v1.3 - 2026-04-29 - Add WebRTC signalling proxy to fix CORS (browser->go2rtc cross-origin)
# v1.4 - 2026-04-29 - Use go2rtc video-stream.js web component (MSE fallback for H.265);
#                     register {id}_main + {id}_sub streams; add stream toggle support
# v1.5 - 2026-04-29 - Add CSP middleware to allow unsafe-eval required by video-rtc.js
# v1.6 - 2026-04-29 - Fix go2rtc cross-origin WebSocket rejection (add origin: * to API config)
# v1.7 - 2026-04-29 - Add joystick PTZ: POST /ptz/{id}/start and /ptz/{id}/stop endpoints
# v1.8 - 2026-04-29 - Add ONVIF WS-Discovery: GET /api/discover scans LAN for cameras
# v1.9 - 2026-04-29 - Restart go2rtc on camera add/update/delete instead of using unreliable PUT API
# v2.0 - 2026-04-29 - Grid layouts (1x1/2x1/2x2) with pagination and per-camera fullscreen
# v2.1 - 2026-04-29 - System tray icon (no CMD window); PyInstaller path handling; build.bat
# v2.2 - 2026-04-29 - Playback: Search recordings + proxy download stream; per-camera opt-in
# v2.3 - 2026-04-29 - Multi-brand RTSP: Dahua cam/realmonitor URL format; brand selector in settings
# v2.4 - 2026-04-29 - RTSP connectivity test before saving cameras; blocks go2rtc hammering bad endpoints
# v2.5 - 2026-04-29 - Capture go2rtc output; health/restart endpoints; stream offline detection
# v2.6 - 2026-04-29 - Force RTSP over TCP (#rtsp_transport=tcp) to fix UDP RTP failure on Reolink NVRs
# v2.7 - 2026-04-30 - ONVIF PTZ for non-Reolink cameras (Dahua etc): ContinuousMove/RelativeMove/Stop/
#                     GetPresets/GotoPreset via SOAP; preset IDs unified as str across all brands
# v2.8 - 2026-04-30 - Fix ONVIF click-to-centre: Dahua ignores RelativeMove; use timed ContinuousMove+Stop
# v2.9 - 2026-04-30 - Improve ONVIF timed click accuracy: proportional speed + stop-lead latency compensation
# v3.0 - 2026-04-30 - Exact Dahua click-to-centre via CGI PositionABS (mirrors SmartPSS);
#                     reads current pan/tilt via getStatus then moves to computed absolute target
# v3.1 - 2026-04-30 - Software position tracking for NVRs where getStatus returns 400;
# v3.2 - 2026-04-30 - Suppress favicon 404; reset Dahua invert flags (wrong for PositionABS)
# v3.3 - 2026-04-30 - Fix camera PUT to merge rather than replace (preserves custom config fields)
# v3.4 - 2026-04-30 - Fix Dahua PositionABS zooming in: arg3 is zoom position not speed; omit it
# v3.5 - 2026-04-30 - PositionABS requires arg3 (400 without it); default to 0 (min zoom);
# v3.6 - 2026-04-30 - Fix click latency: skip getStatus when position already known;
# v3.7 - 2026-04-30 - Move go2rtc.exe to bin\ subdir; disable go2rtc built-in web UI
# v3.8 - 2026-04-30 - Fix frozen app: CREATE_NO_WINDOW for go2rtc; freeze_support();
# v3.9 - 2026-04-30 - Write startup_error.log on crash (no console in frozen app);
#                     WindowsSelectorEventLoopPolicy so uvicorn starts correctly
#                     cache urllib digest-auth opener per NVR (reuses nonce + TCP connection)
#                     configurable via dahua_click_zoom in config.yaml
#                     falls back to configured dahua_default_pan/tilt then timed ONVIF
# v4.0 - 2026-04-30 - Redirect all stdout/stderr to ptzlite.log at startup in frozen
#                     app so daemon thread crashes and import errors are captured
# v4.1 - 2026-04-30 - Fix frozen app server not starting: uvicorn signal-handler install
#                     fails silently in non-main thread; use uvicorn.Server directly with
#                     install_signal_handlers patched out; explicit asyncio loop per thread
# v4.2 - 2026-04-30 - Fix UnicodeEncodeError in log file (open with utf-8); replace arrow
#                     literals with ASCII in print(); guard input() in noconsole builds
# v4.3 - 2026-04-30 - Discovery: use get_running_loop(); surface errors via HTTPException;
#                     fix JS fetch to check r.ok so discovery errors show as toast
# v4.4 - 2026-04-30 - Discovery: send probe on every local interface so cameras are found
#                     even when VPN/virtual adapters cause default route to wrong interface
# v4.5 - 2026-04-30 - Enable/disable cameras: toggle on settings card; PATCH endpoint;
#                     disabled cameras hidden from viewer and excluded from go2rtc streams
# v4.6 - 2026-04-30 - Fix toggle click opening edit modal (stop label click propagation);
#                     auto-save as disabled when connection test fails so bad cameras
#                     can't break go2rtc and kill existing streams
# v4.7 - 2026-04-30 - Upgrade RTSP test to full DESCRIBE with Digest/Basic auth against
#                     the actual stream URL; detects bad channel numbers, wrong passwords
# v4.8 - 2026-04-30 - Fix RTSP test failing on working cameras: strip go2rtc #opts suffix
#                     and inline credentials from URL before sending DESCRIBE to camera
# v4.9 - 2026-04-30 - Fix RTSP test using cam port (API port 443) instead of RTSP port 554
# v5.0 - 2026-04-30 - Playback overhaul: 30s/1m/5m/10m/30m buttons; Live always visible
#                     and highlighted; errors persist until next action; backend falls back
#                     to main stream if configured stream has no recordings
# v5.1 - 2026-04-30 - Playback: block Dahua (uses wrong API) with proper error; log real
#                     stream 502 error from camera; pass brand to /cameras so frontend
#                     can suppress playback bar for unsupported brands
# v5.2 - 2026-05-16 - Fix Hub Pro playback: harden HTTP download (User-Agent, force_close,
#                     channel+1 fallback); add RTSP go2rtc playback fallback via
#                     POST /api/playback/{id}/rtsp_start and DELETE /api/playback/{id}/rtsp_stop;
#                     frontend auto-falls back to RTSP when HTTP download returns 502
# v5.4 - 2026-05-16 - Try POST+JSON body for cmd=Download (Hub Pro uses POST like every other
#                     command, not GET); restructure candidates to support GET and POST methods
# v5.5 - 2026-05-16 - Log full Search result file object (diagnose Hub Pro metadata);
#                     add cmd=GetDownload POST candidates (newer Reolink/Hub Pro firmware);
#                     fix frozen app go2rtc path (bin/ lives in _MEIPASS not next to .exe)
# v5.6 - 2026-05-16 - Fix RTSP playback stream type: use recording file's actual type (sub/main)
#                     from Search result instead of camera's configured live stream type;
#                     Hub Pro records sub-stream over wireless so h264Preview_01_sub needed
# v5.7 - 2026-05-16 - Try "name" as Download param key (Hub Pro Search returns field as "name",
#                     not "filename"; "filename" key returns null); add GetDownload-name variants;
#                     log stream type in RTSP registration line
# v5.8 - 2026-05-17 - Rewrite playback stream using reolink_aio-confirmed Hub Pro API:
# v5.9 - 2026-05-17 - Fix seek: pass seek_offset_s as &seek=N to FLV URL so Hub Pro
#                     starts stream at the requested time offset within the recording file
# v5.10 - 2026-05-17 - FLV timestamp seek: pass target wall-clock time as start= instead
# v5.11 - 2026-05-17 - cmd=Download&source=&start=YYYYMMDDHHMMSS as primary (confirmed working)
# v5.12 - 2026-05-17 - Fix target time: start param is already target_epoch, don't add seek again
# v5.13 - 2026-05-17 - RTSP-first: try RTSP ?starttime= before HTTP download (RTSP is the only
#                      mechanism that supports time-based seeking on Reolink Hub Pro);
#                      pass target_epoch directly to rtsp_start so RTSP URL = exact target time
#                      remove FLV-timestamp (poisons subsequent connections); FLV kept as
#                      fallback; restore video.currentTime for video/mp4 Range seeking
#                      of filename+seek; Hub Pro resolves the exact position natively;
#                      return target_epoch from search; filename FLV kept as fallback
#                     (1) FLV /flv?app=bcs&stream=playback.bcs with user/password auth;
#                     (2) NvrDownload (POST) -> Download (GET source=) two-step NVR flow;
# v5.14 - 2026-05-17 - Revert RTSP-first (Hub Pro registers stream but sends no video);
#                      restore HTTP-download-first with video.currentTime = seekS seeking;
# v5.15 - 2026-05-18 - Fix Range seeking: Hub Pro ignores Range headers and always returns
#                      HTTP 200; intercept browser Range requests and implement byte-skipping
#                      in the proxy so we return proper HTTP 206 -- enables video.currentTime
# v5.16 - 2026-05-18 - Fix Range probe: Chrome sends Range: bytes=0- first to test Range
#                      support; v5.15 had range_start>0 check so 0- got 200 back, Chrome
#                      gave up on Range entirely; now ANY Range header returns 206
# v5.17 - 2026-05-18 - Log raw Search API response on failure to diagnose Hub Pro errors
# v5.18 - 2026-05-18 - Pass file_size from Search result to stream endpoint; use it as
#                      Content-Length fallback when Hub Pro omits it on Download response,
#                      ensuring 206 Content-Range header is always populated for seeking
#                     (3) standard GET fallbacks; pass stream_type from search to stream URL
# v5.19 - 2026-05-18 - Replace byte-skip with temp-file tee cache (superseded by v5.20)
# v5.20 - 2026-05-18 - Fix black-screen buffering: Hub Pro MP4s have moov atom at end of file;
#                      download full recording to temp file before serving; use FileResponse
#                      so Chrome can instantly Range-seek to moov and play position from disk
# v5.21 - 2026-05-18 - Fix silent wait: background prefetch with progress polling; frontend
#                      shows "45%..." in button while downloading, then loads video from cache
# v5.22 - 2026-05-18 - Replace full-file download with FLV streaming: proxy Hub Pro FLV
#                      endpoint with seek=N seconds; flv.js plays via MSE instantly with no
#                      pre-download; falls back to RTSP go2rtc on FLV error
# v5.23 - 2026-05-18 - Fix FLV 502: Hub Pro serves FLV over HTTP:80, not HTTPS:443;
#                      try configured port first then fallback to http:80; log full Hub Pro
#                      rejection response to diagnose remaining failures
# v5.24 - 2026-05-18 - Try RTMP rtmp://ip:1935/bcs/playback.bcs with filename+seek before
#                      RTSP ?starttime= in rtsp_start; Hub Pro exposes native RTMP on 1935;
#                      pass filename+seekS from frontend so RTMP URL has correct recording ref
# v5.32 - 2026-05-18 - Named camera pages, drag-to-reorder, import/export; fullscreen grid, snapshot, labels toggle, audio toggle
# v5.31 - 2026-05-18 - Pre-warm all RTSP sessions at startup: persistent MP4 loopback connections
#                      keep go2rtc pulling RTSP for every camera so WebRTC clients get
#                      immediate video with no cold-start buffer delay
# v5.30 - 2026-05-18 - Disable STUN in go2rtc WebRTC config (LAN-only; eliminates ICE overhead)
# v5.29 - 2026-05-18 - Revert v5.27/5.28 PTZ changes: restore linear ONVIF speed curve,
#                      fixed Reolink speed, remove AbsoluteMove attempt; restore Dahua 5s timeout
# v5.28 - 2026-05-18 - Fix click latency: use tracked position first (GetStatus only on first click);
#                      reduce ONVIF GetStatus timeout 8s->3s; Dahua CGI timeout 5s->2s
# v5.27 - 2026-05-18 - PTZ accuracy: ONVIF AbsoluteMove with GetStatus position tracking;
#                      quadratic speed curve for timed fallback; Reolink speed scaling
# v5.26 - 2026-05-18 - Remove all recording playback code (Hub Pro API incompatible);
#                      live streaming, PTZ, camera management, settings unchanged
# v5.25 - 2026-05-18 - Progressive stream: Hub Pro MP4s have moov at byte 36 (faststart);
#                      /stream now starts a background download and immediately returns a
#                      streaming response from byte 0 so the browser can play within seconds;
#                      Range requests on the in-progress temp file are served as bytes arrive,
#                      enabling the browser to auto-seek to the target time once downloaded;
#                      JS auto-seeks when estimated target byte is available
# v5.3 - 2026-05-16 - Expand search window from 30min to 2h (fixes 1-hour recording blocks);
#                     return end_epoch from search; try time-based Download URL as extra
#                     candidate (&starttime=&endtime= instead of &filename=, Hub Pro variant);
#                     add GET /api/log endpoint; add View Log link in settings
# v5.33 - 2026-05-18 - Modularize into core/services/api packages; Pydantic config validation; structured logging
# v5.34 - 2026-05-19 - Password protection: HMAC session cookie middleware; login/logout/status API; disable when password empty
# v5.35 - 2026-05-20 - Fix 18s startup delay: _wait_for_server was polling /cameras (401 with auth = timeout loop)
# v5.36 - 2026-05-20 - Fix DeprecationWarning: replace WindowsSelectorEventLoopPolicy with direct SelectorEventLoop construction
# v5.37 - 2026-05-20 - Ctrl+Alt+Z global hotkey to open viewer from system tray
# v5.38 - 2026-05-20 - Hotkey: PeekMessage poll replaces blocking GetMessage; MOD_NOREPEAT added;
#                      use_last_error for diagnostics. Tray icon loads main-logo.png
# v5.39 - 2026-05-21 - App window mode: launch Chrome/Edge with --app flag for a browser-chrome-free
#                      window with its own taskbar icon; falls back to default browser if unavailable
# v5.40 - 2026-10-11 - Auth middleware: plain 401 for all non-page requests, redirect only page loads;
#                      startup pre-warm moved from deprecated on_event to lifespan
# v5.41 - 2026-10-11 - Register detection SSE router; close shared camera HTTP session on shutdown
# v5.42 - 2026-10-11 - Readable JSON errors for camera login failures, timeouts, unreachable cameras
# v5.43 - 2026-10-11 - Cache-Control: no-cache for html/js/css (viewer split into static/js + css)
# v5.44 - 2026-10-11 - Login redirect carries ?next= so you return to the page you asked for
# v5.45 - 2026-10-12 - Setup notes point at README; app version now lives only in core/paths.py
"""
Reolink camera viewer with click-to-pan PTZ control.

Uses go2rtc to serve WebRTC streams directly from RTSP -- no ffmpeg per camera.

Setup (full instructions in README.md):
    1. Download go2rtc for Windows from https://github.com/AlexxIT/go2rtc/releases
       and place go2rtc.exe next to this file (or in bin\\).
    2. pip install -r requirements.txt
    3. copy config.example.yaml config.yaml, or add cameras in Settings.
    4. python ptzlite.py  ->  http://localhost:8765

go2rtc will be started automatically and stopped when the app exits.
"""

import asyncio
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path
from urllib.parse import quote

import aiohttp
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from core.log import setup_logging
from core.paths import APP_VERSION, GO2RTC_PORT, _STATIC_DIR, _USER_DIR
from services.browser import open_viewer
from services.go2rtc import _launch_prewarm, start_go2rtc, stop_go2rtc
from services.http import close_session
from services.ptz.reolink import ReolinkLoginError

# Import routers
from api.auth import router as auth_router, is_authenticated
from api.cameras import router as cameras_router
from api.detect import router as detect_router
from api.go2rtc_api import router as go2rtc_router
from api.misc import router as misc_router
from api.ptz import router as ptz_router

# ── FastAPI app ────────────────────────────────────────────────────────────────

from contextlib import asynccontextmanager


@asynccontextmanager
async def _lifespan(_app):
    # Keep go2rtc RTSP sessions warm so streams open instantly.
    asyncio.create_task(_launch_prewarm(delay=3.0))
    yield
    await close_session()   # shared camera HTTP pool


app = FastAPI(title="PTZLite", lifespan=_lifespan)

_AUTH_EXEMPT = {
    "/login.html", "/api/login", "/api/logout", "/api/auth/status",
    "/favicon.ico", "/main-logo.png", "/manifest.json",   # assets the login page needs
}


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    # Deny by default. Page loads are redirected to the login page; everything
    # else (API calls, assets, any future endpoint) gets a plain 401.
    # NB: HTTP middleware does not see WebSockets — /go2rtc/ws checks auth itself.
    p = request.url.path
    if p not in _AUTH_EXEMPT and not is_authenticated(request):
        if p == "/" or p.endswith(".html"):
            target = p + (f"?{request.url.query}" if request.url.query else "")
            return RedirectResponse("/login.html?next=" + quote(target, safe=""))
        return JSONResponse({"detail": "Not authenticated"}, status_code=401)
    response = await call_next(request)
    # Revalidate app code on every load (cheap ETag check) so an update is never
    # run against stale cached scripts/styles.
    if p == "/" or p.endswith((".html", ".js", ".css")):
        response.headers["Cache-Control"] = "no-cache"
    return response


# Camera failures surface in the UI as readable messages instead of a bare 500.
@app.exception_handler(ReolinkLoginError)
async def _camera_login_error(_req: Request, exc: ReolinkLoginError):
    return JSONResponse({"detail": "camera login failed - check its username/password"},
                        status_code=502)


@app.exception_handler(asyncio.TimeoutError)
async def _camera_timeout(_req: Request, _exc: Exception):
    return JSONResponse({"detail": "the camera didn't respond in time"}, status_code=504)


@app.exception_handler(aiohttp.ClientError)
async def _camera_unreachable(_req: Request, exc: Exception):
    return JSONResponse({"detail": "couldn't reach the camera"}, status_code=502)


app.include_router(auth_router)
app.include_router(ptz_router)
app.include_router(cameras_router)
app.include_router(detect_router)
app.include_router(go2rtc_router)
app.include_router(misc_router)

app.mount("/", StaticFiles(directory=_STATIC_DIR, html=True), name="static")


# ── System tray ────────────────────────────────────────────────────────────────

def _make_tray_icon():
    from PIL import Image, ImageDraw
    logo_path = _STATIC_DIR / "main-logo.png"
    try:
        img = Image.open(logo_path).convert("RGBA")
        img = img.resize((64, 64), Image.LANCZOS)
        return img
    except Exception:
        pass
    # Fallback: draw a simple camera icon
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rectangle([4, 16, 60, 52], fill="#1e40af")        # camera body
    d.rectangle([44, 8,  58, 18], fill="#1e40af")        # viewfinder bump
    d.ellipse(  [16, 20, 48, 48], fill="#0f172a")        # lens outer
    d.ellipse(  [22, 26, 42, 42], fill="#93c5fd")        # lens glass
    d.ellipse(  [28, 32, 36, 36], fill="#0f172a")        # lens pupil
    return img



def _wait_for_server(port: int, timeout: float = 15.0) -> bool:
    # Poll an auth-exempt endpoint — /cameras returns 401 when password is set,
    # which urlopen raises as HTTPError (caught as Exception), causing the old
    # code to wait the full 15-second timeout before opening the browser.
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(f"http://localhost:{port}/api/auth/status", timeout=1)
            return True
        except Exception:
            time.sleep(0.25)
    return False


def _run_uvicorn(port: int):
    # uvicorn.run() tries to install OS signal handlers -- only allowed from the main
    # thread.  Running it in a daemon thread (as we do for the tray build) causes a
    # silent ValueError and the server never binds.  Use uvicorn.Server directly and
    # patch out install_signal_handlers to avoid this.
    # Also force SelectorEventLoop: ProactorEventLoop (Python 3.12+ default on Windows)
    # breaks asyncio inside frozen apps.  Create it directly rather than via the
    # deprecated set_event_loop_policy / WindowsSelectorEventLoopPolicy APIs.
    import logging
    logger = logging.getLogger("PTZLite.uvicorn")

    logger.info("[uvicorn] creating config on port %d", port)
    config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="info")
    server = uvicorn.Server(config)
    # Disable signal-handler installation -- not permitted outside the main thread
    server.install_signal_handlers = lambda: None  # type: ignore[method-assign]

    logger.info("[uvicorn] starting server loop")
    try:
        loop = asyncio.SelectorEventLoop() if sys.platform == "win32" else asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(server.serve())
    except Exception:
        import traceback
        traceback.print_exc()  # goes to ptzlite.log via logging
        raise
    finally:
        loop.close()


def _start_hotkey_listener(port: int) -> None:
    """Register Ctrl+Alt+Z as a global hotkey to open the viewer (Windows only).
    Uses PeekMessage polling so the loop never blocks indefinitely."""
    if sys.platform != "win32":
        return

    import ctypes
    import ctypes.wintypes as wt
    import logging
    _hk_log = logging.getLogger("PTZLite.hotkey")

    WM_HOTKEY    = 0x0312
    MOD_CONTROL  = 0x0002
    MOD_ALT      = 0x0001
    MOD_NOREPEAT = 0x4000
    VK_Z         = 0x5A
    HOTKEY_ID    = 1
    PM_REMOVE    = 0x0001

    def _listen() -> None:
        # Private DLL handle — never touches ctypes.windll.user32's shared
        # function objects which pystray owns from the main thread.
        u32 = ctypes.WinDLL("user32", use_last_error=True)
        u32.RegisterHotKey.argtypes   = [wt.HWND, ctypes.c_int, ctypes.c_uint, ctypes.c_uint]
        u32.RegisterHotKey.restype    = wt.BOOL
        u32.UnregisterHotKey.argtypes = [wt.HWND, ctypes.c_int]
        u32.UnregisterHotKey.restype  = wt.BOOL
        u32.PeekMessageW.argtypes     = [ctypes.POINTER(wt.MSG), wt.HWND,
                                         ctypes.c_uint, ctypes.c_uint, ctypes.c_uint]
        u32.PeekMessageW.restype      = wt.BOOL

        if not u32.RegisterHotKey(None, HOTKEY_ID, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, VK_Z):
            err = ctypes.get_last_error()
            _hk_log.warning(
                "Could not register Ctrl+Alt+Z hotkey (error %d — already claimed by another app?)", err
            )
            return
        _hk_log.info("Ctrl+Alt+Z hotkey registered — press to open viewer")
        msg = wt.MSG()
        try:
            while True:
                if u32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
                    if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID:
                        _hk_log.info("Ctrl+Alt+Z pressed — opening viewer")
                        open_viewer(port)
                else:
                    time.sleep(0.05)
        except Exception as exc:
            _hk_log.error("Hotkey listener error: %s", exc)
        finally:
            u32.UnregisterHotKey(None, HOTKEY_ID)
            _hk_log.info("Ctrl+Alt+Z hotkey unregistered")

    threading.Thread(target=_listen, daemon=True, name="hotkey-listener").start()


def _main_tray(port: int):
    import pystray

    server = threading.Thread(target=_run_uvicorn, args=(port,), daemon=True)
    server.start()
    _wait_for_server(port)
    _start_hotkey_listener(port)
    open_viewer(port)

    icon_img = _make_tray_icon()

    def on_open(_icon, _item):
        open_viewer(port)

    def on_settings(_icon, _item):
        webbrowser.open(f"http://localhost:{port}/settings.html")

    def on_exit(icon, _item):
        stop_go2rtc()
        icon.stop()

    tray = pystray.Icon(
        "PTZLite",
        icon_img,
        "PTZLite",
        menu=pystray.Menu(
            pystray.MenuItem("Open Viewer",  on_open,     default=True),
            pystray.MenuItem("Settings",     on_settings),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Exit",         on_exit),
        ),
    )
    tray.run()  # blocks until on_exit calls icon.stop()


# ── Entry point ────────────────────────────────────────────────────────────────

def main():
    port = 8765

    # In a frozen (--noconsole) build there is no stdout/stderr at all -- unhandled
    # exceptions in daemon threads are silently swallowed.  Redirect everything to
    # a log file first so import errors, uvicorn crashes, etc. are always captured.
    if getattr(sys, "frozen", False):
        log_path = _USER_DIR / "ptzlite.log"
        # utf-8 + errors=replace so Unicode chars (arrows etc.) never crash the logger
        _log_file = open(log_path, "w", buffering=1, encoding="utf-8", errors="replace")
        sys.stdout = _log_file
        sys.stderr = _log_file

    # Set up structured logging — must be first before any module logs
    setup_logging()

    import logging
    logger = logging.getLogger("PTZLite.main")
    logger.info("PTZLite %s starting", APP_VERSION)

    for i, arg in enumerate(sys.argv[1:]):
        if arg in ("--port", "-p") and i + 2 < len(sys.argv):
            port = int(sys.argv[i + 2])

    try:
        start_go2rtc()
        logger.info("Reolink viewer -> http://localhost:%d", port)
        try:
            import pystray   # noqa: F401
            from PIL import Image  # noqa: F401
            _main_tray(port)
        except ImportError:
            # pystray / Pillow not installed -- fall back to visible console
            uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
    except Exception as e:
        import traceback
        log_path = _USER_DIR / "startup_error.log"
        with open(log_path, "w") as f:
            traceback.print_exc(file=f)
            f.write(f"\nERROR: {e}\n")
        logger.error("ERROR: %s\nDetails written to %s", e, log_path)
        if not getattr(sys, "frozen", False):
            input("\nPress Enter to close...")


if __name__ == "__main__":
    # Required for PyInstaller frozen apps on Windows: prevents worker re-spawn loop
    import multiprocessing
    multiprocessing.freeze_support()
    try:
        main()
    except Exception as e:
        import logging
        import traceback
        logging.getLogger("PTZLite").error("Unhandled exception: %s", e)
        traceback.print_exc()
        if not getattr(sys, "frozen", False):
            input("\nPress Enter to close...")
