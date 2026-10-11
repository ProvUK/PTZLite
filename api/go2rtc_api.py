#
# Version: 1.0
# Updated: 2026-05-18
#
# v1.0 - 2026-05-18 - Initial extraction from ptzlite.py monolith
#

import asyncio
import logging

import aiohttp
from fastapi import APIRouter, HTTPException, Request, Response

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


@router.post("/api/webrtc/{cam_id}")
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
            logger.info("go2rtc WebRTC %s: status=%d body_len=%d", cam_id, resp.status, len(answer))
            if resp.status != 200:
                logger.error("  go2rtc error body: %s", answer[:300])
            return Response(content=answer, status_code=resp.status, media_type="application/sdp")


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


@router.post("/api/go2rtc/restart")
async def api_go2rtc_restart():
    _cancel_prewarm()
    await asyncio.get_running_loop().run_in_executor(None, _reload_go2rtc)
    asyncio.create_task(_launch_prewarm(delay=3.0))
    return {"ok": True}
