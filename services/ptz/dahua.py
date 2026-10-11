#
# Version: 1.0
# Updated: 2026-05-18
#
# v1.0 - 2026-05-18 - Initial extraction from ptzlite.py monolith
#

# ── Dahua CGI API (HTTP Digest auth) ─────────────────────────────────────────
#
# SmartPSS Lite achieves exact click-to-centre via Dahua's proprietary CGI:
#   GET /cgi-bin/ptz.cgi?action=getStatus&channel=N  -> current pan/tilt/zoom
#   GET /cgi-bin/ptz.cgi?action=start&channel=N&code=PositionABS&arg1=PAN&arg2=TILT&arg3=SPEED
#
# This gives exact positioning without depending on move-duration timing.
# Falls back to timed ONVIF ContinuousMove if the NVR doesn't support it.
#
# NVR position readback limitation: many Dahua NVRs (including DHI-NVR54xx series) return
# HTTP 400 for getStatus on IP camera channels — the NVR doesn't aggregate camera position.
# Workaround: software-track position after every successful PositionABS command.
# Seed with dahua_default_pan / dahua_default_tilt in config.yaml if known.

import asyncio
import logging
import ssl
import urllib.error
import urllib.parse
import urllib.request

logger = logging.getLogger("PTZLite.services.ptz.dahua")

# cam_id -> (pan_deg, tilt_deg) after last successful PositionABS
_dahua_tracked_pos: dict[str, tuple[float, float]] = {}

_dahua_ssl_ctx = ssl.create_default_context()
_dahua_ssl_ctx.check_hostname = False
_dahua_ssl_ctx.verify_mode = ssl.CERT_NONE

# Persistent opener per NVR key — reuses digest-auth nonce and TCP connection
_dahua_openers: dict[str, urllib.request.OpenerDirector] = {}


def _get_dahua_opener(cam: dict) -> urllib.request.OpenerDirector:
    key = f"{cam['ip']}:{cam.get('port', 80)}:{cam['username']}"
    if key not in _dahua_openers:
        port = cam.get("port", 80)
        scheme = "https" if port == 443 or cam.get("https") else "http"
        base_url = f"{scheme}://{cam['ip']}:{port}/cgi-bin/ptz.cgi"
        mgr = urllib.request.HTTPPasswordMgrWithDefaultRealm()
        mgr.add_password(None, base_url, cam["username"], cam["password"])
        _dahua_openers[key] = urllib.request.build_opener(
            urllib.request.HTTPDigestAuthHandler(mgr),
            urllib.request.HTTPSHandler(context=_dahua_ssl_ctx),
        )
    return _dahua_openers[key]


def _dahua_cgi_sync(cam: dict, params: dict) -> tuple[int, str]:
    port = cam.get("port", 80)
    scheme = "https" if port == 443 or cam.get("https") else "http"
    base_url = f"{scheme}://{cam['ip']}:{port}/cgi-bin/ptz.cgi"
    full_url = base_url + "?" + urllib.parse.urlencode(params)
    try:
        with _get_dahua_opener(cam).open(full_url, timeout=5) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception as e:
        logger.error("Dahua CGI error: %s", e)
        return 0, ""


async def _dahua_cgi(cam: dict, params: dict) -> tuple[int, str]:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _dahua_cgi_sync, cam, params)


async def _dahua_get_position(cam: dict) -> tuple[float, float] | None:
    """Returns (pan_deg, tilt_deg) current camera position, or None if unknown.

    Fast paths (no network call):
      1. Software-tracked position (updated after each successful PositionABS)
      2. dahua_default_pan / dahua_default_tilt from config.yaml

    Slow path (only when we have no position at all):
      3. CGI getStatus — works on some firmware; many NVRs return 400
    """
    cam_id = cam["id"]

    # 1. Tracked position — set after every PositionABS, no network needed
    tracked = _dahua_tracked_pos.get(cam_id)
    if tracked is not None:
        logger.debug("Dahua %s: tracked (%.1f°, %.1f°)", cam_id, tracked[0], tracked[1])
        return tracked

    # 2. Configured default — seed for first click after app start, no network needed
    dp = cam.get("dahua_default_pan")
    dt = cam.get("dahua_default_tilt")
    if dp is not None and dt is not None:
        pos = (float(dp), float(dt))
        logger.debug("Dahua %s: default (%.1f°, %.1f°)", cam_id, pos[0], pos[1])
        return pos

    # 3. Slow path: CGI getStatus — only reached when we have no position at all.
    #    Many NVRs return 400 for IP camera channels; on success seeds the tracker.
    channel = cam.get("channel", 1)
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
        else:
            logger.warning("Dahua getStatus HTTP %s", status)
    except Exception as e:
        logger.error("Dahua getStatus error: %s", e)

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
    logger.info("Dahua PositionABS -> HTTP %s, target pan=%.2f° tilt=%.2f°", status, pan, tilt)
    if status == 200:
        # Camera is now at this position — cache it for the next click
        _dahua_tracked_pos[cam["id"]] = (pan, tilt)
    return status == 200
