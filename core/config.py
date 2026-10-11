#
# Version: 1.4
# Updated: 2026-05-23
#
# v1.0 - 2026-05-18 - Initial extraction from ptzlite.py monolith
# v1.1 - 2026-05-23 - Add reversible improved PTZ aim calibration fields
# v1.2 - 2026-05-23 - Add configurable PTZ timed-move minimum duration
# v1.3 - 2026-05-23 - Add per-axis click-to-centre calibration gains (pan_cal/tilt_cal)
# v1.4 - 2026-05-23 - Add per-axis acceleration deadtime (pan_lead_s/tilt_lead_s) for auto-cal
#

import logging
from typing import Optional

import yaml
from pydantic import BaseModel, ValidationError

from core.paths import CONFIG_PATH

logger = logging.getLogger("PTZLite.core.config")


class CameraConfig(BaseModel):
    model_config = {"extra": "allow"}

    id: str
    name: str
    brand: str = "reolink"
    ip: str
    port: int = 443
    username: str
    password: str
    channel: int = 0
    stream: str = "main"
    ptz: bool = False
    enabled: bool = True
    page: Optional[str] = None
    monitor_preset: Optional[int] = None
    ptz_speed: int = 20
    ptz_degrees_per_second: float = 20.0
    pan_degrees_per_second: Optional[float] = None
    tilt_degrees_per_second: Optional[float] = None
    pan_cal: float = 1.0   # empirical click-to-centre gain (1.0 = uncalibrated)
    tilt_cal: float = 1.0
    pan_lead_s: float = 0.0   # motor acceleration/brake deadtime added to every move
    tilt_lead_s: float = 0.0
    legacy_ptz_aim: bool = False
    ptz_min_move_s: float = 0.1
    fov_h: float = 90.0
    fov_v: float = 55.0
    zoom_step_s: float = 0.3
    zoom_speed: int = 40
    pan_invert: bool = False
    tilt_invert: bool = False
    ptz_ip: Optional[str] = None
    ptz_port: int = 80
    dahua_default_pan: Optional[float] = None
    dahua_default_tilt: Optional[float] = None
    dahua_click_zoom: int = 0
    onvif_profile: Optional[str] = None
    onvif_ptz_path: str = "/onvif/ptz_service"
    onvif_stop_lead: float = 0.12
    https: bool = False


def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        raw = yaml.safe_load(f)

    # Validate each camera with Pydantic; on error log WARNING and continue
    validated_cameras = []
    for cam_data in raw.get("cameras", []):
        try:
            CameraConfig(**cam_data)
            validated_cameras.append(cam_data)
        except ValidationError as e:
            logger.warning("Camera config validation error (cam ignored): %s — %s", cam_data.get("id", "?"), e)

    raw["cameras"] = validated_cameras
    return raw


# Module-level shared mutable state — mutated in-place so all importers see the same object
_config: dict = load_config()
cameras: dict[str, dict] = {c["id"]: c for c in _config["cameras"]}


def _save_config() -> None:
    """Write current _config to config.yaml and rebuild the cameras dict."""
    with open(CONFIG_PATH, "w") as f:
        yaml.dump(_config, f, default_flow_style=False, allow_unicode=True)
    cameras.clear()
    cameras.update({c["id"]: c for c in _config["cameras"]})
