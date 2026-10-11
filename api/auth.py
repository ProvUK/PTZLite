#
# Version: 1.0
# Updated: 2026-05-19
#
# v1.0 - 2026-05-19 - Password protection: HMAC session cookie, enable/disable via config
#

import hmac
import logging
import secrets

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from core.config import _config, _save_config

router = APIRouter()
logger = logging.getLogger("PTZLite.api.auth")

# Generated fresh on every server start — invalidates all cookies on restart
_server_secret: str = secrets.token_hex(32)
_COOKIE_NAME = "ptzlite_auth"
_COOKIE_MAX_AGE = 30 * 24 * 3600  # 30 days


def _make_token() -> str:
    return hmac.new(_server_secret.encode(), b"ptzlite-auth", "sha256").hexdigest()


def _verify_token(token: str) -> bool:
    try:
        return hmac.compare_digest(token, _make_token())
    except Exception:
        return False


def is_authenticated(request: Request) -> bool:
    """True when no password is configured, or a valid session cookie is present."""
    if not _config.get("password"):
        return True
    token = request.cookies.get(_COOKIE_NAME)
    return bool(token and _verify_token(token))


@router.get("/api/auth/status")
async def api_auth_status(request: Request):
    return {
        "enabled": bool(_config.get("password")),
        "authenticated": is_authenticated(request),
    }


@router.post("/api/login")
async def api_login(request: Request):
    data = await request.json()
    pw = data.get("password", "")
    configured = _config.get("password", "")
    if not configured or pw != configured:
        return JSONResponse({"ok": False, "error": "Wrong password"}, status_code=401)
    resp = JSONResponse({"ok": True})
    resp.set_cookie(
        _COOKIE_NAME, _make_token(),
        max_age=_COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
    )
    return resp


@router.post("/api/logout")
async def api_logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(_COOKIE_NAME)
    return resp


@router.post("/api/config/password")
async def api_set_password(request: Request):
    data = await request.json()
    new_pw = data.get("password", "")
    _config["password"] = new_pw
    _save_config()
    logger.info("Password %s", "set" if new_pw else "cleared (auth disabled)")
    resp = JSONResponse({"ok": True})
    if not new_pw:
        resp.delete_cookie(_COOKIE_NAME)
    return resp
