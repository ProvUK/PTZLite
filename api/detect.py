#
# Version: 1.0
# Updated: 2026-10-11
#
# v1.0 - 2026-10-11 - Server-side detection poller pushed over SSE (moved from api/ptz.py)
#
"""Live motion / AI detection for the camera grid.

One background poller (not one per open browser) queries every Reolink camera
and pushes changes to viewers over Server-Sent Events:

  * Batched: all channels on the same NVR are queried in ONE request
    (GetMdState + GetAiState per channel), instead of 1-2 calls per camera.
  * Only runs while at least one viewer is subscribed (viewers disconnect when
    their tab is hidden), so an unwatched PTZLite makes no detection traffic.
  * Skips an NVR while a PTZ move is in progress so click-to-centre never
    queues behind a detection sweep.
"""

import asyncio
import json
import logging

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from core.config import cameras
from services.ptz import _is_onvif, _ptz_cam
from services.ptz.reolink import _nvr_call, _nvr_key, ptz_recently_active

router = APIRouter()
logger = logging.getLogger("PTZLite.api.detect")

POLL_INTERVAL_S = 2.5
HEARTBEAT_S = 15.0
AI_TYPES = ("people", "vehicle", "dog_cat", "face", "package")

_latest: dict[str, dict] = {}
_subscribers: set[asyncio.Queue] = set()
_poller: asyncio.Task | None = None


def _reolink_cams() -> list[dict]:
    return [c for c in cameras.values()
            if c.get("enabled", True) and not _is_onvif(c)
            and c.get("brand", "").lower() == "reolink"]


def _parse(md: dict, ai: dict) -> dict:
    alerts: list[str] = []
    if ai.get("code", -1) == 0:
        v = ai.get("value", {})
        for t in AI_TYPES:
            d = v.get(t)
            if isinstance(d, dict) and d.get("support") and d.get("alarm_state"):
                alerts.append(t)
    motion = False
    if md.get("code", -1) == 0:
        motion = int(md.get("value", {}).get("state", 0)) == 1
    return {"motion": motion and not alerts, "alerts": alerts}


async def poll_once(previous: dict | None = None) -> dict:
    """One detection sweep: a single batched request per NVR."""
    previous = previous or {}
    groups: dict[str, list[dict]] = {}
    for cam in _reolink_cams():
        groups.setdefault(_nvr_key(_ptz_cam(cam)), []).append(cam)

    result: dict[str, dict] = {}
    for cams_on_nvr in groups.values():
        pc0 = _ptz_cam(cams_on_nvr[0])
        if ptz_recently_active(pc0):
            # A PTZ move is in flight on this NVR — keep last-known state, don't queue.
            for cam in cams_on_nvr:
                result[cam["id"]] = previous.get(cam["id"], {"motion": False, "alerts": []})
            continue
        payload = []
        for cam in cams_on_nvr:
            ch = _ptz_cam(cam).get("channel", 0)
            payload += [{"cmd": "GetMdState", "action": 0, "param": {"channel": ch}},
                        {"cmd": "GetAiState", "action": 0, "param": {"channel": ch}}]
        try:
            resp = await _nvr_call(pc0, "GetMdState", payload, timeout=5)
        except Exception as e:
            logger.debug("Detection sweep failed for %s: %s", _nvr_key(pc0), e)
            for cam in cams_on_nvr:
                result[cam["id"]] = previous.get(cam["id"], {"motion": False, "alerts": []})
            continue
        for i, cam in enumerate(cams_on_nvr):
            md = resp[2 * i] if 2 * i < len(resp) else {}
            ai = resp[2 * i + 1] if 2 * i + 1 < len(resp) else {}
            result[cam["id"]] = _parse(md, ai)
    return result


async def _poll_loop() -> None:
    global _latest, _poller
    try:
        while _subscribers:
            try:
                states = await poll_once(_latest)
                if states != _latest:
                    _latest = states
                    msg = json.dumps(states)
                    for q in list(_subscribers):
                        q.put_nowait(msg)
            except Exception as e:
                logger.warning("Detection poll error: %s", e)
            await asyncio.sleep(POLL_INTERVAL_S)
    finally:
        _poller = None


@router.get("/detect/stream")
async def detect_stream(request: Request):
    """SSE: sends the full state map now, then again whenever it changes."""
    global _poller
    q: asyncio.Queue = asyncio.Queue()
    _subscribers.add(q)
    if _poller is None:
        _poller = asyncio.create_task(_poll_loop())

    async def events():
        try:
            yield f"data: {json.dumps(_latest)}\n\n"
            while True:
                if await request.is_disconnected():
                    return
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=HEARTBEAT_S)
                    yield f"data: {msg}\n\n"
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
        finally:
            _subscribers.discard(q)

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/detect/states")
async def detect_states():
    """One-off snapshot (kept for scripts / debugging; the viewer uses /detect/stream)."""
    return _latest if _poller else await poll_once()
