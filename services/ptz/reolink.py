#
# Version: 1.1
# Updated: 2026-05-22
#
# v1.0 - 2026-05-18 - Initial extraction from ptzlite.py monolith
# v1.1 - 2026-05-22 - Retry on rspCode -6 (token invalidated by NVR between calls)
#

import asyncio
import logging
import time

import aiohttp

logger = logging.getLogger("PTZLite.services.ptz.reolink")

# ── Reolink authentication + serialized NVR API ────────────────────────────────
#
# This NVR kills the session if it receives concurrent API requests from the same
# login session. _nvr_call serializes all calls per NVR identity (one at a time),
# handling login automatically. The lock is held only for the duration of the HTTP
# request itself — sleeps between PTZ command and Stop happen outside the lock.

_tokens: dict[str, tuple[str, float]] = {}
_nvr_locks: dict[str, asyncio.Lock] = {}

_DIR = {
    ( 1,  0): "Right",   (-1,  0): "Left",
    ( 0,  1): "Up",      ( 0, -1): "Down",
    ( 1,  1): "RightUp", ( 1, -1): "RightDown",
    (-1,  1): "LeftUp",  (-1, -1): "LeftDown",
}


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


async def _login(cam: dict) -> str:
    """Perform a fresh login and cache the new token. Returns the token string."""
    key = _nvr_key(cam)
    port = cam.get("port", 80)
    scheme = "https" if port == 443 or cam.get("https") else "http"
    login_url = f"{scheme}://{cam['ip']}:{port}/api.cgi?cmd=Login"
    data = await _post(login_url, [{"cmd": "Login", "action": 0, "param": {
        "User": {"userName": cam["username"], "password": cam["password"]}
    }}])
    token = data[0]["value"]["Token"]["name"]
    lease = data[0]["value"]["Token"]["leaseTime"]
    _tokens[key] = (token, time.time() + lease)
    logger.info("Login %s: token cached (lease=%ss)", key, lease)
    return token


def _has_auth_error(result: list) -> bool:
    """Return True if any response item signals token invalidation (rspCode -6)."""
    return any(
        item.get("error", {}).get("rspCode") == -6
        for item in result
        if isinstance(item, dict)
    )


async def _nvr_call(cam: dict, cmd: str, payload: list, timeout: int = 8) -> list:
    """Serialized NVR API call — one request at a time per NVR, auto-login if needed.

    If the NVR returns rspCode -6 (token invalidated between calls), the cached
    token is discarded, a fresh login is performed, and the command is retried once.
    """
    key = _nvr_key(cam)
    lock = _get_nvr_lock(cam)
    async with lock:
        token, expires = _tokens.get(key, ("", 0.0))
        if not token or time.time() >= expires - 60:
            token = await _login(cam)

        result = await _post(_api_url(cam, cmd, token), payload, timeout)

        if _has_auth_error(result):
            logger.warning(
                "NVR %s returned rspCode -6 on %s — token invalidated; re-logging in",
                key, cmd,
            )
            _tokens.pop(key, None)
            token = await _login(cam)
            result = await _post(_api_url(cam, cmd, token), payload, timeout)

        return result


async def _ptz_stop(cam: dict) -> None:
    await _nvr_call(cam, "PtzCtrl", [{"cmd": "PtzCtrl", "action": 0, "param": {
        "channel": cam.get("channel", 0), "op": "Stop", "speed": 1, "timeout": 0,
    }}])
