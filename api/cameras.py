#
# Version: 1.1
# Updated: 2026-10-11
#
# v1.0 - 2026-05-18 - Initial extraction from ptzlite.py monolith
# v1.1 - 2026-10-11 - Never return camera passwords to the browser: mask on read,
#                     keep the stored password when the mask is sent back on save
#

import asyncio
import logging

from fastapi import APIRouter, HTTPException, Request

from core.config import _config, _save_config, cameras
from core.paths import GO2RTC_PORT
from services.go2rtc import _reload_go2rtc
from services.ptz import preset_cache
from services.ptz.reolink import _nvr_key, _tokens

router = APIRouter()
logger = logging.getLogger("PTZLite.api.cameras")

# Returned in place of a real camera password. Sending it back on save means
# "unchanged". An empty string stays a genuine empty password.
PW_MASK = "••••••••"


def _masked(cam: dict) -> dict:
    out = dict(cam)
    if out.get("password"):
        out["password"] = PW_MASK
    return out


def _drop_masked_password(data: dict) -> dict:
    """Remove a masked password from an incoming payload so the stored one is kept."""
    if data.get("password") == PW_MASK:
        data = {k: v for k, v in data.items() if k != "password"}
    return data


@router.get("/cameras")
def list_cameras():
    return [
        {
            "id": c["id"],
            "name": c["name"],
            "brand": c.get("brand", "reolink"),
            "ptz": bool(c.get("ptz", False)),
            "monitor_preset": c.get("monitor_preset"),
            "page": c.get("page") or None,
            "crop": bool(c.get("crop", False)),
            "go2rtc_port": GO2RTC_PORT,
        }
        for c in _config["cameras"]
        if c.get("enabled", True)    # disabled cameras are hidden from the viewer
    ]


@router.get("/api/cameras")
def api_list_cameras():
    return [_masked(c) for c in _config["cameras"]]


@router.post("/api/cameras", status_code=201)
async def api_add_camera(request: Request):
    data = await request.json()
    cam_id = data.get("id", "").strip()
    if not cam_id:
        raise HTTPException(400, "id is required")
    if any(c["id"] == cam_id for c in _config["cameras"]):
        raise HTTPException(409, f"Camera '{cam_id}' already exists")
    if data.get("password") == PW_MASK:
        data["password"] = ""
    _config["cameras"].append(data)
    _save_config()
    await asyncio.get_running_loop().run_in_executor(None, _reload_go2rtc)
    return _masked(data)


@router.put("/api/cameras/{cam_id}")
async def api_update_camera(cam_id: str, request: Request):
    data = _drop_masked_password(await request.json())
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
    if old_key != _nvr_key(merged) or merged.get("password") != old_cam.get("password"):
        _tokens.pop(old_key, None)

    await asyncio.get_running_loop().run_in_executor(None, _reload_go2rtc)
    return _masked(merged)


@router.patch("/api/cameras/{cam_id}")
async def api_patch_camera(cam_id: str, request: Request):
    """Partial update — only the supplied fields are changed (e.g. {"enabled": false})."""
    data = _drop_masked_password(await request.json())
    idx = next((i for i, c in enumerate(_config["cameras"]) if c["id"] == cam_id), None)
    if idx is None:
        raise HTTPException(404, f"Camera '{cam_id}' not found")
    merged = {**_config["cameras"][idx], **data}
    _config["cameras"][idx] = merged
    _save_config()
    # Reload go2rtc so enabled/disabled state is reflected in stream registration
    await asyncio.get_running_loop().run_in_executor(None, _reload_go2rtc)
    return _masked(merged)


@router.delete("/api/cameras/{cam_id}", status_code=204)
async def api_delete_camera(cam_id: str):
    idx = next((i for i, c in enumerate(_config["cameras"]) if c["id"] == cam_id), None)
    if idx is None:
        raise HTTPException(404, f"Camera '{cam_id}' not found")
    _config["cameras"].pop(idx)
    _save_config()
    preset_cache.pop(cam_id, None)
    await asyncio.get_running_loop().run_in_executor(None, _reload_go2rtc)


@router.post("/api/cameras/reorder")
async def api_reorder_cameras(request: Request):
    ids: list = await request.json()
    id_set = {c["id"] for c in _config["cameras"]}
    if set(ids) != id_set:
        raise HTTPException(400, "IDs must match the current camera set exactly")
    order = {cid: i for i, cid in enumerate(ids)}
    _config["cameras"].sort(key=lambda c: order.get(c["id"], 9999))
    _save_config()
    return {"ok": True}


@router.get("/api/config/export")
def api_export_config():
    # Deliberately unmasked: this is the user's full backup (behind login).
    return {"cameras": _config["cameras"]}


@router.post("/api/config/import")
async def api_import_config(request: Request):
    data = await request.json()
    cams = data.get("cameras")
    if not isinstance(cams, list):
        raise HTTPException(400, 'Expected {"cameras": [...]}')
    for cam in cams:
        if not cam.get("id") or not cam.get("name"):
            raise HTTPException(400, "Every camera must have an id and name")
    _config["cameras"] = cams
    _save_config()
    preset_cache.clear()
    await asyncio.get_running_loop().run_in_executor(None, _reload_go2rtc)
    return {"ok": True, "count": len(cams)}
