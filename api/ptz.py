#
# Version: 2.3
# Updated: 2026-10-11
#
# v1.0 - 2026-05-18 - Initial extraction from ptzlite.py monolith
# v1.1 - 2026-05-19 - Fix camera keeps moving: send stop before timed click-to-move (Reolink + ONVIF)
# v1.2 - 2026-05-20 - Fix GetPresets crash when NVR returns error response (missing 'value' key)
# v1.3 - 2026-05-23 - Add reversible improved click-to-centre projection and per-axis speed calibration
# v1.4 - 2026-05-23 - Make timed-move minimum duration configurable per camera
# v1.5 - 2026-05-23 - Add per-axis click-to-centre calibration gains + /calibrate endpoint
# v1.6 - 2026-05-23 - Add acceleration-aware motor model (lead) + auto-cal raw_move/set_axis_cal
# v1.7 - 2026-05-23 - Ramp-aware piecewise move duration: fixes small-move (esp. tilt) overshoot
# v1.8 - 2026-05-23 - Drop fragile acceleration 'lead' term (corrupted tilt); add reset_cal endpoint
# v1.9 - 2026-05-23 - Add Reolink absolute zoom, auto-tracking, and guard-position endpoints
# v2.0 - 2026-07-28 - Add floodlight/IR, siren, patrol, save-preset, and live detection-state endpoints
# v2.1 - 2026-07-28 - Floodlight/IR/siren work on any Reolink camera, not just PTZ (_reolink_cam gate)
# v2.2 - 2026-10-11 - Detection moved to api/detect.py (server-side batched poller + SSE)
# v2.3 - 2026-10-11 - Remove dead lead / legacy-aim code paths
#

import asyncio
import logging
import math

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from core.config import _save_config, cameras
from services.ptz import _is_onvif, _ptz_cam, preset_cache
from services.ptz.dahua import _dahua_get_position, _dahua_position_abs, _dahua_tracked_pos
from services.ptz.onvif import (
    _onvif_continuous,
    _onvif_get_presets,
    _onvif_goto_preset,
    _onvif_stop,
)
from services.ptz.reolink import _DIR, _nvr_call, _ptz_stop

router = APIRouter()
logger = logging.getLogger("PTZLite.api.ptz")


class ClickPayload(BaseModel):
    x: float  # 0.0 = left, 1.0 = right
    y: float  # 0.0 = top,  1.0 = bottom


class JoystickPayload(BaseModel):
    x: float  # -1.0 = full left,  +1.0 = full right
    y: float  # -1.0 = full up,    +1.0 = full down (screen space)


class ZoomPayload(BaseModel):
    direction: int  # +1 = zoom in, -1 = zoom out
    steps: int = 1


class CalibratePayload(BaseModel):
    before: ClickPayload  # where the target was when the centring click was made
    after: ClickPayload   # where the same target ended up after the move


class RawMovePayload(BaseModel):
    axis: str          # "pan" | "tilt"
    dir: int           # +1 | -1
    seconds: float     # how long to run the motor


class AxisCalPayload(BaseModel):
    axis: str           # "pan" | "tilt"
    dps: float          # fitted degrees-per-second (slope)


class ZoomPosPayload(BaseModel):
    pos: int            # absolute optical-zoom position (camera's own units)


class TogglePayload(BaseModel):
    enable: bool


class GuardPayload(BaseModel):
    enable: bool | None = None      # None = leave enable unchanged
    timeout: int | None = None      # seconds before auto-return; None = unchanged
    save_pos: bool = False          # also save the current view as the guard position


class WhiteLedPayload(BaseModel):
    enable: bool
    bright: int | None = None       # 0-100; None = leave brightness unchanged


class IrPayload(BaseModel):
    state: str                      # "Auto" | "On" | "Off"


class SirenPayload(BaseModel):
    on: bool


class PatrolPayload(BaseModel):
    start: bool


class SavePresetPayload(BaseModel):
    id: int
    name: str = ""


def _screen_delta_to_angles(cam: dict, dx: float, dy: float) -> tuple[float, float]:
    """Convert normalised screen delta to pan/tilt degrees."""
    fov_h = float(cam.get("fov_h", 90))
    fov_v = float(cam.get("fov_v", 55))

    pan = math.degrees(math.atan(dx * math.tan(math.radians(fov_h) / 2)))
    tilt = -math.degrees(math.atan(dy * math.tan(math.radians(fov_v) / 2)))
    return pan, tilt


def _axis_dps(cam: dict, axis: str) -> float:
    """Return calibrated degrees-per-second for one axis, falling back to legacy field."""
    key = "pan_degrees_per_second" if axis == "pan" else "tilt_degrees_per_second"
    value = cam.get(key)
    if value is None:
        value = cam.get("ptz_degrees_per_second", 20)
    return max(0.1, float(value))


def _min_move_s(cam: dict) -> float:
    return max(0.0, float(cam.get("ptz_min_move_s", 0.1)))


def _axis_cal(cam: dict, axis: str) -> float:
    """Empirical click-to-centre gain for one axis (1.0 = uncalibrated).

    Absorbs FOV-magnitude, degrees-per-second and brake-overshoot error into a
    single multiplier on the commanded move duration. Set by /calibrate.
    """
    key = "pan_cal" if axis == "pan" else "tilt_cal"
    return max(0.2, min(5.0, float(cam.get(key, 1.0))))


def _move_duration(cam: dict, axis: str, degrees: float) -> float:
    """Motor-on time for an angular move: simple proportional model.

    duration = degrees / dps, trimmed by the manual per-axis gain.  This is the
    robust baseline.  An earlier acceleration-deadtime ('lead') term was dropped:
    it depended on a clean auto-cal line fit, which is unreliable on repetitive
    scenes (the tilt image-tracking can lock onto the wrong repeat), and a bad
    lead corrupted small moves.  The lead fields remain in config but are ignored.

    Excludes the per-platform min-move floor and any ONVIF stop-lead; callers
    apply those.
    """
    deg = max(0.0, degrees * _axis_cal(cam, axis))
    return deg / _axis_dps(cam, axis)


@router.post("/ptz/{cam_id}")
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

    want_pan, want_tilt = _screen_delta_to_angles(cam, dx, dy)

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
                logger.info(
                    "PTZ Dahua CGI %s: click=(%.2f,%.2f) cur=(%.1f°,%.1f°) -> target=(%.1f°,%.1f°)",
                    cam["id"], click.x, click.y, cur_pan, cur_tilt, target_pan, target_tilt,
                )
                ok = await _dahua_position_abs(cam, target_pan, target_tilt)
                if ok:
                    return {"ok": True, "mode": "dahua_cgi_abs"}
                logger.warning("PTZ Dahua PositionABS failed — falling back to timed ONVIF")
            else:
                logger.warning("PTZ Dahua getStatus failed — falling back to timed ONVIF")

        # Timed ContinuousMove (all non-Dahua ONVIF, or Dahua when CGI fails).
        # Speed scales linearly with click distance; stop is sent early to compensate
        # for network + mechanical brake latency.
        ONVIF_STOP_LEAD_S = cam.get("onvif_stop_lead", 0.12)
        max_spd = cam.get("ptz_speed", 40) / 63.0
        async def _onvif_timed_axis(pan_v: float, tilt_v: float, degrees: float, axis: str):
            axis_fov = fov_h if axis == "pan" else fov_v
            magnitude = min(1.0, degrees / (axis_fov / 2))
            spd = max(0.2, min(1.0, magnitude)) * max_spd
            duration_s = max(_min_move_s(cam),
                             _move_duration(cam, axis, degrees) - ONVIF_STOP_LEAD_S)
            logger.debug(
                "  ONVIF timed: pan_v=%.2f tilt_v=%.2f spd=%.2f dur=%.2fs deg=%.1f°",
                pan_v, tilt_v, spd, duration_s, degrees,
            )
            await _onvif_continuous(pc, pan=pan_v * spd, tilt=tilt_v * spd)
            await asyncio.sleep(duration_s)
            await _onvif_stop(pc)

        logger.info(
            "PTZ ONVIF %s: click=(%.2f,%.2f) pan=%.1f° tilt=%.1f°",
            cam["id"], click.x, click.y, want_pan, want_tilt,
        )
        # Stop first so any in-flight joystick /start commands don't outlive this move
        await _onvif_stop(pc)
        if abs(want_pan) > 0.5:
            await _onvif_timed_axis(1.0 if want_pan > 0 else -1.0, 0.0, abs(want_pan), "pan")
        if abs(want_tilt) > 0.5:
            await _onvif_timed_axis(0.0, 1.0 if want_tilt > 0 else -1.0, abs(want_tilt), "tilt")
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

    logger.info(
        "PTZ %s: click=(%.2f,%.2f) want_pan=%.1f° want_tilt=%.1f° pan=%s tilt=%s",
        cam["id"], click.x, click.y, want_pan, want_tilt, pan_dir, tilt_dir,
    )

    async def _timed_axis(op: str, degrees: float, axis: str):
        duration_s = max(_min_move_s(cam), _move_duration(cam, axis, degrees))
        resp = await _nvr_call(pc, "PtzCtrl", [{"cmd": "PtzCtrl", "action": 0, "param": {
            "channel": pc.get("channel", 0), "op": op, "speed": ptz_speed, "timeout": 0,
        }}])
        logger.info("  PtzCtrl %s (%.2fs): %s", op, duration_s, resp)
        await asyncio.sleep(duration_s)
        await _ptz_stop(pc)

    # Stop first so any in-flight joystick /start commands don't outlive this move
    await _ptz_stop(pc)
    if pan_dir:
        await _timed_axis(pan_dir, abs(want_pan), "pan")
    if tilt_dir:
        await _timed_axis(tilt_dir, abs(want_tilt), "tilt")

    return {"ok": True, "mode": "timed"}


@router.post("/ptz/{cam_id}/start")
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


@router.post("/ptz/{cam_id}/stop")
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


@router.post("/ptz/{cam_id}/calibrate")
async def ptz_calibrate(cam_id: str, payload: CalibratePayload):
    """Refine per-axis click-to-centre gain from one measured move.

    `before` is where a target sat when a centring click was issued; `after` is
    where that same target ended up. For each axis the fraction of the intended
    move that actually happened is measured in screen space, and the gain is
    scaled so a repeat would land dead centre. Iterating halves the residual
    each pass. Axes with too little travel in this sample are left untouched.
    """
    cam = cameras.get(cam_id)
    if not cam:
        raise HTTPException(404, "Camera not found")
    if not cam.get("ptz"):
        raise HTTPException(400, "Camera is not PTZ")

    # Normalised screen deltas from centre (-1..+1)
    bx = (payload.before.x - 0.5) * 2
    by = (payload.before.y - 0.5) * 2
    ax = (payload.after.x - 0.5) * 2
    ay = (payload.after.y - 0.5) * 2

    MIN_TRAVEL = 0.12   # ignore axes the target barely moved along (too noisy)
    result: dict = {"ok": True, "updated": []}

    for axis, b, a in (("pan", bx, ax), ("tilt", by, ay)):
        if abs(b) < MIN_TRAVEL:
            continue
        moved_fraction = (b - a) / b   # 1.0 = perfect, <1 undershoot, >1 overshoot
        if not (0.25 <= moved_fraction <= 4.0):
            # Wrong direction or wild misclick — don't poison the gain
            result.setdefault("skipped", []).append(axis)
            continue
        key = "pan_cal" if axis == "pan" else "tilt_cal"
        old = max(0.2, min(5.0, float(cam.get(key, 1.0))))
        new = max(0.2, min(5.0, old / moved_fraction))
        cam[key] = round(new, 4)
        result["updated"].append({
            "axis": axis,
            "old": round(old, 4),
            "new": round(new, 4),
            "residual": round(a, 4),   # where it landed; ~0 means centred
        })

    if result["updated"]:
        _save_config()
        logger.info("Calibrate %s: %s", cam_id, result["updated"])

    return result


@router.post("/ptz/{cam_id}/raw_move")
async def ptz_raw_move(cam_id: str, payload: RawMovePayload):
    """Run the motor along one axis for an exact time, bypassing the aim maths.

    Used by auto-calibration to probe how far a known motor-on time actually
    rotates the camera. Honours pan/tilt inversion so the learned numbers apply
    directly to normal click moves.
    """
    cam = cameras.get(cam_id)
    if not cam or not cam.get("ptz"):
        raise HTTPException(404, "Camera not found or not PTZ")

    seconds = max(0.0, min(5.0, float(payload.seconds)))
    d = 1 if payload.dir >= 0 else -1
    if payload.axis == "pan" and cam.get("pan_invert", False):
        d = -d
    if payload.axis == "tilt" and cam.get("tilt_invert", False):
        d = -d

    pc = _ptz_cam(cam)

    if _is_onvif(cam):
        spd = cam.get("ptz_speed", 40) / 63.0
        pan_v  = d * spd if payload.axis == "pan"  else 0.0
        tilt_v = d * spd if payload.axis == "tilt" else 0.0
        await _onvif_stop(pc)
        await _onvif_continuous(pc, pan=pan_v, tilt=tilt_v)
        await asyncio.sleep(seconds)
        await _onvif_stop(pc)
    else:
        op = {("pan", 1): "Right", ("pan", -1): "Left",
              ("tilt", 1): "Up",   ("tilt", -1): "Down"}[(payload.axis, d)]
        speed = cam.get("ptz_speed", 40)
        await _ptz_stop(pc)
        await _nvr_call(pc, "PtzCtrl", [{"cmd": "PtzCtrl", "action": 0, "param": {
            "channel": pc.get("channel", 0), "op": op, "speed": speed, "timeout": 0,
        }}])
        await asyncio.sleep(seconds)
        await _ptz_stop(pc)

    return {"ok": True}


@router.post("/ptz/{cam_id}/set_axis_cal")
async def ptz_set_axis_cal(cam_id: str, payload: AxisCalPayload):
    """Store auto-calibration speed (dps) for one axis.

    Resets that axis's manual gain to 1.0 (dps now fits the motor directly) and
    zeroes the lead field (the acceleration term is no longer used by the move
    model). Does NOT reload go2rtc — that would interrupt the calibration video.
    """
    cam = cameras.get(cam_id)
    if not cam or not cam.get("ptz"):
        raise HTTPException(404, "Camera not found or not PTZ")

    dps = max(0.5, min(300.0, float(payload.dps)))

    if payload.axis == "pan":
        cam["pan_degrees_per_second"] = round(dps, 3)
        cam["pan_cal"] = 1.0
    else:
        cam["tilt_degrees_per_second"] = round(dps, 3)
        cam["tilt_cal"] = 1.0

    _save_config()
    logger.info("Auto-cal %s %s: dps=%.2f", cam_id, payload.axis, dps)
    return {"ok": True, "axis": payload.axis, "dps": round(dps, 3)}


@router.post("/ptz/{cam_id}/reset_cal")
async def ptz_reset_cal(cam_id: str):
    """Clear all aim calibration for a camera back to factory defaults.

    Per-axis speeds revert to the shared ptz_degrees_per_second fallback, gains to
    1.0, and lead to 0 — i.e. the plain proportional move model. Use this to undo
    a bad calibration and get back to a known-good baseline.
    """
    cam = cameras.get(cam_id)
    if not cam or not cam.get("ptz"):
        raise HTTPException(404, "Camera not found or not PTZ")

    for k in ("pan_degrees_per_second", "tilt_degrees_per_second"):
        cam.pop(k, None)
    cam["pan_cal"] = 1.0
    cam["tilt_cal"] = 1.0

    _save_config()
    logger.info("Reset aim calibration for %s", cam_id)
    return {"ok": True}


@router.post("/zoom/{cam_id}")
async def zoom(cam_id: str, payload: ZoomPayload):
    cam = cameras.get(cam_id)
    if not cam:
        raise HTTPException(404, "Camera not found")
    if not cam.get("ptz"):
        raise HTTPException(400, "Camera is not PTZ")

    duration_s = cam.get("zoom_step_s", 0.3) * payload.steps
    pc = _ptz_cam(cam)

    if _is_onvif(cam):
        zoom_v = (cam.get("zoom_speed", 40) / 63.0) * payload.direction
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


# ── Reolink extras: absolute zoom, auto-tracking, guard position ───────────────
# These use Reolink's HTTP API (GetZoomFocus/StartZoomFocus, Get/SetAiCfg,
# Get/SetPtzGuard). GET endpoints return {"supported": False} for cameras that
# aren't Reolink or don't offer the feature, so the UI can hide the control.

def _reolink_ptz(cam_id: str):
    """Return (cam, pc) where pc is the Reolink target, or (cam, None) if this
    camera isn't a Reolink HTTP-API PTZ (e.g. ONVIF/Dahua). For PTZ-only features
    (zoom, tracking, guard, patrol, presets)."""
    cam = cameras.get(cam_id)
    if not cam or not cam.get("ptz"):
        raise HTTPException(404, "Camera not found or not PTZ")
    if _is_onvif(cam) or cam.get("brand", "").lower() != "reolink":
        return cam, None
    return cam, _ptz_cam(cam)


def _reolink_cam(cam_id: str):
    """Return (cam, pc) for any Reolink camera — PTZ or not — or (cam, None) if it
    isn't a Reolink HTTP-API camera. For features that also exist on non-PTZ models
    (floodlight, IR/night-vision, siren, detection)."""
    cam = cameras.get(cam_id)
    if not cam:
        raise HTTPException(404, "Camera not found")
    if _is_onvif(cam) or cam.get("brand", "").lower() != "reolink":
        return cam, None
    return cam, _ptz_cam(cam)


@router.get("/zoom/{cam_id}/pos")
async def get_zoom_pos(cam_id: str):
    cam, pc = _reolink_ptz(cam_id)
    if pc is None:
        return {"supported": False}
    try:
        data = await _nvr_call(pc, "GetZoomFocus", [{"cmd": "GetZoomFocus", "action": 1,
            "param": {"channel": pc.get("channel", 0)}}], timeout=5)
        if data[0].get("code", -1) != 0:
            return {"supported": False}
        zpos = data[0]["value"]["ZoomFocus"]["zoom"]["pos"]
        zrng = data[0]["range"]["ZoomFocus"]["zoom"]["pos"]
        return {"supported": True, "pos": zpos, "min": zrng["min"], "max": zrng["max"]}
    except Exception as e:
        logger.info("GetZoomFocus %s unavailable: %s", cam_id, e)
        return {"supported": False}


@router.post("/zoom/{cam_id}/pos")
async def set_zoom_pos(cam_id: str, payload: ZoomPosPayload):
    cam, pc = _reolink_ptz(cam_id)
    if pc is None:
        raise HTTPException(400, "Absolute zoom not supported on this camera")
    resp = await _nvr_call(pc, "StartZoomFocus", [{"cmd": "StartZoomFocus", "action": 0,
        "param": {"ZoomFocus": {"channel": pc.get("channel", 0), "op": "ZoomPos",
                                "pos": int(payload.pos)}}}])
    return {"ok": resp[0].get("code", -1) == 0, "pos": int(payload.pos)}


@router.get("/ai/{cam_id}")
async def get_ai(cam_id: str):
    cam, pc = _reolink_ptz(cam_id)
    if pc is None:
        return {"supported": False}
    try:
        data = await _nvr_call(pc, "GetAiCfg", [{"cmd": "GetAiCfg", "action": 1,
            "param": {"channel": pc.get("channel", 0)}}], timeout=5)
        if data[0].get("code", -1) != 0:
            return {"supported": False}
        return {"supported": True, "enabled": int(data[0]["value"].get("aiTrack", 0)) != 0}
    except Exception as e:
        logger.info("GetAiCfg %s unavailable: %s", cam_id, e)
        return {"supported": False}


@router.post("/ai/{cam_id}")
async def set_ai(cam_id: str, payload: TogglePayload):
    cam, pc = _reolink_ptz(cam_id)
    if pc is None:
        raise HTTPException(400, "Auto-tracking not supported on this camera")
    # Echo the full config block back with aiTrack flipped (SetAiCfg needs the whole value).
    data = await _nvr_call(pc, "GetAiCfg", [{"cmd": "GetAiCfg", "action": 1,
        "param": {"channel": pc.get("channel", 0)}}], timeout=5)
    value = data[0]["value"]
    value["aiTrack"] = 4 if payload.enable else 0
    resp = await _nvr_call(pc, "SetAiCfg", [{"cmd": "SetAiCfg", "action": 0, "param": value}])
    return {"ok": resp[0].get("code", -1) == 0, "enabled": payload.enable}


@router.get("/guard/{cam_id}")
async def get_guard(cam_id: str):
    cam, pc = _reolink_ptz(cam_id)
    if pc is None:
        return {"supported": False}
    try:
        data = await _nvr_call(pc, "GetPtzGuard", [{"cmd": "GetPtzGuard", "action": 1,
            "param": {"channel": pc.get("channel", 0)}}], timeout=5)
        if data[0].get("code", -1) != 0:
            return {"supported": False}
        g = data[0]["value"]["PtzGuard"]
        return {"supported": True, "enabled": int(g.get("benable", 0)) == 1,
                "timeout": g.get("timeout", 60), "has_pos": int(g.get("bexistPos", 0)) == 1}
    except Exception as e:
        logger.info("GetPtzGuard %s unavailable: %s", cam_id, e)
        return {"supported": False}


@router.post("/guard/{cam_id}")
async def set_guard(cam_id: str, payload: GuardPayload):
    cam, pc = _reolink_ptz(cam_id)
    if pc is None:
        raise HTTPException(400, "Guard position not supported on this camera")
    ch = pc.get("channel", 0)
    cur = await _nvr_call(pc, "GetPtzGuard", [{"cmd": "GetPtzGuard", "action": 1,
        "param": {"channel": ch}}], timeout=5)
    g = cur[0]["value"]["PtzGuard"]
    benable = (1 if payload.enable else 0) if payload.enable is not None else g.get("benable", 0)
    timeout = int(payload.timeout) if payload.timeout is not None else g.get("timeout", 60)
    resp = await _nvr_call(pc, "SetPtzGuard", [{"cmd": "SetPtzGuard", "action": 0, "param": {
        "PtzGuard": {"channel": ch, "cmdStr": "setPos", "benable": benable,
                     "timeout": timeout, "bSaveCurrentPos": 1 if payload.save_pos else 0}}}])
    return {"ok": resp[0].get("code", -1) == 0, "enabled": benable == 1, "timeout": timeout}


# ── Lights: floodlight (WhiteLed) + night-vision (IrLights) ────────────────────

@router.get("/light/{cam_id}")
async def get_light(cam_id: str):
    cam, pc = _reolink_cam(cam_id)
    if pc is None:
        return {"supported": False}
    ch = pc.get("channel", 0)
    out = {"supported": False}
    try:
        w = await _nvr_call(pc, "GetWhiteLed", [{"cmd": "GetWhiteLed", "action": 1,
            "param": {"channel": ch}}], timeout=5)
        if w[0].get("code", -1) == 0:
            wl = w[0]["value"]["WhiteLed"]
            out["white"] = {"on": int(wl.get("state", 0)) == 1, "bright": wl.get("bright", 100)}
            out["supported"] = True
    except Exception as e:
        logger.info("GetWhiteLed %s unavailable: %s", cam_id, e)
    try:
        r = await _nvr_call(pc, "GetIrLights", [{"cmd": "GetIrLights", "action": 1,
            "param": {"channel": ch}}], timeout=5)
        if r[0].get("code", -1) == 0:
            out["ir"] = {"state": r[0]["value"]["IrLights"].get("state", "Auto")}
            out["supported"] = True
    except Exception as e:
        logger.info("GetIrLights %s unavailable: %s", cam_id, e)
    return out


@router.post("/light/{cam_id}/white")
async def set_white(cam_id: str, payload: WhiteLedPayload):
    cam, pc = _reolink_cam(cam_id)
    if pc is None:
        raise HTTPException(400, "Floodlight not supported on this camera")
    ch = pc.get("channel", 0)
    # Read-modify-write so mode / schedule / AI types are preserved.
    cur = await _nvr_call(pc, "GetWhiteLed", [{"cmd": "GetWhiteLed", "action": 1,
        "param": {"channel": ch}}], timeout=5)
    wl = cur[0]["value"]["WhiteLed"]
    wl["channel"] = ch
    wl["state"] = 1 if payload.enable else 0
    if payload.bright is not None:
        wl["bright"] = max(0, min(100, int(payload.bright)))
    resp = await _nvr_call(pc, "SetWhiteLed", [{"cmd": "SetWhiteLed", "action": 0,
        "param": {"WhiteLed": wl}}])
    return {"ok": resp[0].get("code", -1) == 0, "on": payload.enable, "bright": wl.get("bright")}


@router.post("/light/{cam_id}/ir")
async def set_ir(cam_id: str, payload: IrPayload):
    cam, pc = _reolink_cam(cam_id)
    if pc is None:
        raise HTTPException(400, "IR control not supported on this camera")
    state = payload.state if payload.state in ("Auto", "On", "Off") else "Auto"
    resp = await _nvr_call(pc, "SetIrLights", [{"cmd": "SetIrLights", "action": 0,
        "param": {"IrLights": {"channel": pc.get("channel", 0), "state": state}}}])
    return {"ok": resp[0].get("code", -1) == 0, "state": state}


# ── Siren (manual audio alarm) — only where the camera advertises support ──────

@router.get("/siren/{cam_id}")
async def get_siren(cam_id: str):
    cam, pc = _reolink_cam(cam_id)
    if pc is None:
        return {"supported": False}
    try:
        ab = await _nvr_call(pc, "GetAbility", [{"cmd": "GetAbility", "action": 0,
            "param": {"User": {"userName": pc.get("username", "admin")}}}], timeout=5)
        chn = ab[0]["value"]["Ability"].get("abilityChn", [])
        ch = pc.get("channel", 0)
        permit = chn[ch].get("supportAudioAlarm", {}).get("permit", 0) if ch < len(chn) else 0
        return {"supported": permit != 0}
    except Exception as e:
        logger.info("Siren ability %s unavailable: %s", cam_id, e)
        return {"supported": False}


@router.post("/siren/{cam_id}")
async def set_siren(cam_id: str, payload: SirenPayload):
    cam, pc = _reolink_cam(cam_id)
    if pc is None:
        raise HTTPException(400, "Siren not supported on this camera")
    resp = await _nvr_call(pc, "AudioAlarmPlay", [{"cmd": "AudioAlarmPlay", "action": 0,
        "param": {"alarm_mode": "manual", "manual_switch": 1 if payload.on else 0,
                  "times": 1, "channel": pc.get("channel", 0)}}])
    return {"ok": resp[0].get("code", -1) == 0, "on": payload.on}


# ── Patrol / auto-cruise ──────────────────────────────────────────────────────

@router.post("/patrol/{cam_id}")
async def set_patrol(cam_id: str, payload: PatrolPayload):
    cam, pc = _reolink_ptz(cam_id)
    if pc is None:
        raise HTTPException(400, "Patrol not supported on this camera")
    op = "Auto" if payload.start else "Stop"
    resp = await _nvr_call(pc, "PtzCtrl", [{"cmd": "PtzCtrl", "action": 0, "param": {
        "channel": pc.get("channel", 0), "op": op, "speed": cam.get("ptz_speed", 20)}}])
    return {"ok": resp[0].get("code", -1) == 0, "running": payload.start}


# ── Save current view as a preset ─────────────────────────────────────────────

@router.post("/preset/{cam_id}/save")
async def save_preset(cam_id: str, payload: SavePresetPayload):
    cam, pc = _reolink_ptz(cam_id)
    if pc is None:
        raise HTTPException(400, "Presets not supported on this camera")
    name = payload.name or f"Preset {payload.id}"
    resp = await _nvr_call(pc, "SetPtzPreset", [{"cmd": "SetPtzPreset", "action": 0, "param": {
        "PtzPreset": {"channel": pc.get("channel", 0), "enable": 1,
                      "id": int(payload.id), "name": name}}}])
    preset_cache.pop(cam_id, None)   # force preset bar to reload
    return {"ok": resp[0].get("code", -1) == 0, "id": payload.id, "name": name}


@router.get("/presets/{cam_id}")
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
            resp = data[0]
            if resp.get("code", -1) != 0 or "value" not in resp:
                logger.warning(
                    "GetPresets %s: NVR returned code=%s (camera may still be booting)",
                    cam_id, resp.get("code", "?"),
                )
                preset_cache[cam_id] = []
                return []
            raw = resp["value"]["PtzPreset"]
            result = [
                {"id": str(int(p["id"])), "name": p.get("name") or f"Preset {p['id']}"}
                for p in raw
                if p.get("enable", 1)
            ]
        preset_cache[cam_id] = result
        return result
    except Exception as e:
        logger.error("GetPresets failed for %s: %s", cam_id, e)
        return []


@router.post("/preset/{cam_id}/{preset_id}")
async def goto_preset(cam_id: str, preset_id: str):
    cam = cameras.get(cam_id)
    if not cam or not cam.get("ptz"):
        raise HTTPException(404, "Camera not found or not PTZ")
    pc = _ptz_cam(cam)

    if _is_onvif(cam):
        try:
            await _onvif_goto_preset(pc, preset_id)
            logger.info("Preset ONVIF %s token=%r: ok", cam_id, preset_id)
            # Camera moved to an unknown absolute position; invalidate so next click
            # doesn't use stale tracking and get an incorrect absolute target
            if cam.get("brand", "").lower() == "dahua":
                _dahua_tracked_pos.pop(cam_id, None)
            return {"ok": True, "preset": preset_id}
        except Exception as e:
            logger.error("Preset ONVIF %s token=%r: %s", cam_id, preset_id, e)
            return {"ok": False, "error": str(e)}

    pid = int(preset_id)
    if "ptz_ip" in cam:
        resp = await _nvr_call(pc, "GoPtzPreset", [{"cmd": "GoPtzPreset", "action": 0,
            "param": {"channel": 0, "id": pid}}])
        logger.info("Preset %s #%d (direct): %s", cam_id, pid, resp)
        return {"ok": True, "preset": pid}

    channel = pc.get("channel", 0)
    speed = cam.get("ptz_speed", 40)
    resp = await _nvr_call(pc, "PtzCtrl", [{"cmd": "PtzCtrl", "action": 0, "param": {
        "channel": channel, "op": "ToPos", "id": pid, "speed": speed,
    }}])
    code = resp[0].get("code", -99)
    logger.info("Preset %s #%d ToPos: code=%d", cam_id, pid, code)
    return {"ok": code == 0, "preset": pid}
