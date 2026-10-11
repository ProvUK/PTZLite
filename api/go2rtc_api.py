#
# Version: 1.1
# Updated: 2026-10-11
#
# v1.0 - 2026-05-18 - Initial extraction from ptzlite.py monolith
# v1.1 - 2026-10-11 - Add authenticated /go2rtc/ws proxy (go2rtc now localhost-only);
#                     remove unused /api/webrtc proxy (requested a non-existent stream)
#

import asyncio
import logging
from urllib.parse import urlparse

import aiohttp
from fastapi import APIRouter, WebSocket

from api.auth import is_authenticated
from core.config import cameras
from core.paths import GO2RTC_PORT
import services.go2rtc as _go2rtc_mod
from services.go2rtc import (
    _cancel_prewarm,
    _go2rtc_log,
    _launch_prewarm,
    _reload_go2rtc,
)

router = APIRouter()
logger = logging.getLogger("PTZLite.api.go2rtc_api")

_STREAM_KINDS = ("main", "sub", "talk")


def _allowed_stream(src: str) -> bool:
    """Only proxy streams PTZLite itself registers: <cam_id>_main/_sub/_talk."""
    cam_id, _, kind = src.rpartition("_")
    return kind in _STREAM_KINDS and cam_id in cameras


def _same_origin(ws: WebSocket) -> bool:
    """Reject cross-site pages trying to open a stream socket on the user's behalf."""
    origin = ws.headers.get("origin")
    if not origin:
        return True   # non-browser client (no Origin header)
    return urlparse(origin).netloc == ws.headers.get("host", "")


@router.websocket("/go2rtc/ws")
async def go2rtc_ws_proxy(ws: WebSocket):
    """Relay the video-rtc player's signalling / MSE socket to go2rtc.

    HTTP middleware never sees WebSockets, so auth is checked here.
    """
    src = ws.query_params.get("src", "")
    if not is_authenticated(ws) or not _same_origin(ws):
        await ws.close(code=4401)
        return
    if not _allowed_stream(src):
        await ws.close(code=4404)
        return
    await ws.accept()

    upstream_url = f"ws://127.0.0.1:{GO2RTC_PORT}/api/ws?src={src}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(upstream_url, max_msg_size=0) as up:

                async def client_to_go2rtc():
                    while True:
                        msg = await ws.receive()
                        if msg["type"] == "websocket.disconnect":
                            return
                        if msg.get("text") is not None:
                            await up.send_str(msg["text"])
                        elif msg.get("bytes") is not None:
                            await up.send_bytes(msg["bytes"])

                async def go2rtc_to_client():
                    async for m in up:
                        if m.type == aiohttp.WSMsgType.TEXT:
                            await ws.send_text(m.data)
                        elif m.type == aiohttp.WSMsgType.BINARY:
                            await ws.send_bytes(m.data)
                        else:
                            return

                tasks = {asyncio.create_task(client_to_go2rtc()),
                         asyncio.create_task(go2rtc_to_client())}
                _, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for t in pending:
                    t.cancel()
    except Exception as e:
        logger.info("go2rtc ws proxy %s ended: %s", src, e)
    finally:
        try:
            await ws.close()
        except Exception:
            pass


@router.get("/api/go2rtc/health")
async def api_go2rtc_health():
    # Use module-level reference so we always see the latest proc after _reload_go2rtc
    proc = _go2rtc_mod._go2rtc_proc
    alive = proc is not None and proc.poll() is None
    api_ok = False
    stream_keys: list[str] = []
    if alive:
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(
                    f"http://127.0.0.1:{GO2RTC_PORT}/api/streams",
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


@router.post("/api/go2rtc/restart")
async def api_go2rtc_restart():
    _cancel_prewarm()
    await asyncio.get_running_loop().run_in_executor(None, _reload_go2rtc)
    asyncio.create_task(_launch_prewarm(delay=3.0))
    return {"ok": True}
