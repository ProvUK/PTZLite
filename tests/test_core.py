#
# Version: 1.0
# Updated: 2026-10-11
#
# v1.0 - 2026-10-11 - RTSP URL builders and PTZ move maths
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
