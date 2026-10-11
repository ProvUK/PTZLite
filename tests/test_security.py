#
# Version: 1.1
# Updated: 2026-10-11
#
# v1.0 - 2026-10-11 - Auth middleware, password masking, go2rtc proxy lockdown
# v1.1 - 2026-10-11 - Session persistence + password-change sign-out
#
import pytest
from starlette.websockets import WebSocketDisconnect

from api.cameras import PW_MASK


# ── Auth middleware: deny by default ──────────────────────────────────────────

# Every action endpoint, including ones added after the auth middleware was
# written. A new endpoint is protected automatically; this list guards that.
PROTECTED_POSTS = [
    "/ptz/front_right", "/zoom/front_right", "/siren/front_right",
    "/light/front_right/white", "/light/front_right/ir", "/guard/front_right",
    "/ai/front_right", "/patrol/front_right", "/preset/front_right/save",
    "/api/config/password", "/api/go2rtc/restart",
]
PROTECTED_GETS = ["/api/cameras", "/cameras", "/detect/states", "/api/config/export", "/api/log"]


@pytest.mark.parametrize("path", PROTECTED_POSTS)
def test_actions_require_login(client, password, path):
    r = client.post(path, json={}, follow_redirects=False)
    assert r.status_code == 401


@pytest.mark.parametrize("path", PROTECTED_GETS)
def test_reads_require_login(client, password, path):
    r = client.get(path, follow_redirects=False)
    assert r.status_code == 401


def test_page_load_redirects_to_login(client, password):
    r = client.get("/", follow_redirects=False)
    assert r.status_code in (302, 307)
    assert r.headers["location"] == "/login.html"


@pytest.mark.parametrize("path", ["/login.html", "/main-logo.png", "/api/auth/status"])
def test_login_page_and_assets_are_public(client, password, path):
    assert client.get(path).status_code == 200


def test_login_flow(client, password):
    assert client.post("/api/login", json={"password": "wrong"}).status_code == 401
    assert client.post("/api/login", json={"password": password}).status_code == 200
    assert client.get("/api/cameras").status_code == 200   # cookie now set


def test_no_password_means_open(client):
    assert client.get("/api/cameras").status_code == 200


# ── Camera passwords never reach the browser ──────────────────────────────────

def test_passwords_masked_on_read(client):
    cams = {c["id"]: c for c in client.get("/api/cameras").json()}
    assert cams["front_right"]["password"] == PW_MASK
    assert cams["trackmix_tele"]["password"] == ""        # genuinely blank stays blank
    assert "s3cret" not in client.get("/api/cameras").text


def test_saving_mask_keeps_stored_password(client, app_env):
    r = client.put("/api/cameras/front_right",
                   json={"id": "front_right", "name": "Renamed", "password": PW_MASK})
    assert r.status_code == 200 and r.json()["password"] == PW_MASK
    assert app_env.cameras["front_right"]["password"] == "s3cret"
    assert app_env.cameras["front_right"]["name"] == "Renamed"


def test_saving_new_password_replaces_it(client, app_env):
    client.put("/api/cameras/front_right", json={"id": "front_right", "password": "n3w"})
    assert app_env.cameras["front_right"]["password"] == "n3w"


def test_saving_blank_password_clears_it(client, app_env):
    client.put("/api/cameras/front_right", json={"id": "front_right", "password": ""})
    assert app_env.cameras["front_right"]["password"] == ""


def test_patch_with_mask_keeps_password(client, app_env):
    client.patch("/api/cameras/front_right", json={"enabled": False, "password": PW_MASK})
    assert app_env.cameras["front_right"]["password"] == "s3cret"


# ── go2rtc lockdown ───────────────────────────────────────────────────────────

def test_go2rtc_api_bound_to_localhost():
    from services.go2rtc import _build_go2rtc_config
    cfg = _build_go2rtc_config({})
    assert cfg["api"]["listen"].startswith("127.0.0.1:")
    assert "origin" not in cfg["api"]          # no wildcard CORS
    assert cfg["rtsp"]["listen"] == ""          # RTSP restream server stays off


@pytest.mark.parametrize("src,ok", [
    ("front_right_main", True), ("front_right_sub", True), ("front_right_talk", True),
    ("trackmix_tele_main", True),                   # ids containing underscores
    ("front_right", False), ("front_right_raw", False), ("nosuchcam_main", False),
    ("../api/streams", False),
])
def test_proxy_only_allows_known_streams(app_env, src, ok):
    from api.go2rtc_api import _allowed_stream
    assert _allowed_stream(src) is ok


def test_ws_proxy_rejects_unauthenticated(client, password):
    with pytest.raises(WebSocketDisconnect) as e:
        with client.websocket_connect("/go2rtc/ws?src=front_right_sub") as ws:
            ws.receive_text()
    assert e.value.code == 4401


def test_ws_proxy_rejects_cross_site_origin(client):
    with pytest.raises(WebSocketDisconnect) as e:
        with client.websocket_connect("/go2rtc/ws?src=front_right_sub",
                                      headers={"origin": "http://evil.example"}) as ws:
            ws.receive_text()
    assert e.value.code == 4401


def test_ws_proxy_rejects_unknown_stream(client):
    with pytest.raises(WebSocketDisconnect) as e:
        with client.websocket_connect("/go2rtc/ws?src=nosuchcam_main") as ws:
            ws.receive_text()
    assert e.value.code == 4404


def test_password_change_signs_out_other_sessions(client, password):
    from fastapi.testclient import TestClient
    import ptzlite
    other = TestClient(ptzlite.app)
    assert other.post("/api/login", json={"password": password}).status_code == 200
    assert client.post("/api/login", json={"password": password}).status_code == 200
    assert client.post("/api/config/password", json={"password": "newpass"}).status_code == 200
    assert client.get("/api/cameras").status_code == 200      # changer stays signed in
    assert other.get("/api/cameras").status_code == 401       # everyone else is out


def test_session_secret_persists(tmp_path, monkeypatch):
    import api.auth as auth
    monkeypatch.setattr(auth, "_SECRET_FILE", tmp_path / ".session_secret")
    first = auth._load_secret()
    assert auth._load_secret() == first      # same secret after a "restart"
