#!/usr/bin/env python3
# Version: 3.5
# Updated: 2026-04-30
# v1.0 - 2026-04-28 - Initial stable release: go2rtc streaming, PTZ click-to-centre,
#                     sequential pan+tilt, optical zoom, presets, serialized NVR API
# v1.1 - 2026-04-28 - Add camera management API (GET/POST/PUT/DELETE /api/cameras)
# v1.2 - 2026-04-29 - Fix stream quality: delete+re-add go2rtc stream on update so
#                     sub/main change actually takes effect
# v1.3 - 2026-04-29 - Add WebRTC signalling proxy to fix CORS (browser→go2rtc cross-origin)
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
#                     configurable via dahua_click_zoom in config.yaml
#                     falls back to configured dahua_default_pan/tilt then timed ONVIF
"""
Reolink camera viewer with click-to-pan PTZ control.

Uses go2rtc to serve WebRTC streams directly from RTSP — no ffmpeg per camera.

Setup:
    1. Download go2rtc for Windows from https://github.com/AlexxIT/go2rtc/releases
       and place go2rtc.exe next to this file.
    2. pip install -r requirements.txt
    3. Edit config.yaml with your camera details.
    4. python app.py  →  http://localhost:8765

go2rtc will be started automatically and stopped when the app exits.
"""

import asyncio
import atexit
import base64
import collections
import datetime
import hashlib
import math
import os
import re
import socket
import subprocess
import sys
import threading
import time
import ssl
import urllib.error
import urllib.parse
import urllib.request
import uuid
import webbrowser
import xml.etree.ElementTree as ET
from pathlib import Path

import aiohttp
import uvicorn
import yaml
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# When frozen by PyInstaller: user files (config, go2rtc) live next to the .exe;
# bundled resources (static/) are extracted to sys._MEIPASS at runtime.
if getattr(sys, 'frozen', False):
    _USER_DIR   = Path(sys.executable).parent
    _BUNDLE_DIR = Path(sys._MEIPASS)        # type: ignore[attr-defined]
else:
    _USER_DIR   = Path(__file__).parent
    _BUNDLE_DIR = Path(__file__).parent

BASE        = _USER_DIR
CONFIG_PATH = BASE / "config.yaml"
GO2RTC_BIN  = BASE / "go2rtc.exe"
GO2RTC_PORT = 1984
_STATIC_DIR = _BUNDLE_DIR / "static"

app = FastAPI(title="Reolink Viewer")

# ── Config ─────────────────────────────────────────────────────────────────────

def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)

_config = load_config()
cameras: dict[str, dict] = {c["id"]: c for c in _config["cameras"]}

# ── go2rtc lifecycle ───────────────────────────────────────────────────────────

_go2rtc_proc: subprocess.Popen | None = None
_go2rtc_log: collections.deque = collections.deque(maxlen=150)


def _drain_go2rtc_output(proc: subprocess.Popen):
    """Reads go2rtc stdout+stderr into _go2rtc_log; exits when the process closes its pipe."""
    try:
        for raw in proc.stdout:  # type: ignore[union-attr]
            _go2rtc_log.append(raw.decode("utf-8", errors="replace").rstrip())
    except Exception:
        pass


def _rtsp_url(cam: dict, stream: str | None = None) -> str:
    ch = cam.get("channel", 0)
    s = stream if stream is not None else cam.get("stream", "main")
    brand = cam.get("brand", "reolink").lower()
    # #backchannel=0 disables two-way audio; #rtsp_transport=tcp forces TCP for RTP data
    # (go2rtc defaults to UDP which many Reolink/Dahua NVRs silently drop, causing CONN-state loops)
    _opts = "#backchannel=0#rtsp_transport=tcp"
    if brand == "dahua":
        subtype = 0 if s == "main" else 1
        return (
            f"rtsp://{cam['username']}:{cam['password']}"
            f"@{cam['ip']}:554/cam/realmonitor?channel={ch}&subtype={subtype}{_opts}"
        )
    # Reolink: channel is 0-indexed in config, 1-indexed in URL
    return (
        f"rtsp://{cam['username']}:{cam['password']}"
        f"@{cam['ip']}:554/h264Preview_{ch + 1:02d}_{s}{_opts}"
    )


def _copy_go2rtc_js():
    """Download video-stream.js from go2rtc into static/ so we can serve it same-origin."""
    static = _STATIC_DIR
    for fname in ("video-stream.js", "video-rtc.js"):
        try:
            url = f"http://localhost:{GO2RTC_PORT}/{fname}"
            with urllib.request.urlopen(url, timeout=3) as r:
                (static / fname).write_bytes(r.read())
            print(f"Copied {fname} from go2rtc to static/")
        except Exception as e:
            print(f"WARNING: Could not copy {fname} from go2rtc: {e}")


def start_go2rtc():
    global _go2rtc_proc

    if not GO2RTC_BIN.exists():
        print(
            f"\n  ERROR: go2rtc.exe not found at {GO2RTC_BIN}\n"
            "  Download from https://github.com/AlexxIT/go2rtc/releases\n"
            "  and place go2rtc.exe next to app.py\n"
        )
        sys.exit(1)

    # Register both _main and _sub streams for every camera so the UI can toggle
    streams = {}
    for cam in _config["cameras"]:
        streams[f"{cam['id']}_main"] = _rtsp_url(cam, "main")
        streams[f"{cam['id']}_sub"]  = _rtsp_url(cam, "sub")

    go2rtc_cfg = {
        "streams": streams,
        "api": {"listen": f":{GO2RTC_PORT}", "origin": "*"},
        "log": {"level": "warn"},
        "rtsp": {"listen": ""},
    }

    cfg_file = BASE / "go2rtc_generated.yaml"
    with open(cfg_file, "w") as f:
        yaml.dump(go2rtc_cfg, f)

    _go2rtc_proc = subprocess.Popen(
        [str(GO2RTC_BIN), "-config", str(cfg_file)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    threading.Thread(target=_drain_go2rtc_output, args=(_go2rtc_proc,), daemon=True).start()
    atexit.register(stop_go2rtc)

    for _ in range(20):
        try:
            urllib.request.urlopen(f"http://localhost:{GO2RTC_PORT}/api/streams", timeout=1)
            break
        except Exception:
            time.sleep(0.3)
    else:
        print("WARNING: go2rtc did not start in time — streams may not work immediately")

    _copy_go2rtc_js()


def stop_go2rtc():
    if _go2rtc_proc and _go2rtc_proc.poll() is None:
        _go2rtc_proc.terminate()
        try:
            _go2rtc_proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            _go2rtc_proc.kill()

# ── PTZ target resolution ──────────────────────────────────────────────────────

def _ptz_cam(cam: dict) -> dict:
    """If ptz_ip is set, route PTZ commands directly to the camera instead of the NVR."""
    if "ptz_ip" not in cam:
        return cam
    return {
        **cam,
        "id":      f"{cam['id']}__ptz",
        "ip":      cam["ptz_ip"],
        "port":    cam.get("ptz_port", 80),
        "channel": 0,
    }

# ── Reolink authentication + serialized NVR API ────────────────────────────────
#
# This NVR kills the session if it receives concurrent API requests from the same
# login session. _nvr_call serializes all calls per NVR identity (one at a time),
# handling login automatically. The lock is held only for the duration of the HTTP
# request itself — sleeps between PTZ command and Stop happen outside the lock.

_tokens: dict[str, tuple[str, float]] = {}
_nvr_locks: dict[str, asyncio.Lock] = {}


def _nvr_key(cam: dict) -> str:
    return f"{cam['ip']}:{cam.get('port', 80)}:{cam['username']}"


def _get_nvr_lock(cam: dict) -> asyncio.Lock:
    key = _nvr_key(cam)
    if key not in _nvr_locks:
        _nvr_locks[key] = asyncio.Lock()
    return _nvr_locks[key]


def _api_url(cam: dict, cmd: str, token: str) -> str:
    port = cam.get("port", 80)
    scheme = "https" if port == 443 or cam.get("https") else "http"
    return f"{scheme}://{cam['ip']}:{port}/api.cgi?cmd={cmd}&token={token}"


async def _post(url: str, payload: list, timeout: int = 8) -> list:
    async with aiohttp.ClientSession() as session:
        async with session.post(
            url, json=payload, ssl=False,
            timeout=aiohttp.ClientTimeout(total=timeout)
        ) as resp:
            return await resp.json(content_type=None)


async def _nvr_call(cam: dict, cmd: str, payload: list, timeout: int = 8) -> list:
    """Serialized NVR API call — one request at a time per NVR, auto-login if needed."""
    key = _nvr_key(cam)
    lock = _get_nvr_lock(cam)
    async with lock:
        token, expires = _tokens.get(key, ("", 0.0))
        if not token or time.time() >= expires - 60:
            port = cam.get("port", 80)
            scheme = "https" if port == 443 or cam.get("https") else "http"
            login_url = f"{scheme}://{cam['ip']}:{port}/api.cgi?cmd=Login"
            data = await _post(login_url, [{"cmd": "Login", "action": 0, "param": {
                "User": {"userName": cam["username"], "password": cam["password"]}
            }}])
            token = data[0]["value"]["Token"]["name"]
            lease = data[0]["value"]["Token"]["leaseTime"]
            _tokens[key] = (token, time.time() + lease)
            print(f"Login {key}: token cached (lease={lease}s)")
        return await _post(_api_url(cam, cmd, token), payload, timeout)

# ── PTZ control ────────────────────────────────────────────────────────────────

_DIR = {
    ( 1,  0): "Right",   (-1,  0): "Left",
    ( 0,  1): "Up",      ( 0, -1): "Down",
    ( 1,  1): "RightUp", ( 1, -1): "RightDown",
    (-1,  1): "LeftUp",  (-1, -1): "LeftDown",
}


class ClickPayload(BaseModel):
    x: float  # 0.0 = left, 1.0 = right
    y: float  # 0.0 = top,  1.0 = bottom


async def _ptz_stop(cam: dict):
    await _nvr_call(cam, "PtzCtrl", [{"cmd": "PtzCtrl", "action": 0, "param": {
        "channel": cam.get("channel", 0), "op": "Stop", "speed": 1, "timeout": 0,
    }}])

# ── ONVIF PTZ (Dahua / Hikvision / generic ONVIF cameras) ─────────────────────
# ── Dahua CGI API (HTTP Digest auth) ─────────────────────────────────────────
#
# SmartPSS Lite achieves exact click-to-centre via Dahua's proprietary CGI:
#   GET /cgi-bin/ptz.cgi?action=getStatus&channel=N  → current pan/tilt/zoom
#   GET /cgi-bin/ptz.cgi?action=start&channel=N&code=PositionABS&arg1=PAN&arg2=TILT&arg3=SPEED
#
# This gives exact positioning without depending on move-duration timing.
# Falls back to timed ONVIF ContinuousMove if the NVR doesn't support it.
#
# NVR position readback limitation: many Dahua NVRs (including DHI-NVR54xx series) return
# HTTP 400 for getStatus on IP camera channels — the NVR doesn't aggregate camera position.
# Workaround: software-track position after every successful PositionABS command.
# Seed with dahua_default_pan / dahua_default_tilt in config.yaml if known.

# cam_id → (pan_deg, tilt_deg) after last successful PositionABS; None = unknown
_dahua_tracked_pos: dict[str, tuple[float, float]] = {}

_dahua_ssl_ctx = ssl.create_default_context()
_dahua_ssl_ctx.check_hostname = False
_dahua_ssl_ctx.verify_mode = ssl.CERT_NONE


def _dahua_cgi_sync(cam: dict, params: dict) -> tuple[int, str]:
    """Synchronous Dahua CGI call with HTTP Digest auth. Run in executor."""
    port = cam.get("port", 80)
    scheme = "https" if port == 443 or cam.get("https") else "http"
    base_url = f"{scheme}://{cam['ip']}:{port}/cgi-bin/ptz.cgi"
    full_url  = base_url + "?" + urllib.parse.urlencode(params)

    mgr = urllib.request.HTTPPasswordMgrWithDefaultRealm()
    mgr.add_password(None, base_url, cam["username"], cam["password"])
    digest_handler = urllib.request.HTTPDigestAuthHandler(mgr)
    https_handler  = urllib.request.HTTPSHandler(context=_dahua_ssl_ctx)
    opener = urllib.request.build_opener(digest_handler, https_handler)
    try:
        with opener.open(full_url, timeout=5) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception as e:
        print(f"Dahua CGI error: {e}")
        return 0, ""


async def _dahua_cgi(cam: dict, params: dict) -> tuple[int, str]:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _dahua_cgi_sync, cam, params)


async def _dahua_get_position(cam: dict) -> tuple[float, float] | None:
    """Returns (pan_deg, tilt_deg) current camera position, or None if unknown.

    Priority:
      1. CGI getStatus (live readback — works on some firmware)
      2. Software-tracked position (set after each successful PositionABS)
      3. dahua_default_pan / dahua_default_tilt from config (user-supplied seed)
    """
    cam_id  = cam["id"]
    channel = cam.get("channel", 1)

    # 1. Try live CGI readback
    try:
        status, text = await _dahua_cgi(cam, {"action": "getStatus", "channel": channel})
        if status == 200:
            pan = tilt = None
            for line in text.splitlines():
                key, _, val = line.strip().partition("=")
                k = key.strip().lower()
                if k.endswith(".pan") and pan is None:
                    try: pan = float(val.strip())
                    except ValueError: pass
                elif k.endswith(".tilt") and tilt is None:
                    try: tilt = float(val.strip())
                    except ValueError: pass
            if pan is not None and tilt is not None:
                _dahua_tracked_pos[cam_id] = (pan, tilt)
                return pan, tilt
            print(f"Dahua getStatus: could not parse pan/tilt from: {text!r}")
        else:
            print(f"Dahua getStatus HTTP {status}")
    except Exception as e:
        print(f"Dahua getStatus error: {e}")

    # 2. Software-tracked position (updated after every successful PositionABS)
    tracked = _dahua_tracked_pos.get(cam_id)
    if tracked is not None:
        print(f"Dahua {cam_id}: using tracked position ({tracked[0]:.1f}°, {tracked[1]:.1f}°)")
        return tracked

    # 3. User-configured default (seed position for first click after app start)
    dp = cam.get("dahua_default_pan")
    dt = cam.get("dahua_default_tilt")
    if dp is not None and dt is not None:
        pos = (float(dp), float(dt))
        print(f"Dahua {cam_id}: using configured default position ({pos[0]:.1f}°, {pos[1]:.1f}°)")
        return pos

    return None


async def _dahua_position_abs(cam: dict, pan: float, tilt: float) -> bool:
    # arg1=pan, arg2=tilt, arg3=zoom position (NOT speed — omitting arg3 returns 400).
    # Default zoom=0 (minimum / 1x wide). Override with dahua_click_zoom in config.yaml
    # if your lens needs a different rest zoom level (e.g. dahua_click_zoom: 10).
    channel   = cam.get("channel", 1)
    zoom_arg  = cam.get("dahua_click_zoom", 0)
    status, _ = await _dahua_cgi(cam, {
        "action": "start",
        "channel": channel,
        "code": "PositionABS",
        "arg1": f"{pan:.2f}",
        "arg2": f"{tilt:.2f}",
        "arg3": zoom_arg,
    })
    print(f"Dahua PositionABS → HTTP {status}, target pan={pan:.2f}° tilt={tilt:.2f}°")
    if status == 200:
        # Camera is now at this position — cache it for the next click
        _dahua_tracked_pos[cam["id"]] = (pan, tilt)
    return status == 200


#
# Reolink uses a proprietary HTTP API; everything else uses ONVIF SOAP.
# No extra dependencies — requests are built manually with aiohttp.

_ONVIF_SOAP_ATTRS = (
    'xmlns:s="http://www.w3.org/2003/05/soap-envelope"'
    ' xmlns:tptz="http://www.onvif.org/ver20/ptz/wsdl"'
    ' xmlns:tt="http://www.onvif.org/ver10/schema"'
)


def _is_onvif(cam: dict) -> bool:
    return cam.get("brand", "reolink").lower() != "reolink"


def _onvif_profile(cam: dict) -> str:
    # Dahua NVRs: "MediaProfile00000" = channel 1 main, "MediaProfile00008" = channel 2, etc.
    # Set onvif_profile in config.yaml to override.
    return cam.get("onvif_profile", "MediaProfile00000")


def _onvif_ptz_path(cam: dict) -> str:
    # Dahua uses /onvif/ptz_service; override with onvif_ptz_path in config.yaml for other brands.
    return cam.get("onvif_ptz_path", "/onvif/ptz_service")


def _onvif_envelope(cam: dict, body: str) -> str:
    # WS-Security PasswordDigest: Base64(SHA-1(nonce_bytes + created_str + password))
    # Required by Dahua PTZ service; PasswordText is rejected with NotAuthorized.
    nonce_raw = os.urandom(16)
    nonce_b64 = base64.b64encode(nonce_raw).decode()
    created = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    digest = base64.b64encode(
        hashlib.sha1(nonce_raw + created.encode() + cam["password"].encode()).digest()
    ).decode()
    wsse = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"
    wsu  = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd"
    pw_type = f"{wsse}#PasswordDigest"
    nc_type = f"{wsse}#Base64Binary"
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<s:Envelope {_ONVIF_SOAP_ATTRS}>'
        '<s:Header>'
        f'<wsse:Security xmlns:wsse="{wsse}" xmlns:wsu="{wsu}">'
        '<wsse:UsernameToken>'
        f'<wsse:Username>{cam["username"]}</wsse:Username>'
        f'<wsse:Password Type="{pw_type}">{digest}</wsse:Password>'
        f'<wsse:Nonce EncodingType="{nc_type}">{nonce_b64}</wsse:Nonce>'
        f'<wsu:Created>{created}</wsu:Created>'
        '</wsse:UsernameToken>'
        '</wsse:Security>'
        '</s:Header>'
        f'<s:Body>{body}</s:Body>'
        '</s:Envelope>'
    )


async def _onvif_post(cam: dict, action: str, body: str) -> str:
    port = cam.get("port", 80)
    scheme = "https" if port == 443 or cam.get("https") else "http"
    path = _onvif_ptz_path(cam)
    url = f"{scheme}://{cam['ip']}:{port}{path}"
    envelope = _onvif_envelope(cam, body).encode("utf-8")
    headers = {
        "Content-Type": "application/soap+xml; charset=utf-8",
        "SOAPAction": f'"http://www.onvif.org/ver20/ptz/wsdl/{action}"',
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(
            url, data=envelope, headers=headers, ssl=False,
            timeout=aiohttp.ClientTimeout(total=8),
        ) as resp:
            return await resp.text()


async def _onvif_continuous(cam: dict, pan: float = 0.0, tilt: float = 0.0, zoom: float = 0.0):
    pt = f'<tt:PanTilt x="{pan:.3f}" y="{tilt:.3f}"/>' if pan or tilt else ""
    z  = f'<tt:Zoom x="{zoom:.3f}"/>' if zoom else ""
    body = (
        f"<tptz:ContinuousMove>"
        f"<tptz:ProfileToken>{_onvif_profile(cam)}</tptz:ProfileToken>"
        f"<tptz:Velocity>{pt}{z}</tptz:Velocity>"
        f"</tptz:ContinuousMove>"
    )
    await _onvif_post(cam, "ContinuousMove", body)


async def _onvif_relative(cam: dict, pan: float, tilt: float):
    body = (
        f"<tptz:RelativeMove>"
        f"<tptz:ProfileToken>{_onvif_profile(cam)}</tptz:ProfileToken>"
        f"<tptz:Translation><tt:PanTilt x=\"{pan:.3f}\" y=\"{tilt:.3f}\"/></tptz:Translation>"
        f"</tptz:RelativeMove>"
    )
    await _onvif_post(cam, "RelativeMove", body)


async def _onvif_stop(cam: dict):
    body = (
        f"<tptz:Stop>"
        f"<tptz:ProfileToken>{_onvif_profile(cam)}</tptz:ProfileToken>"
        f"<tptz:PanTilt>true</tptz:PanTilt>"
        f"<tptz:Zoom>true</tptz:Zoom>"
        f"</tptz:Stop>"
    )
    await _onvif_post(cam, "Stop", body)


async def _onvif_get_presets(cam: dict) -> list[dict]:
    body = (
        f"<tptz:GetPresets>"
        f"<tptz:ProfileToken>{_onvif_profile(cam)}</tptz:ProfileToken>"
        f"</tptz:GetPresets>"
    )
    xml_text = await _onvif_post(cam, "GetPresets", body)
    try:
        root = ET.fromstring(xml_text)
        NS = "http://www.onvif.org/ver10/schema"
        result = []
        for preset in root.iter(f"{{{NS}}}Preset"):
            token = preset.get("token", "")
            name_el = preset.find(f"{{{NS}}}Name")
            name = (name_el.text or f"Preset {token}") if name_el is not None else f"Preset {token}"
            if token:
                result.append({"id": token, "name": name})
        return result
    except Exception as e:
        print(f"ONVIF GetPresets parse error: {e}")
        return []


async def _onvif_goto_preset(cam: dict, token: str):
    body = (
        f"<tptz:GotoPreset>"
        f"<tptz:ProfileToken>{_onvif_profile(cam)}</tptz:ProfileToken>"
        f"<tptz:PresetToken>{token}</tptz:PresetToken>"
        f"</tptz:GotoPreset>"
    )
    await _onvif_post(cam, "GotoPreset", body)


@app.post("/ptz/{cam_id}")
async def ptz_click(cam_id: str, click: ClickPayload):
    cam = cameras.get(cam_id)
    if not cam:
        raise HTTPException(404, "Camera not found")
    if not cam.get("ptz"):
        raise HTTPException(400, "Camera is not PTZ")

    dx = (click.x - 0.5) * 2   # -1 = far left, +1 = far right
    dy = (click.y - 0.5) * 2   # -1 = top,      +1 = bottom

    if math.sqrt(dx * dx + dy * dy) < 0.05:
        return {"ok": True, "note": "near centre, no move"}

    fov_h = cam.get("fov_h", 90)
    fov_v = cam.get("fov_v", 55)

    want_pan  =  dx * fov_h / 2
    want_tilt = -dy * fov_v / 2   # screen Y is inverted

    pc = _ptz_cam(cam)

    if _is_onvif(cam):
        if cam.get("pan_invert",  False): want_pan  = -want_pan
        if cam.get("tilt_invert", False): want_tilt = -want_tilt

        # Dahua: use proprietary CGI PositionABS for exact click-to-centre.
        # Mirrors what SmartPSS Lite does internally.
        if cam.get("brand", "").lower() == "dahua":
            pos = await _dahua_get_position(cam)
            if pos is not None:
                cur_pan, cur_tilt = pos
                target_pan  = (cur_pan  + want_pan)  % 360.0
                target_tilt = max(-90.0, min(90.0, cur_tilt + want_tilt))
                print(f"PTZ Dahua CGI {cam['id']}: click=({click.x:.2f},{click.y:.2f}) "
                      f"cur=({cur_pan:.1f}°,{cur_tilt:.1f}°) → target=({target_pan:.1f}°,{target_tilt:.1f}°)")
                ok = await _dahua_position_abs(cam, target_pan, target_tilt)
                if ok:
                    return {"ok": True, "mode": "dahua_cgi_abs"}
                print(f"PTZ Dahua PositionABS failed — falling back to timed ONVIF")
            else:
                print(f"PTZ Dahua getStatus failed — falling back to timed ONVIF")

        # Timed ContinuousMove fallback (all ONVIF brands, or Dahua when CGI fails).
        # Accuracy improvements over naive approach:
        #   1. Speed scales with click distance (slow near centre → less overshoot)
        #   2. Stop is sent early by ONVIF_STOP_LEAD_S to compensate for network/brake latency
        ONVIF_STOP_LEAD_S = cam.get("onvif_stop_lead", 0.12)
        max_spd = cam.get("ptz_speed", 40) / 63.0
        dps     = cam.get("ptz_degrees_per_second", 20)

        async def _onvif_timed_axis(pan_v: float, tilt_v: float, degrees: float):
            magnitude = min(1.0, degrees / (fov_h / 2))
            spd = max(0.2, min(1.0, magnitude)) * max_spd
            duration_s = max(0.05, degrees / dps - ONVIF_STOP_LEAD_S)
            print(f"  ONVIF timed: pan_v={pan_v:.2f} tilt_v={tilt_v:.2f} "
                  f"spd={spd:.2f} dur={duration_s:.2f}s deg={degrees:.1f}°")
            await _onvif_continuous(pc, pan=pan_v * spd, tilt=tilt_v * spd)
            await asyncio.sleep(duration_s)
            await _onvif_stop(pc)

        print(f"PTZ ONVIF {cam['id']}: click=({click.x:.2f},{click.y:.2f}) "
              f"pan={want_pan:.1f}° tilt={want_tilt:.1f}°")
        if abs(want_pan) > 0.5:
            await _onvif_timed_axis(1.0 if want_pan > 0 else -1.0, 0.0, abs(want_pan))
        if abs(want_tilt) > 0.5:
            await _onvif_timed_axis(0.0, 1.0 if want_tilt > 0 else -1.0, abs(want_tilt))
        return {"ok": True, "mode": "onvif_timed"}

    ptz_speed = cam.get("ptz_speed", 20)

    sx = (1 if want_pan  > 0.5 else (-1 if want_pan  < -0.5 else 0))
    sy = (1 if want_tilt > 0.5 else (-1 if want_tilt < -0.5 else 0))
    if cam.get("pan_invert",  False): sx = -sx
    if cam.get("tilt_invert", False): sy = -sy

    if sx == 0 and sy == 0:
        return {"ok": True, "note": "delta too small"}

    pan_dir  = _DIR.get((sx,  0))
    tilt_dir = _DIR.get(( 0, sy))

    print(f"PTZ {cam['id']}: click=({click.x:.2f},{click.y:.2f}) "
          f"want_pan={want_pan:.1f}° want_tilt={want_tilt:.1f}° "
          f"pan={pan_dir} tilt={tilt_dir}")

    dps = cam.get("ptz_degrees_per_second", 20)

    async def _timed_axis(op: str, degrees: float):
        duration_s = max(0.1, degrees / dps)
        resp = await _nvr_call(pc, "PtzCtrl", [{"cmd": "PtzCtrl", "action": 0, "param": {
            "channel": pc.get("channel", 0), "op": op, "speed": ptz_speed, "timeout": 0,
        }}])
        print(f"  PtzCtrl {op} ({duration_s:.2f}s): {resp}")
        await asyncio.sleep(duration_s)
        await _ptz_stop(pc)

    if pan_dir:
        await _timed_axis(pan_dir, abs(want_pan))
    if tilt_dir:
        await _timed_axis(tilt_dir, abs(want_tilt))

    return {"ok": True, "mode": "timed"}

# ── PTZ joystick ──────────────────────────────────────────────────────────────

class JoystickPayload(BaseModel):
    x: float  # -1.0 = full left,  +1.0 = full right
    y: float  # -1.0 = full up,    +1.0 = full down (screen space)


@app.post("/ptz/{cam_id}/start")
async def ptz_joystick_start(cam_id: str, payload: JoystickPayload):
    cam = cameras.get(cam_id)
    if not cam or not cam.get("ptz"):
        raise HTTPException(404, "Camera not found or not PTZ")

    ax, ay = abs(payload.x), abs(payload.y)
    if ax < 0.05 and ay < 0.05:
        return {"ok": True, "note": "deadzone"}

    pc = _ptz_cam(cam)

    if _is_onvif(cam):
        # ONVIF: ContinuousMove; payload is already -1..1; screen y+ = down, ONVIF y+ = up
        pan_n  =  payload.x if not cam.get("pan_invert",  False) else -payload.x
        tilt_n = -payload.y if not cam.get("tilt_invert", False) else  payload.y
        spd = cam.get("ptz_speed", 40) / 63.0  # normalize 1-63 to 0-1
        await _onvif_continuous(pc, pan=pan_n * spd, tilt=tilt_n * spd)
        return {"ok": True, "mode": "onvif_continuous"}

    sx = 1 if payload.x > 0 else -1
    sy = 1 if payload.y > 0 else -1
    if cam.get("pan_invert",  False): sx = -sx
    if cam.get("tilt_invert", False): sy = -sy

    # Dominant axis — NVR ignores tilt component in diagonal commands
    if ax >= ay:
        op, magnitude = ("Right" if sx > 0 else "Left"), ax
    else:
        op, magnitude = ("Down" if sy > 0 else "Up"), ay

    speed = max(1, round(cam.get("ptz_speed", 20) * magnitude))
    await _nvr_call(pc, "PtzCtrl", [{"cmd": "PtzCtrl", "action": 0, "param": {
        "channel": pc.get("channel", 0), "op": op, "speed": speed, "timeout": 0,
    }}])
    return {"ok": True, "op": op, "speed": speed}


@app.post("/ptz/{cam_id}/stop")
async def ptz_joystick_stop(cam_id: str):
    cam = cameras.get(cam_id)
    if not cam or not cam.get("ptz"):
        raise HTTPException(404, "Camera not found or not PTZ")
    pc = _ptz_cam(cam)
    if _is_onvif(cam):
        await _onvif_stop(pc)
        # Joystick leaves camera at an unknown position; invalidate so next click
        # doesn't use stale tracking and get an incorrect absolute target
        if cam.get("brand", "").lower() == "dahua":
            _dahua_tracked_pos.pop(cam_id, None)
    else:
        await _ptz_stop(pc)
    return {"ok": True}

# ── Zoom control ───────────────────────────────────────────────────────────────

class ZoomPayload(BaseModel):
    direction: int  # +1 = zoom in, -1 = zoom out
    steps: int = 1


@app.post("/zoom/{cam_id}")
async def zoom(cam_id: str, payload: ZoomPayload):
    cam = cameras.get(cam_id)
    if not cam:
        raise HTTPException(404, "Camera not found")
    if not cam.get("ptz"):
        raise HTTPException(400, "Camera is not PTZ")

    duration_s = cam.get("zoom_step_s", 0.3) * payload.steps
    pc = _ptz_cam(cam)

    if _is_onvif(cam):
        zoom_v = 0.5 * payload.direction  # +0.5 = in, -0.5 = out
        await _onvif_continuous(pc, zoom=zoom_v)
        await asyncio.sleep(duration_s)
        await _onvif_stop(pc)
        return {"ok": True, "op": "onvif_zoom", "duration_s": round(duration_s, 2)}

    op = "ZoomInc" if payload.direction > 0 else "ZoomDec"
    zoom_speed = cam.get("zoom_speed", 40)
    await _nvr_call(pc, "PtzCtrl", [{"cmd": "PtzCtrl", "action": 0, "param": {
        "channel": pc.get("channel", 0), "op": op, "speed": zoom_speed, "timeout": 0,
    }}])
    await asyncio.sleep(duration_s)
    await _ptz_stop(pc)

    return {"ok": True, "op": op, "duration_s": round(duration_s, 2)}

# ── PTZ Presets ────────────────────────────────────────────────────────────────

_preset_cache: dict[str, list[dict]] = {}


@app.get("/presets/{cam_id}")
async def get_presets(cam_id: str):
    cam = cameras.get(cam_id)
    if not cam or not cam.get("ptz"):
        raise HTTPException(404, "Camera not found or not PTZ")
    pc = _ptz_cam(cam)
    try:
        if _is_onvif(cam):
            result = await _onvif_get_presets(pc)
        else:
            data = await _nvr_call(pc, "GetPtzPreset", [{"cmd": "GetPtzPreset", "action": 0,
                "param": {"channel": pc.get("channel", 0)}}], timeout=5)
            raw = data[0]["value"]["PtzPreset"]
            result = [
                {"id": str(int(p["id"])), "name": p.get("name") or f"Preset {p['id']}"}
                for p in raw
                if p.get("enable", 1)
            ]
        _preset_cache[cam_id] = result
        return result
    except Exception as e:
        print(f"GetPresets failed for {cam_id}: {e}")
        return []


@app.post("/preset/{cam_id}/{preset_id}")
async def goto_preset(cam_id: str, preset_id: str):
    cam = cameras.get(cam_id)
    if not cam or not cam.get("ptz"):
        raise HTTPException(404, "Camera not found or not PTZ")
    pc = _ptz_cam(cam)

    if _is_onvif(cam):
        try:
            await _onvif_goto_preset(pc, preset_id)
            print(f"Preset ONVIF {cam_id} token={preset_id!r}: ok")
            # Camera moved to an unknown absolute position; invalidate so next click
            # doesn't use stale tracking and get an incorrect absolute target
            if cam.get("brand", "").lower() == "dahua":
                _dahua_tracked_pos.pop(cam_id, None)
            return {"ok": True, "preset": preset_id}
        except Exception as e:
            print(f"Preset ONVIF {cam_id} token={preset_id!r}: {e}")
            return {"ok": False, "error": str(e)}

    pid = int(preset_id)
    if "ptz_ip" in cam:
        resp = await _nvr_call(pc, "GoPtzPreset", [{"cmd": "GoPtzPreset", "action": 0,
            "param": {"channel": 0, "id": pid}}])
        print(f"Preset {cam_id} #{pid} (direct): {resp}")
        return {"ok": True, "preset": pid}

    channel = pc.get("channel", 0)
    speed = cam.get("ptz_speed", 40)
    resp = await _nvr_call(pc, "PtzCtrl", [{"cmd": "PtzCtrl", "action": 0, "param": {
        "channel": channel, "op": "ToPos", "id": pid, "speed": speed,
    }}])
    code = resp[0].get("code", -99)
    print(f"Preset {cam_id} #{pid} ToPos: code={code}")
    return {"ok": code == 0, "preset": pid}

# ── Camera list ────────────────────────────────────────────────────────────────

@app.get("/cameras")
def list_cameras():
    return [
        {
            "id": c["id"],
            "name": c["name"],
            "ptz": bool(c.get("ptz", False)),
            "playback": bool(c.get("playback", False)),
            "monitor_preset": c.get("monitor_preset"),
            "go2rtc_port": GO2RTC_PORT,
        }
        for c in _config["cameras"]
    ]

# ── WebRTC signalling proxy ────────────────────────────────────────────────────
# Proxying through FastAPI keeps the browser on one origin and avoids CORS issues
# when the browser POSTs the SDP offer directly to go2rtc's port.

@app.post("/api/webrtc/{cam_id}")
async def webrtc_offer(cam_id: str, request: Request):
    if cam_id not in cameras:
        raise HTTPException(404, "Camera not found")
    body = await request.body()
    async with aiohttp.ClientSession() as s:
        async with s.post(
            f"http://localhost:{GO2RTC_PORT}/api/webrtc?src={cam_id}",
            data=body,
            headers={"Content-Type": "application/sdp"},
        ) as resp:
            answer = await resp.text()
            print(f"go2rtc WebRTC {cam_id}: status={resp.status} body_len={len(answer)}")
            if resp.status != 200:
                print(f"  go2rtc error body: {answer[:300]}")
            return Response(content=answer, status_code=resp.status, media_type="application/sdp")


# ── RTSP connectivity test ─────────────────────────────────────────────────────

@app.post("/api/cameras/test")
async def api_test_camera(request: Request):
    """TCP + RTSP handshake check — called before saving to avoid hammering bad endpoints."""
    data = await request.json()
    ip = data.get("ip", "").strip()
    if not ip:
        return {"ok": False, "error": "No IP address provided"}

    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(ip, 554), timeout=3.0
        )
    except asyncio.TimeoutError:
        return {"ok": False, "error": f"Timed out — nothing responded on {ip}:554"}
    except OSError as e:
        return {"ok": False, "error": f"Cannot reach {ip}:554 — {e}"}

    resp_str = ""
    try:
        writer.write(f"OPTIONS rtsp://{ip}:554/ RTSP/1.0\r\nCSeq: 1\r\n\r\n".encode())
        await writer.drain()
        raw = await asyncio.wait_for(reader.read(512), timeout=3.0)
        resp_str = raw.decode("utf-8", errors="replace")
    except asyncio.TimeoutError:
        pass
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass

    if not resp_str:
        return {"ok": False, "error": f"Connected to {ip}:554 but got no response — may not be an RTSP server"}
    if "RTSP/1.0" not in resp_str:
        return {"ok": False, "error": f"Unexpected response from {ip}:554 — may not be an RTSP server"}

    return {"ok": True, "message": f"RTSP server confirmed at {ip}:554"}


# ── Camera management API ──────────────────────────────────────────────────────

def _save_config():
    """Write current _config to config.yaml and rebuild the cameras dict."""
    with open(CONFIG_PATH, "w") as f:
        yaml.dump(_config, f, default_flow_style=False, allow_unicode=True)
    cameras.clear()
    cameras.update({c["id"]: c for c in _config["cameras"]})


def _reload_go2rtc():
    """Rewrite go2rtc config from current _config and restart the process."""
    global _go2rtc_proc

    streams = {}
    for cam in _config["cameras"]:
        streams[f"{cam['id']}_main"] = _rtsp_url(cam, "main")
        streams[f"{cam['id']}_sub"]  = _rtsp_url(cam, "sub")

    go2rtc_cfg = {
        "streams": streams,
        "api": {"listen": f":{GO2RTC_PORT}", "origin": "*"},
        "log": {"level": "warn"},
        "rtsp": {"listen": ""},
    }
    cfg_file = BASE / "go2rtc_generated.yaml"
    with open(cfg_file, "w") as f:
        yaml.dump(go2rtc_cfg, f)

    if _go2rtc_proc and _go2rtc_proc.poll() is None:
        _go2rtc_proc.terminate()
        try:
            _go2rtc_proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            _go2rtc_proc.kill()

    _go2rtc_proc = subprocess.Popen(
        [str(GO2RTC_BIN), "-config", str(cfg_file)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    threading.Thread(target=_drain_go2rtc_output, args=(_go2rtc_proc,), daemon=True).start()

    for _ in range(20):
        try:
            urllib.request.urlopen(f"http://localhost:{GO2RTC_PORT}/api/streams", timeout=1)
            break
        except Exception:
            time.sleep(0.3)
    else:
        print("WARNING: go2rtc did not restart in time")

    _copy_go2rtc_js()
    print("go2rtc reloaded")


@app.get("/api/cameras")
def api_list_cameras():
    return _config["cameras"]


@app.post("/api/cameras", status_code=201)
async def api_add_camera(request: Request):
    data = await request.json()
    cam_id = data.get("id", "").strip()
    if not cam_id:
        raise HTTPException(400, "id is required")
    if any(c["id"] == cam_id for c in _config["cameras"]):
        raise HTTPException(409, f"Camera '{cam_id}' already exists")
    _config["cameras"].append(data)
    _save_config()
    await asyncio.get_event_loop().run_in_executor(None, _reload_go2rtc)
    return data


@app.put("/api/cameras/{cam_id}")
async def api_update_camera(cam_id: str, request: Request):
    data = await request.json()
    idx = next((i for i, c in enumerate(_config["cameras"]) if c["id"] == cam_id), None)
    if idx is None:
        raise HTTPException(404, f"Camera '{cam_id}' not found")

    new_id = data.get("id", cam_id).strip()
    if new_id != cam_id and any(c["id"] == new_id for c in _config["cameras"]):
        raise HTTPException(409, f"Camera '{new_id}' already exists")

    old_cam = _config["cameras"][idx]
    # Merge: preserve existing fields not present in the incoming payload
    # (e.g. onvif_profile, dahua_default_pan — custom fields the settings UI doesn't know about)
    merged = {**old_cam, **data}
    _config["cameras"][idx] = merged
    _save_config()

    # Invalidate cached token if connection details changed
    old_key = _nvr_key(old_cam)
    new_key = _nvr_key(data)
    if old_key != new_key:
        _tokens.pop(old_key, None)

    await asyncio.get_event_loop().run_in_executor(None, _reload_go2rtc)
    return data


@app.delete("/api/cameras/{cam_id}", status_code=204)
async def api_delete_camera(cam_id: str):
    idx = next((i for i, c in enumerate(_config["cameras"]) if c["id"] == cam_id), None)
    if idx is None:
        raise HTTPException(404, f"Camera '{cam_id}' not found")
    _config["cameras"].pop(idx)
    _save_config()
    _preset_cache.pop(cam_id, None)
    await asyncio.get_event_loop().run_in_executor(None, _reload_go2rtc)


# ── Playback ───────────────────────────────────────────────────────────────────

def _dt_to_reolink(dt: datetime.datetime) -> dict:
    return {"year": dt.year, "mon": dt.month, "day": dt.day,
            "hour": dt.hour, "min": dt.minute, "sec": dt.second}


def _reolink_to_epoch(d: dict) -> float:
    return datetime.datetime(
        d["year"], d["mon"], d["day"],
        d["hour"], d.get("min", 0), d.get("sec", 0)
    ).timestamp()


async def _ensure_token(cam: dict) -> str:
    """Return a valid cached token, logging in if needed."""
    key = _nvr_key(cam)
    lock = _get_nvr_lock(cam)
    async with lock:
        token, expires = _tokens.get(key, ("", 0.0))
        if not token or time.time() >= expires - 60:
            port = cam.get("port", 80)
            scheme = "https" if port == 443 or cam.get("https") else "http"
            data = await _post(
                f"{scheme}://{cam['ip']}:{port}/api.cgi?cmd=Login",
                [{"cmd": "Login", "action": 0, "param": {
                    "User": {"userName": cam["username"], "password": cam["password"]}
                }}]
            )
            token = data[0]["value"]["Token"]["name"]
            lease = data[0]["value"]["Token"]["leaseTime"]
            _tokens[key] = (token, time.time() + lease)
        return token


class PlaybackPayload(BaseModel):
    minutes_ago: float = 5.0


@app.post("/api/playback/{cam_id}")
async def api_playback_find(cam_id: str, payload: PlaybackPayload):
    """Search for the recording that covers (now - minutes_ago) and return seek info."""
    cam = cameras.get(cam_id)
    if not cam:
        raise HTTPException(404, "Camera not found")
    if not cam.get("playback"):
        raise HTTPException(400, "Playback not enabled for this camera")

    target_dt  = datetime.datetime.now() - datetime.timedelta(minutes=payload.minutes_ago)
    search_start = target_dt - datetime.timedelta(minutes=30)
    search_end   = target_dt + datetime.timedelta(minutes=5)

    try:
        data = await _nvr_call(cam, "Search", [{"cmd": "Search", "action": 0, "param": {
            "Search": {
                "channel":    cam.get("channel", 0),
                "onlyStatus": 0,
                "streamType": cam.get("stream", "main"),
                "StartTime":  _dt_to_reolink(search_start),
                "EndTime":    _dt_to_reolink(search_end),
            }
        }}])
    except Exception as e:
        raise HTTPException(502, f"Recording search failed: {e}")

    try:
        files = data[0]["value"]["SearchResult"]["File"]
    except (KeyError, IndexError, TypeError):
        return {"found": False, "reason": "no recordings in range"}

    if not files:
        return {"found": False, "reason": "no recordings in range"}

    target_epoch = target_dt.timestamp()

    # Prefer a file whose window contains the target time
    best = next(
        (f for f in files
         if _reolink_to_epoch(f["StartTime"]) <= target_epoch <= _reolink_to_epoch(f["EndTime"])),
        None
    )
    # Fall back to the most recent file ending before the target
    if best is None:
        before = [f for f in files if _reolink_to_epoch(f["EndTime"]) <= target_epoch]
        best = max(before, key=lambda f: _reolink_to_epoch(f["EndTime"])) if before else None

    if best is None:
        return {"found": False, "reason": "no suitable recording found"}

    start_epoch = _reolink_to_epoch(best["StartTime"])
    seek_s = max(0.0, target_epoch - start_epoch)

    return {
        "found":       True,
        "filename":    best["name"],
        "seek_offset_s": round(seek_s),
        "start_epoch": start_epoch,
    }


@app.get("/api/playback/{cam_id}/stream")
async def api_playback_stream(cam_id: str, filename: str, request: Request):
    """Proxy the recording file from the hub/NVR, supporting Range requests for seeking."""
    cam = cameras.get(cam_id)
    if not cam:
        raise HTTPException(404, "Camera not found")

    token = await _ensure_token(cam)
    port   = cam.get("port", 80)
    scheme = "https" if port == 443 or cam.get("https") else "http"
    dl_url = (
        f"{scheme}://{cam['ip']}:{port}/api.cgi"
        f"?cmd=Download&token={token}"
        f"&channel={cam.get('channel', 0)}&filename={filename}"
    )

    req_headers: dict[str, str] = {}
    if rng := request.headers.get("range"):
        req_headers["Range"] = rng

    session = aiohttp.ClientSession()
    try:
        resp = await session.get(
            dl_url, headers=req_headers, ssl=False,
            timeout=aiohttp.ClientTimeout(total=None, connect=10),
        )
        resp_headers = {"Accept-Ranges": "bytes"}
        for h in ("Content-Length", "Content-Range"):
            if h in resp.headers:
                resp_headers[h] = resp.headers[h]

        async def _stream():
            try:
                async for chunk in resp.content.iter_chunked(65536):
                    yield chunk
            finally:
                resp.release()
                await session.close()

        return StreamingResponse(
            _stream(),
            status_code=resp.status,
            headers=resp_headers,
            media_type=resp.content_type or "video/mp4",
        )
    except Exception as e:
        await session.close()
        raise HTTPException(502, f"Stream error: {e}")


# ── go2rtc health & control ────────────────────────────────────────────────────

@app.get("/api/go2rtc/health")
async def api_go2rtc_health():
    alive = _go2rtc_proc is not None and _go2rtc_proc.poll() is None
    api_ok = False
    stream_keys: list[str] = []
    if alive:
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(
                    f"http://localhost:{GO2RTC_PORT}/api/streams",
                    timeout=aiohttp.ClientTimeout(total=2),
                ) as r:
                    if r.status == 200:
                        api_ok = True
                        stream_keys = list((await r.json(content_type=None)).keys())
        except Exception:
            pass
    recent = list(_go2rtc_log)
    errors = [l for l in recent if any(w in l.lower() for w in ("error", "warn", "fail", "conn"))]
    return {
        "alive": alive,
        "api_ok": api_ok,
        "streams": stream_keys,
        "recent_errors": errors[-15:],
        "log_tail": recent[-15:],
    }


@app.post("/api/go2rtc/restart")
async def api_go2rtc_restart():
    await asyncio.get_event_loop().run_in_executor(None, _reload_go2rtc)
    return {"ok": True}


# ── ONVIF WS-Discovery ─────────────────────────────────────────────────────────

_WSD_MCAST = ("239.255.255.250", 3702)

_WSD_PROBE_TMPL = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<Envelope xmlns="http://www.w3.org/2003/05/soap-envelope"'
    ' xmlns:dn="http://www.onvif.org/ver10/network/wsdl"'
    ' xmlns:wsa="http://schemas.xmlsoap.org/ws/2004/08/addressing"'
    ' xmlns:wsdd="http://schemas.xmlsoap.org/ws/2005/04/discovery">'
    "<Header>"
    "<wsa:Action>http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe</wsa:Action>"
    "<wsa:MessageID>urn:uuid:{uid}</wsa:MessageID>"
    "<wsa:To>urn:schemas-xmlsoap-org:ws:2005:04:discovery</wsa:To>"
    "</Header>"
    "<Body>"
    "<wsdd:Probe><wsdd:Types>dn:NetworkVideoTransmitter</wsdd:Types></wsdd:Probe>"
    "</Body>"
    "</Envelope>"
)

_NS_WSDD = "http://schemas.xmlsoap.org/ws/2005/04/discovery"


def _parse_wsd_response(data: bytes, sender_ip: str) -> dict | None:
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return None

    xaddrs_el = root.find(f".//{{{_NS_WSDD}}}XAddrs")
    xaddrs = xaddrs_el.text.strip() if xaddrs_el is not None and xaddrs_el.text else ""

    # Prefer IP from XAddr URL over UDP sender (avoids NAT surprises on same host)
    ip = sender_ip
    if xaddrs:
        m = re.search(r"https?://([0-9]+\.[0-9]+\.[0-9]+\.[0-9]+)", xaddrs)
        if m:
            ip = m.group(1)

    model = ""
    scopes_el = root.find(f".//{{{_NS_WSDD}}}Scopes")
    if scopes_el is not None and scopes_el.text:
        m = re.search(r"onvif://www\.onvif\.org/hardware/([^\s]+)", scopes_el.text)
        if m:
            model = m.group(1).replace("%20", " ").replace("+", " ")

    return {"ip": ip, "model": model}


def _ws_discover(timeout: float = 3.0) -> list[dict]:
    probe = _WSD_PROBE_TMPL.format(uid=uuid.uuid4()).encode()
    results: list[dict] = []
    seen: set[str] = set()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 4)
        sock.settimeout(0.5)
        sock.sendto(probe, _WSD_MCAST)

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                data, addr = sock.recvfrom(65535)
            except socket.timeout:
                continue
            result = _parse_wsd_response(data, addr[0])
            if result and result["ip"] not in seen:
                seen.add(result["ip"])
                results.append(result)
                print(f"WSD: {result['ip']}  model={result['model']!r}")
    finally:
        sock.close()

    return results


@app.get("/api/discover")
async def api_discover():
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _ws_discover)


# ── Static files ───────────────────────────────────────────────────────────────

@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return Response(status_code=204)


app.mount("/", StaticFiles(directory=_STATIC_DIR, html=True), name="static")

# ── System tray ────────────────────────────────────────────────────────────────

def _make_tray_icon():
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rectangle([4, 16, 60, 52], fill="#1e40af")        # camera body
    d.rectangle([44, 8,  58, 18], fill="#1e40af")        # viewfinder bump
    d.ellipse(  [16, 20, 48, 48], fill="#0f172a")        # lens outer
    d.ellipse(  [22, 26, 42, 42], fill="#93c5fd")        # lens glass
    d.ellipse(  [28, 32, 36, 36], fill="#0f172a")        # lens pupil
    return img


def _wait_for_server(port: int, timeout: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(f"http://localhost:{port}/cameras", timeout=1)
            return True
        except Exception:
            time.sleep(0.25)
    return False


def _run_uvicorn(port: int):
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")


def _main_tray(port: int):
    import pystray

    server = threading.Thread(target=_run_uvicorn, args=(port,), daemon=True)
    server.start()
    _wait_for_server(port)
    webbrowser.open(f"http://localhost:{port}")

    icon_img = _make_tray_icon()

    def on_open(_icon, _item):
        webbrowser.open(f"http://localhost:{port}")

    def on_settings(_icon, _item):
        webbrowser.open(f"http://localhost:{port}/settings.html")

    def on_exit(icon, _item):
        stop_go2rtc()
        icon.stop()

    tray = pystray.Icon(
        "Reolink Viewer",
        icon_img,
        "Reolink Viewer",
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
    for i, arg in enumerate(sys.argv[1:]):
        if arg in ("--port", "-p") and i + 2 < len(sys.argv):
            port = int(sys.argv[i + 2])

    try:
        start_go2rtc()
        print(f"Reolink viewer → http://localhost:{port}")
        try:
            import pystray   # noqa: F401
            from PIL import Image  # noqa: F401
            _main_tray(port)
        except ImportError:
            # pystray / Pillow not installed — fall back to visible console
            uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
    except Exception as e:
        print(f"\nERROR: {e}")
        input("\nPress Enter to close...")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\nERROR: {e}")
        import traceback; traceback.print_exc()
        input("\nPress Enter to close...")
