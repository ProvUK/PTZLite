#
# Version: 1.0
# Updated: 2026-05-18
#
# v1.0 - 2026-05-18 - Initial extraction from ptzlite.py monolith
#

import logging

logger = logging.getLogger("PTZLite.services.ptz")

# Preset cache shared across ptz submodules and api/cameras.py
preset_cache: dict[str, list[dict]] = {}


def _is_onvif(cam: dict) -> bool:
    return cam.get("brand", "reolink").lower() != "reolink"


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
