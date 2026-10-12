#
# Version: 1.2
# Updated: 2026-10-12
#
# v1.0 - 2026-05-18 - Initial extraction from ptzlite.py monolith
# v1.1 - 2026-10-11 - Use shared pooled HTTP session; retry once on stale keep-alive
# v1.2 - 2026-10-12 - Add _onvif_get_zoom (zoom-aware click-to-centre)
#

# ── ONVIF PTZ (Dahua / Hikvision / generic ONVIF cameras) ─────────────────────
# Reolink uses a proprietary HTTP API; everything else uses ONVIF SOAP.
# No extra dependencies — requests are built manually with aiohttp.

import base64
import datetime
import hashlib
import logging
import os
import xml.etree.ElementTree as ET

import aiohttp

from services.http import STALE_CONN_ERRORS, get_session

logger = logging.getLogger("PTZLite.services.ptz.onvif")

# cam_id -> (x, y) in ONVIF normalised space (-1..1) after last successful AbsoluteMove
_onvif_tracked_pos: dict[str, tuple[float, float]] = {}

_ONVIF_SOAP_ATTRS = (
    'xmlns:s="http://www.w3.org/2003/05/soap-envelope"'
    ' xmlns:tptz="http://www.onvif.org/ver20/ptz/wsdl"'
    ' xmlns:tt="http://www.onvif.org/ver10/schema"'
)


def _onvif_profile(cam: dict) -> str:
    # Dahua NVRs: "MediaProfile00000" = channel 1 main, "MediaProfile00008" = channel 2, etc.
    # Set onvif_profile in config.yaml to override.
    return cam.get("onvif_profile", "MediaProfile00000")


def _onvif_ptz_path(cam: dict) -> str:
    # Dahua uses /onvif/ptz_service; override with onvif_ptz_path in config.yaml for other brands.
    return cam.get("onvif_ptz_path", "/onvif/ptz_service")


def _onvif_envelope(cam: dict, body: str) -> str:
    # WS-Security PasswordDigest: Base64(SHA-1(nonce_bytes + created_str + password))
    # Required by Dahua PTZ service; PasswordText is rejected with NotAuthorized.
    nonce_raw = os.urandom(16)
    nonce_b64 = base64.b64encode(nonce_raw).decode()
    created = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    digest = base64.b64encode(
        hashlib.sha1(nonce_raw + created.encode() + cam["password"].encode()).digest()
    ).decode()
    wsse = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"
    wsu  = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd"
    pw_type = f"{wsse}#PasswordDigest"
    nc_type = f"{wsse}#Base64Binary"
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<s:Envelope {_ONVIF_SOAP_ATTRS}>'
        '<s:Header>'
        f'<wsse:Security xmlns:wsse="{wsse}" xmlns:wsu="{wsu}">'
        '<wsse:UsernameToken>'
        f'<wsse:Username>{cam["username"]}</wsse:Username>'
        f'<wsse:Password Type="{pw_type}">{digest}</wsse:Password>'
        f'<wsse:Nonce EncodingType="{nc_type}">{nonce_b64}</wsse:Nonce>'
        f'<wsu:Created>{created}</wsu:Created>'
        '</wsse:UsernameToken>'
        '</wsse:Security>'
        '</s:Header>'
        f'<s:Body>{body}</s:Body>'
        '</s:Envelope>'
    )


async def _onvif_post(cam: dict, action: str, body: str, timeout: float = 5.0) -> str:
    port = cam.get("port", 80)
    scheme = "https" if port == 443 or cam.get("https") else "http"
    path = _onvif_ptz_path(cam)
    url = f"{scheme}://{cam['ip']}:{port}{path}"
    envelope = _onvif_envelope(cam, body).encode("utf-8")
    headers = {
        "Content-Type": "application/soap+xml; charset=utf-8",
        "SOAPAction": f'"http://www.onvif.org/ver20/ptz/wsdl/{action}"',
    }
    for attempt in (1, 2):
        try:
            async with get_session().post(
                url, data=envelope, headers=headers,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                return await resp.text()
        except STALE_CONN_ERRORS:
            if attempt == 2:
                raise


async def _onvif_continuous(cam: dict, pan: float = 0.0, tilt: float = 0.0, zoom: float = 0.0) -> None:
    pt = f'<tt:PanTilt x="{pan:.3f}" y="{tilt:.3f}"/>' if pan or tilt else ""
    z  = f'<tt:Zoom x="{zoom:.3f}"/>' if zoom else ""
    body = (
        f"<tptz:ContinuousMove>"
        f"<tptz:ProfileToken>{_onvif_profile(cam)}</tptz:ProfileToken>"
        f"<tptz:Velocity>{pt}{z}</tptz:Velocity>"
        f"</tptz:ContinuousMove>"
    )
    await _onvif_post(cam, "ContinuousMove", body)


async def _onvif_relative(cam: dict, pan: float, tilt: float) -> None:
    body = (
        f"<tptz:RelativeMove>"
        f"<tptz:ProfileToken>{_onvif_profile(cam)}</tptz:ProfileToken>"
        f"<tptz:Translation><tt:PanTilt x=\"{pan:.3f}\" y=\"{tilt:.3f}\"/></tptz:Translation>"
        f"</tptz:RelativeMove>"
    )
    await _onvif_post(cam, "RelativeMove", body)


async def _onvif_stop(cam: dict) -> None:
    body = (
        f"<tptz:Stop>"
        f"<tptz:ProfileToken>{_onvif_profile(cam)}</tptz:ProfileToken>"
        f"<tptz:PanTilt>true</tptz:PanTilt>"
        f"<tptz:Zoom>true</tptz:Zoom>"
        f"</tptz:Stop>"
    )
    await _onvif_post(cam, "Stop", body)


async def _onvif_get_status(cam: dict) -> tuple[float, float] | None:
    body = (
        f"<tptz:GetStatus>"
        f"<tptz:ProfileToken>{_onvif_profile(cam)}</tptz:ProfileToken>"
        f"</tptz:GetStatus>"
    )
    try:
        xml_text = await _onvif_post(cam, "GetStatus", body, timeout=3.0)
        root = ET.fromstring(xml_text)
        NS = "http://www.onvif.org/ver10/schema"
        for pt in root.iter(f"{{{NS}}}PanTilt"):
            return (float(pt.get("x", "0")), float(pt.get("y", "0")))
    except Exception as e:
        logger.error("ONVIF GetStatus error: %s", e)
    return None


async def _onvif_get_zoom(cam: dict) -> float | None:
    """Current zoom from GetStatus (generic space: 0 = wide .. 1 = full), or None."""
    body = (
        f"<tptz:GetStatus>"
        f"<tptz:ProfileToken>{_onvif_profile(cam)}</tptz:ProfileToken>"
        f"</tptz:GetStatus>"
    )
    try:
        xml_text = await _onvif_post(cam, "GetStatus", body, timeout=3.0)
        root = ET.fromstring(xml_text)
        NS = "http://www.onvif.org/ver10/schema"
        for z in root.iter(f"{{{NS}}}Zoom"):
            if z.get("x") is not None:
                return float(z.get("x"))
    except Exception as e:
        logger.debug("ONVIF zoom status unavailable: %s", e)
    return None


async def _onvif_absolute(cam: dict, pan: float, tilt: float, speed: float = 0.5) -> None:
    pan  = max(-1.0, min(1.0, pan))
    tilt = max(-1.0, min(1.0, tilt))
    body = (
        f"<tptz:AbsoluteMove>"
        f"<tptz:ProfileToken>{_onvif_profile(cam)}</tptz:ProfileToken>"
        f"<tptz:Position><tt:PanTilt x=\"{pan:.4f}\" y=\"{tilt:.4f}\"/></tptz:Position>"
        f"<tptz:Speed><tt:PanTilt x=\"{speed:.3f}\" y=\"{speed:.3f}\"/></tptz:Speed>"
        f"</tptz:AbsoluteMove>"
    )
    await _onvif_post(cam, "AbsoluteMove", body)


async def _onvif_get_presets(cam: dict) -> list[dict]:
    body = (
        f"<tptz:GetPresets>"
        f"<tptz:ProfileToken>{_onvif_profile(cam)}</tptz:ProfileToken>"
        f"</tptz:GetPresets>"
    )
    xml_text = await _onvif_post(cam, "GetPresets", body)
    try:
        root = ET.fromstring(xml_text)
        NS = "http://www.onvif.org/ver10/schema"
        result = []
        for preset in root.iter(f"{{{NS}}}Preset"):
            token = preset.get("token", "")
            name_el = preset.find(f"{{{NS}}}Name")
            name = (name_el.text or f"Preset {token}") if name_el is not None else f"Preset {token}"
            if token:
                result.append({"id": token, "name": name})
        return result
    except Exception as e:
        logger.error("ONVIF GetPresets parse error: %s", e)
        return []


async def _onvif_goto_preset(cam: dict, token: str) -> None:
    body = (
        f"<tptz:GotoPreset>"
        f"<tptz:ProfileToken>{_onvif_profile(cam)}</tptz:ProfileToken>"
        f"<tptz:PresetToken>{token}</tptz:PresetToken>"
        f"</tptz:GotoPreset>"
    )
    await _onvif_post(cam, "GotoPreset", body)
