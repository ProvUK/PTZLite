#!/usr/bin/env python3
# Version: 1.1
# Updated: 2026-04-28
# v1.0 - 2026-04-28 - Initial stable release: go2rtc streaming, PTZ click-to-centre,
#                     sequential pan+tilt, optical zoom, presets, serialized NVR API
# v1.1 - 2026-04-28 - Add camera management API (GET/POST/PUT/DELETE /api/cameras)
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
import math
import subprocess
import sys
import time
from pathlib import Path

import aiohttp
import uvicorn
import yaml
from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

BASE = Path(__file__).parent
CONFIG_PATH = BASE / "config.yaml"
GO2RTC_BIN = BASE / "go2rtc.exe"
GO2RTC_PORT = 1984

app = FastAPI(title="Reolink Viewer")

# ── Config ─────────────────────────────────────────────────────────────────────

def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)

_config = load_config()
cameras: dict[str, dict] = {c["id"]: c for c in _config["cameras"]}

# ── go2rtc lifecycle ───────────────────────────────────────────────────────────

_go2rtc_proc: subprocess.Popen | None = None


def _rtsp_url(cam: dict) -> str:
    ch = cam.get("channel", 0)
    stream = cam.get("stream", "sub")
    return (
        f"rtsp://{cam['username']}:{cam['password']}"
        f"@{cam['ip']}:554/h264Preview_{ch + 1:02d}_{stream}"
    )


def start_go2rtc():
    global _go2rtc_proc

    if not GO2RTC_BIN.exists():
        print(
            f"\n  ERROR: go2rtc.exe not found at {GO2RTC_BIN}\n"
            "  Download from https://github.com/AlexxIT/go2rtc/releases\n"
            "  and place go2rtc.exe next to app.py\n"
        )
        sys.exit(1)

    streams = {cam["id"]: _rtsp_url(cam) for cam in _config["cameras"]}
    go2rtc_cfg = {
        "streams": streams,
        "api": {"listen": f":{GO2RTC_PORT}"},
        "log": {"level": "debug"},
        "rtsp": {"listen": ""},
    }

    cfg_file = BASE / "go2rtc_generated.yaml"
    with open(cfg_file, "w") as f:
        yaml.dump(go2rtc_cfg, f)

    _go2rtc_proc = subprocess.Popen(
        [str(GO2RTC_BIN), "-config", str(cfg_file)],
        stdout=subprocess.DEVNULL,
    )
    atexit.register(stop_go2rtc)

    import urllib.request
    for _ in range(20):
        try:
            urllib.request.urlopen(f"http://localhost:{GO2RTC_PORT}/api/streams", timeout=1)
            break
        except Exception:
            time.sleep(0.3)
    else:
        print("WARNING: go2rtc did not start in time — streams may not work immediately")


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

    pc = _ptz_cam(cam)
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

    op = "ZoomInc" if payload.direction > 0 else "ZoomDec"
    zoom_speed = cam.get("zoom_speed", 40)
    duration_s = cam.get("zoom_step_s", 0.3) * payload.steps

    pc = _ptz_cam(cam)
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
        data = await _nvr_call(pc, "GetPtzPreset", [{"cmd": "GetPtzPreset", "action": 0,
            "param": {"channel": pc.get("channel", 0)}}], timeout=5)
        raw = data[0]["value"]["PtzPreset"]
        result = [
            {"id": int(p["id"]), "name": p.get("name") or f"Preset {p['id']}"}
            for p in raw
            if p.get("enable", 1)
        ]
        _preset_cache[cam_id] = result
        return result
    except Exception as e:
        print(f"GetPtzPreset failed for {cam_id}: {e}")
        return []


@app.post("/preset/{cam_id}/{preset_id}")
async def goto_preset(cam_id: str, preset_id: int):
    cam = cameras.get(cam_id)
    if not cam or not cam.get("ptz"):
        raise HTTPException(404, "Camera not found or not PTZ")
    pc = _ptz_cam(cam)

    if "ptz_ip" in cam:
        resp = await _nvr_call(pc, "GoPtzPreset", [{"cmd": "GoPtzPreset", "action": 0,
            "param": {"channel": 0, "id": preset_id}}])
        print(f"Preset {cam_id} #{preset_id} (direct): {resp}")
        return {"ok": True, "preset": preset_id}

    channel = pc.get("channel", 0)
    speed = cam.get("ptz_speed", 40)
    resp = await _nvr_call(pc, "PtzCtrl", [{"cmd": "PtzCtrl", "action": 0, "param": {
        "channel": channel, "op": "ToPos", "id": preset_id, "speed": speed,
    }}])
    code = resp[0].get("code", -99)
    print(f"Preset {cam_id} #{preset_id} ToPos: code={code}")
    return {"ok": code == 0, "preset": preset_id}

# ── Camera list ────────────────────────────────────────────────────────────────

@app.get("/cameras")
def list_cameras():
    return [
        {
            "id": c["id"],
            "name": c["name"],
            "ptz": bool(c.get("ptz", False)),
            "monitor_preset": c.get("monitor_preset"),
            "go2rtc_port": GO2RTC_PORT,
        }
        for c in _config["cameras"]
    ]

# ── Camera management API ──────────────────────────────────────────────────────

def _save_config():
    """Write current _config to config.yaml and rebuild the cameras dict."""
    with open(CONFIG_PATH, "w") as f:
        yaml.dump(_config, f, default_flow_style=False, allow_unicode=True)
    cameras.clear()
    cameras.update({c["id"]: c for c in _config["cameras"]})


async def _go2rtc_put_stream(cam: dict):
    url = f"http://localhost:{GO2RTC_PORT}/api/streams/{cam['id']}"
    try:
        async with aiohttp.ClientSession() as s:
            await s.put(url, params={"src": _rtsp_url(cam)})
    except Exception as e:
        print(f"go2rtc stream update failed for {cam['id']}: {e}")


async def _go2rtc_delete_stream(cam_id: str):
    url = f"http://localhost:{GO2RTC_PORT}/api/streams/{cam_id}"
    try:
        async with aiohttp.ClientSession() as s:
            await s.delete(url)
    except Exception as e:
        print(f"go2rtc stream delete failed for {cam_id}: {e}")


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
    await _go2rtc_put_stream(data)
    return data


@app.put("/api/cameras/{cam_id}")
async def api_update_camera(cam_id: str, request: Request):
    data = await request.json()
    idx = next((i for i, c in enumerate(_config["cameras"]) if c["id"] == cam_id), None)
    if idx is None:
        raise HTTPException(404, f"Camera '{cam_id}' not found")

    new_id = data.get("id", cam_id).strip()
    # If the ID changed, check for conflicts
    if new_id != cam_id and any(c["id"] == new_id for c in _config["cameras"]):
        raise HTTPException(409, f"Camera '{new_id}' already exists")

    old_cam = _config["cameras"][idx]
    _config["cameras"][idx] = data

    _save_config()

    # Update go2rtc: remove old stream name if ID changed, add new
    if new_id != cam_id:
        await _go2rtc_delete_stream(cam_id)
    await _go2rtc_put_stream(data)

    # Invalidate cached token if connection details changed
    old_key = _nvr_key(old_cam)
    new_key = _nvr_key(data)
    if old_key != new_key:
        _tokens.pop(old_key, None)

    return data


@app.delete("/api/cameras/{cam_id}", status_code=204)
async def api_delete_camera(cam_id: str):
    idx = next((i for i, c in enumerate(_config["cameras"]) if c["id"] == cam_id), None)
    if idx is None:
        raise HTTPException(404, f"Camera '{cam_id}' not found")
    _config["cameras"].pop(idx)
    _save_config()
    await _go2rtc_delete_stream(cam_id)
    _preset_cache.pop(cam_id, None)


# ── Static files ───────────────────────────────────────────────────────────────

app.mount("/", StaticFiles(directory=BASE / "static", html=True), name="static")

# ── Entry point ────────────────────────────────────────────────────────────────

def main():
    port = 8765
    for i, arg in enumerate(sys.argv[1:]):
        if arg in ("--port", "-p") and i + 2 < len(sys.argv):
            port = int(sys.argv[i + 2])

    try:
        start_go2rtc()
        print(f"Reolink viewer → http://localhost:{port}")
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
