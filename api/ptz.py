#
# Version: 2.5
# Updated: 2026-10-12
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
# v2.4 - 2026-10-11 - Save preset picks the next free slot when no id is given
# v2.5 - 2026-10-12 - Click-to-centre: zoom-aware aim (zoom_max_x + live zoom read),
#                     pan+tilt move together, zoom calibration, /ptz/{id}/aim
#

import asyncio
import logging
import math
import time

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from core.config import _save_config, cameras
from services.ptz import _is_onvif, _ptz_cam, preset_cache
from services.ptz.dahua import _dahua_get_position, _dahua_position_abs, _dahua_tracked_pos
from services.ptz.onvif import (
    _onvif_continuous,
    _onvif_get_presets,
    _onvif_get_zoom,
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
    id: int | None = None   # None = use the next free slot
    name: str = ""


def _screen_delta_to_angles(cam: dict, dx: float, dy: float, mag: float = 1.0) -> tuple[float, float]:
    """Convert normalised screen delta to pan/tilt degrees.

    `mag` is the current optical magnification (1 = fully zoomed out): zooming
    in narrows the field of view, so the same screen distance is a smaller angle.
    """
    fov_h = float(cam.get("fov_h", 90))
    fov_v = float(cam.get("fov_v", 55))

    pan = math.degrees(math.atan(dx * math.tan(math.radians(fov_h) / 2) / mag))
    tilt = -math.degrees(math.atan(dy * math.tan(math.radians(fov_v) / 2) / mag))
    return pan, tilt


# ── Optical zoom ──────────────────────────────────────────────────────────────
# Detected per camera at runtime: Reolink reports its zoom motor position via
# GetZoomFocus, ONVIF cameras via GetStatus. `zoom_max_x` (the camera's optical
# zoom at full zoom-in, e.g. 4 for a 4x lens) is entered in Settings and refined
# by running Manual calibration while fully zoomed in. 1 = don't adjust for zoom.

_ZOOM_RETRY_S = 600.0
_zoom_unsupported: dict[str, float] = {}   # cam id -> when its zoom read last failed


def _zoom_max_x(cam: dict) -> float:
    return max(1.0, min(60.0, float(cam.get("zoom_max_x", 1.0) or 1.0)))


def _zoom_mag(cam: dict, frac: float | None) -> float:
    """Magnification at zoom fraction `frac` (0 = wide, 1 = full zoom).

    Assumes focal length is roughly linear in the zoom motor position, which
    is close for typical PTZ zoom lenses; calibration at full zoom pins the end.
    """
    if frac is None:
        return 1.0
    return 1.0 + (_zoom_max_x(cam) - 1.0) * frac


async def _zoom_fraction(cam: dict) -> float | None:
    """Current optical zoom as 0 (wide) .. 1 (full), or None if unknown."""
    failed = _zoom_unsupported.get(cam["id"])
    if failed and time.monotonic() - failed < _ZOOM_RETRY_S:
        return None
    pc = _ptz_cam(cam)
    frac = None
    try:
        if _is_onvif(cam):
            frac = await _onvif_get_zoom(pc)
        else:
            data = await _nvr_call(pc, "GetZoomFocus", [{"cmd": "GetZoomFocus", "action": 1,
                "param": {"channel": pc.get("channel", 0)}}], timeout=3)
            if data[0].get("code", -1) == 0:
                pos = data[0]["value"]["ZoomFocus"]["zoom"]["pos"]
                rng = data[0]["range"]["ZoomFocus"]["zoom"]["pos"]
                span = rng["max"] - rng["min"]
                if span > 0:
                    frac = (pos - rng["min"]) / span
    except Exception as e:
        logger.debug("Zoom read %s unavailable: %s", cam["id"], e)
    if frac is None:
        _zoom_unsupported[cam["id"]] = time.monotonic()
        return None
    _zoom_unsupported.pop(cam["id"], None)
    return max(0.0, min(1.0, float(frac)))


async def _current_mag(cam: dict) -> float:
    """Current magnification; skips the zoom read when no zoom is configured."""
    if _zoom_max_x(cam) <= 1.0:
        return 1.0
    return _zoom_mag(cam, await _zoom_fraction(cam))


async def _sleep_until(deadline: float) -> None:
    await asyncio.sleep(max(0.0, deadline - time.monotonic()))


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

    mag = await _current_mag(cam)
    want_pan, want_tilt = _screen_delta_to_angles(cam, dx, dy, mag)

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

        def _onvif_axis(degrees: float, axis: str) -> tuple[float, float]:
            """(signed velocity, motor-on seconds) for one axis, or (0, 0) if no move."""
            if abs(degrees) <= 0.5:
                return 0.0, 0.0
            axis_fov = fov_h if axis == "pan" else fov_v
            magnitude = min(1.0, abs(degrees) / (axis_fov / 2))
            spd = max(0.2, min(1.0, magnitude)) * max_spd
            duration_s = max(_min_move_s(cam),
                             _move_duration(cam, axis, abs(degrees)) - ONVIF_STOP_LEAD_S)
            return math.copysign(spd, degrees), duration_s

        pan_v, pan_s = _onvif_axis(want_pan, "pan")
        tilt_v, tilt_s = _onvif_axis(want_tilt, "tilt")
        logger.info(
            "PTZ ONVIF %s: click=(%.2f,%.2f) zoom=%.2fx pan=%.1f° (%.2fs) tilt=%.1f° (%.2fs)",
            cam["id"], click.x, click.y, mag, want_pan, pan_s, want_tilt, tilt_s,
        )
        # Stop first so any in-flight joystick /start commands don't outlive this move
        await _onvif_stop(pc)
        if pan_s or tilt_s:
            # Both axes run together; the shorter one drops out, the longer finishes.
            await _onvif_continuous(pc, pan=pan_v, tilt=tilt_v)
            t0 = time.monotonic()
            if pan_s and tilt_s and pan_s != tilt_s:
                await _sleep_until(t0 + min(pan_s, tilt_s))
                if pan_s > tilt_s:
                    await _onvif_continuous(pc, pan=pan_v)
                else:
                    await _onvif_continuous(pc, tilt=tilt_v)
            await _sleep_until(t0 + max(pan_s, tilt_s))
            await _onvif_stop(pc)
        return {"ok": True, "mode": "onvif_timed"}

    sx = (1 if want_pan  > 0.5 else (-1 if want_pan  < -0.5 else 0))
    sy = (1 if want_tilt > 0.5 else (-1 if want_tilt < -0.5 else 0))
    if cam.get("pan_invert",  False): sx = -sx
    if cam.get("tilt_invert", False): sy = -sy

    if sx == 0 and sy == 0:
        return {"ok": True, "note": "delta too small"}

    pan_s  = max(_min_move_s(cam), _move_duration(cam, "pan",  abs(want_pan)))  if sx else 0.0
    tilt_s = max(_min_move_s(cam), _move_duration(cam, "tilt", abs(want_tilt))) if sy else 0.0
    legs = _reolink_legs(sx, sy, pan_s, tilt_s)

    logger.info(
        "PTZ %s: click=(%.2f,%.2f) zoom=%.2fx want_pan=%.1f° (%.2fs) want_tilt=%.1f° (%.2fs) legs=%s",
        cam["id"], click.x, click.y, mag, want_pan, pan_s, want_tilt, tilt_s, legs,
    )

    # Stop first so any in-flight joystick /start commands don't outlive this move
    await _ptz_stop(pc)
    t0 = None
    for op, until in legs:
        await _nvr_call(pc, "PtzCtrl", [{"cmd": "PtzCtrl", "action": 0, "param": {
            "channel": pc.get("channel", 0), "op": op, "speed": cam.get("ptz_speed", 20), "timeout": 0,
        }}])
        if t0 is None:
            t0 = time.monotonic()   # times are measured from when the move started
        await _sleep_until(t0 + until)
    await _ptz_stop(pc)

    return {"ok": True, "mode": "timed"}


def _reolink_legs(sx: int, sy: int, pan_s: float, tilt_s: float) -> list[tuple[str, float]]:
    """Plan a timed Reolink move as [(op, run-until seconds), ...].

    Pan and tilt run together (a diagonal op) until the shorter axis has had its
    time, then the longer axis carries on alone. Each axis gets exactly the same
    motor-on time it would get moving on its own, so calibration still applies.
    """
    if sx and sy:
        both = min(pan_s, tilt_s)
        legs = [(_DIR[(sx, sy)], both)]
        if pan_s > both:
            legs.append((_DIR[(sx, 0)], pan_s))
        elif tilt_s > both:
            legs.append((_DIR[(0, sy)], tilt_s))
        return legs
    if sx:
        return [(_DIR[(sx, 0)], pan_s)]
    return [(_DIR[(0, sy)], tilt_s)]


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

    # Zoomed in: the miss is down to the zoom figure, not the motor — refine
    # zoom_max_x instead of the per-axis gains (which are measured zoomed out).
    frac = await _zoom_fraction(cam)
    if frac is not None and frac >= 0.5:
        return _calibrate_zoom(cam, frac, ((bx, ax), (by, ay)), MIN_TRAVEL)
    if frac is not None and frac > 0.15:
        result["zoom_partial"] = True   # part-zoomed: neither measurement is clean
        return result

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


def _calibrate_zoom(cam: dict, frac: float, samples, min_travel: float) -> dict:
    """Refine zoom_max_x from one measured move made while zoomed in.

    With the motor calibrated, the fraction of the intended screen distance the
    target actually travelled equals true ÷ assumed magnification, so the new
    magnification is simply old × moved fraction (averaged over both axes).
    """
    fracs, residual = [], 0.0
    for b, a in samples:
        if abs(b) < min_travel:
            continue
        moved = (b - a) / b
        if 0.1 <= moved <= 30.0:     # wrong direction or a wild misclick is ignored
            fracs.append(moved)
            residual = max(residual, abs(a))
    if not fracs:
        return {"ok": True, "updated": []}
    moved = math.prod(fracs) ** (1 / len(fracs))
    old_x = _zoom_max_x(cam)
    new_mag = max(1.0, _zoom_mag(cam, frac) * moved)
    new_x = max(1.0, min(60.0, 1.0 + (new_mag - 1.0) / frac))
    cam["zoom_max_x"] = round(new_x, 3)
    _save_config()
    logger.info("Calibrate %s zoom: %.3fx -> %.3fx (at %.0f%% zoom)", cam["id"], old_x, new_x, frac * 100)
    return {"ok": True, "updated": [{"axis": "zoom", "old": round(old_x, 3),
                                     "new": round(new_x, 3), "residual": round(residual, 4)}]}


@router.get("/ptz/{cam_id}/aim")
async def ptz_aim_info(cam_id: str):
    """Field of view right now, allowing for optical zoom (used by auto-calibration)."""
    cam = cameras.get(cam_id)
    if not cam or not cam.get("ptz"):
        raise HTTPException(404, "Camera not found or not PTZ")
    frac = await _zoom_fraction(cam)
    mag = _zoom_mag(cam, frac)

    def eff(fov: float) -> float:
        return math.degrees(2 * math.atan(math.tan(math.radians(fov) / 2) / mag))
    return {"fov_h": round(eff(float(cam.get("fov_h", 90))), 3),
            "fov_v": round(eff(float(cam.get("fov_v", 55))), 3),
            "zoom": None if frac is None else round(frac, 3), "mag": round(mag, 3)}


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
    ch = pc.get("channel", 0)
    preset_id = payload.id
    if preset_id is None:
        data = await _nvr_call(pc, "GetPtzPreset", [{"cmd": "GetPtzPreset", "action": 0,
            "param": {"channel": ch}}], timeout=5)
        slots = data[0].get("value", {}).get("PtzPreset", []) if data[0].get("code", -1) == 0 else []
        preset_id = _next_free_preset(slots)
        if preset_id is None:
            raise HTTPException(409, "All preset slots on this camera are in use")
    name = (payload.name or f"Preset {preset_id}")[:31]   # Reolink name limit
    resp = await _nvr_call(pc, "SetPtzPreset", [{"cmd": "SetPtzPreset", "action": 0, "param": {
        "PtzPreset": {"channel": ch, "enable": 1, "id": int(preset_id), "name": name}}}])
    preset_cache.pop(cam_id, None)   # force preset bar to reload
    return {"ok": resp[0].get("code", -1) == 0, "id": preset_id, "name": name}


def _next_free_preset(slots: list) -> int | None:
    """Lowest slot id the camera reports as unused (enable == 0)."""
    free = sorted(int(p["id"]) for p in slots if not p.get("enable") and "id" in p)
    return free[0] if free else None


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
