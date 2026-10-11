#
# Version: 1.3
# Updated: 2026-10-11
#
# v1.0 - 2026-10-11 - RTSP URL builders and PTZ move maths
# v1.1 - 2026-10-11 - Pre-warm mode tests
# v1.2 - 2026-10-11 - Config load: invalid cameras preserved, retired keys stripped
# v1.3 - 2026-10-11 - Next free preset slot; readable camera errors
#
import math

import pytest

from services.rtsp import _clean_rtsp_url, _rtsp_url, _rtsp_url_talk

REOLINK = {"brand": "reolink", "ip": "10.0.0.1", "username": "admin", "password": "pw", "channel": 0}
DAHUA = {"brand": "dahua", "ip": "10.0.0.3", "username": "admin", "password": "pw", "channel": 1}


def test_reolink_url_is_one_indexed_with_backchannel_off():
    url = _rtsp_url(REOLINK, "sub")
    assert url.startswith("rtsp://admin:pw@10.0.0.1:554/h264Preview_01_sub")
    assert "#backchannel=0" in url and "#rtsp_transport=tcp" in url


def test_reolink_second_lens_is_preview_02():
    assert "h264Preview_02_main" in _rtsp_url({**REOLINK, "channel": 1}, "main")


def test_talk_url_enables_backchannel_on_sub():
    url = _rtsp_url_talk(REOLINK)
    assert "h264Preview_01_sub" in url and "#backchannel=1" in url and "backchannel=0" not in url


def test_dahua_url_shape():
    assert "/cam/realmonitor?channel=1&subtype=0" in _rtsp_url(DAHUA, "main")
    assert "subtype=1" in _rtsp_url(DAHUA, "sub")


def test_clean_url_has_no_credentials_or_go2rtc_options():
    url = _clean_rtsp_url(REOLINK, "main")
    assert "pw" not in url and "#" not in url


# ── PTZ maths ─────────────────────────────────────────────────────────────────

def test_click_projection_is_rectilinear():
    from api.ptz import _screen_delta_to_angles
    cam = {"fov_h": 90, "fov_v": 60}
    pan, tilt = _screen_delta_to_angles(cam, 1.0, 0.0)
    assert pan == pytest.approx(45.0)                  # frame edge = half the FOV
    pan, _ = _screen_delta_to_angles(cam, 0.5, 0.0)
    assert pan == pytest.approx(math.degrees(math.atan(0.5)))   # not the linear 22.5
    _, tilt = _screen_delta_to_angles(cam, 0.0, 1.0)
    assert tilt == pytest.approx(-30.0)                # screen down = tilt down


def test_move_duration_is_proportional_and_trimmed_by_gain():
    from api.ptz import _move_duration
    cam = {"pan_degrees_per_second": 20.0, "pan_cal": 1.0}
    assert _move_duration(cam, "pan", 10) == pytest.approx(0.5)
    assert _move_duration({**cam, "pan_cal": 1.2}, "pan", 10) == pytest.approx(0.6)


def test_move_duration_falls_back_to_shared_speed():
    from api.ptz import _move_duration
    assert _move_duration({"ptz_degrees_per_second": 40}, "tilt", 20) == pytest.approx(0.5)


@pytest.mark.parametrize("cfg,expected", [({}, "both"), ({"prewarm": "sub"}, "sub"),
                                          ({"prewarm": "none"}, "none"), ({"prewarm": "bogus"}, "both")])
def test_prewarm_mode(app_env, cfg, expected):
    from services.go2rtc import prewarm_mode
    app_env._config.update(cfg)
    assert prewarm_mode() == expected


def test_prewarm_setting_validated(client):
    assert client.post("/api/config/prewarm", json={"mode": "bogus"}).status_code == 400


def test_invalid_camera_is_preserved_on_save(tmp_path, monkeypatch):
    """A camera that fails validation must not be erased from disk by a save."""
    import yaml
    import core.config as cc
    cfg = tmp_path / "config.yaml"
    cfg.write_text(yaml.safe_dump({"cameras": [
        {"id": "ok", "name": "OK", "ip": "1.2.3.4", "username": "a", "password": ""},
        {"id": "broken", "name": "Missing ip and username"},
    ]}))
    monkeypatch.setattr(cc, "CONFIG_PATH", cfg)
    monkeypatch.setattr(cc, "_invalid_cameras", [])
    loaded = cc.load_config()
    assert [c["id"] for c in loaded["cameras"]] == ["ok"]
    monkeypatch.setattr(cc, "_config", loaded)
    monkeypatch.setattr(cc, "cameras", {})
    cc._save_config()
    ids = [c["id"] for c in yaml.safe_load(cfg.read_text())["cameras"]]
    assert ids == ["ok", "broken"]


def test_retired_keys_stripped_on_load(tmp_path, monkeypatch):
    import yaml
    import core.config as cc
    cfg = tmp_path / "config.yaml"
    cfg.write_text(yaml.safe_dump({"cameras": [
        {"id": "c", "name": "C", "ip": "1.2.3.4", "username": "a", "password": "",
         "pan_lead_s": -0.2, "legacy_ptz_aim": False}]}))
    monkeypatch.setattr(cc, "CONFIG_PATH", cfg)
    monkeypatch.setattr(cc, "_invalid_cameras", [])
    cam = cc.load_config()["cameras"][0]
    assert "pan_lead_s" not in cam and "legacy_ptz_aim" not in cam


def test_next_free_preset_slot():
    from api.ptz import _next_free_preset
    slots = [{"id": 0, "enable": 1}, {"id": 1, "enable": 1}, {"id": 2, "enable": 0}, {"id": 3, "enable": 0}]
    assert _next_free_preset(slots) == 2
    assert _next_free_preset([{"id": 0, "enable": 1}]) is None


def test_camera_errors_are_readable(client, monkeypatch):
    import api.ptz
    from services.ptz.reolink import ReolinkLoginError

    async def bad_login(*a, **k):
        raise ReolinkLoginError("Login to x failed: invalid password")
    monkeypatch.setattr(api.ptz, "_nvr_call", bad_login)
    r = client.post("/light/front_right/ir", json={"state": "Auto"})
    assert r.status_code == 502 and "username/password" in r.json()["detail"]

    async def timeout(*a, **k):
        raise TimeoutError()
    monkeypatch.setattr(api.ptz, "_nvr_call", timeout)
    r = client.post("/light/front_right/ir", json={"state": "Auto"})
    assert r.status_code == 504 and "didn't respond" in r.json()["detail"]
