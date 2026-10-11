#
# Version: 1.3
# Updated: 2026-10-11
#
# v1.0 - 2026-05-18 - Initial extraction from ptzlite.py monolith
# v1.1 - 2026-05-21 - Add GET/POST /api/config/app_window and POST /api/open_viewer
# v1.2 - 2026-10-11 - Camera test resolves a masked password to the stored one
# v1.3 - 2026-10-11 - GET/POST /api/config/prewarm
#

import asyncio
import logging
import threading
import traceback

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse

from api.cameras import PW_MASK
from core.config import _config, _save_config, cameras
from core.paths import APP_VERSION, _STATIC_DIR, _USER_DIR
from services.browser import open_viewer
from services.go2rtc import PREWARM_MODES, _launch_prewarm, prewarm_mode
from services.discovery import _ws_discover
from services.rtsp import _clean_rtsp_url, _rtsp_describe_sync

router = APIRouter()
logger = logging.getLogger("PTZLite.api.misc")


@router.get("/api/version")
async def api_version():
    return {"version": APP_VERSION}


@router.get("/api/log")
async def api_get_log(lines: int = 500):
    """Return the last N lines of ptzlite.log as plain text."""
    log_path = _USER_DIR / "ptzlite.log"
    if not log_path.exists():
        return Response(content="Log file not found.\n", media_type="text/plain; charset=utf-8")
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
        tail = "\n".join(text.splitlines()[-lines:])
        return Response(content=tail + "\n", media_type="text/plain; charset=utf-8")
    except Exception as e:
        return Response(content=f"Error reading log: {e}\n", media_type="text/plain; charset=utf-8")


@router.get("/api/discover")
async def api_discover():
    loop = asyncio.get_running_loop()
    try:
        results = await loop.run_in_executor(None, _ws_discover)
        return results
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Discovery failed: {e}")


@router.post("/api/cameras/test")
async def api_test_camera(request: Request):
    """
    Full RTSP DESCRIBE test against the actual stream URL with credentials.
    Detects bad channels, wrong passwords, and unreachable hosts.
    """
    data = await request.json()
    ip = data.get("ip", "").strip()
    if not ip:
        return {"ok": False, "error": "No IP address provided"}

    user     = data.get("username", "admin")
    password = data.get("password", "")
    # Settings shows a masked password for saved cameras; test with the real one.
    if password == PW_MASK and data.get("id") in cameras:
        password = cameras[data["id"]].get("password", "")
    brand    = data.get("brand", "reolink")
    channel  = data.get("channel", 0)
    stream   = data.get("stream", "sub")
    port     = data.get("port", 554)

    cam = {"ip": ip, "port": port, "username": user, "password": password,
           "brand": brand, "channel": channel, "stream": stream}
    # Use clean URL (no inline creds, no go2rtc options) — cameras won't parse those
    url = _clean_rtsp_url(cam, stream)

    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, _rtsp_describe_sync, url, user, password)
    return result


@router.get("/api/config/app_window")
async def get_app_window():
    return {"app_window": bool(_config.get("app_window", False))}


@router.post("/api/config/app_window")
async def set_app_window(request: Request):
    data = await request.json()
    _config["app_window"] = bool(data.get("enabled", False))
    _save_config()
    logger.info("App window mode %s", "enabled" if _config["app_window"] else "disabled")
    return {"ok": True}


@router.get("/api/config/prewarm")
async def get_prewarm():
    return {"mode": prewarm_mode(), "modes": list(PREWARM_MODES)}


@router.post("/api/config/prewarm")
async def set_prewarm(request: Request):
    mode = (await request.json()).get("mode", "both")
    if mode not in PREWARM_MODES:
        raise HTTPException(400, f"mode must be one of {PREWARM_MODES}")
    _config["prewarm"] = mode
    _save_config()
    asyncio.create_task(_launch_prewarm(delay=0.5))   # apply now, no restart
    logger.info("Pre-warm mode set to %s", mode)
    return {"ok": True, "mode": mode}


@router.post("/api/open_viewer")
async def api_open_viewer(request: Request):
    """Trigger the server to open the viewer in the currently configured mode.

    The frontend passes its port so the server opens the correct URL.
    Runs in a daemon thread so the response returns before the subprocess spawns.
    """
    data = await request.json()
    port = int(data.get("port", 8765))
    threading.Thread(target=open_viewer, args=(port,), daemon=True).start()
    return {"ok": True}


@router.get("/favicon.ico", include_in_schema=False)
async def favicon():
    ico = _STATIC_DIR / "favicon.ico"
    if ico.exists():
        return FileResponse(str(ico), media_type="image/x-icon")
    return Response(status_code=204)
