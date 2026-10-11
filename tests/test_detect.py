#
# Version: 1.0
# Updated: 2026-10-11
#
# v1.0 - 2026-10-11 - Batched detection sweep, parsing, PTZ-skip
#
import asyncio

import api.detect as detect
from services.ptz import reolink


def _fake_nvr(calls, responder):
    async def fake(cam, cmd, payload, timeout=8):
        calls.append((cam["ip"], [p["param"]["channel"] for p in payload]))
        return responder(payload)
    return fake


def _quiet(payload):
    out = []
    for p in payload:
        if p["cmd"] == "GetMdState":
            out.append({"cmd": "GetMdState", "code": 0, "value": {"state": 0}})
        else:
            out.append({"cmd": "GetAiState", "code": 0, "value": {
                "people": {"support": 1, "alarm_state": 0}}})
    return out


def test_one_batched_request_per_nvr(app_env, monkeypatch):
    # Two Reolink cameras on the same NVR, plus the Dahua (excluded).
    app_env.cameras["trackmix_tele"]["ip"] = "10.0.0.1"
    calls = []
    monkeypatch.setattr(detect, "_nvr_call", _fake_nvr(calls, _quiet))
    states = asyncio.run(detect.poll_once())
    assert calls == [("10.0.0.1", [0, 0, 1, 1])]      # ONE request: md+ai for both channels
    assert set(states) == {"front_right", "trackmix_tele"}
    assert states["front_right"] == {"motion": False, "alerts": []}


def test_ai_alert_beats_plain_motion(app_env, monkeypatch):
    def responder(payload):
        return [{"cmd": "GetMdState", "code": 0, "value": {"state": 1}},
                {"cmd": "GetAiState", "code": 0, "value": {
                    "people": {"support": 1, "alarm_state": 1},
                    "vehicle": {"support": 0, "alarm_state": 1}}}]   # unsupported -> ignored
    monkeypatch.setattr(detect, "_nvr_call", _fake_nvr([], responder))
    app_env.cameras.pop("trackmix_tele")
    states = asyncio.run(detect.poll_once())
    assert states["front_right"] == {"motion": False, "alerts": ["people"]}


def test_skips_nvr_while_ptz_moving(app_env, monkeypatch):
    calls = []
    monkeypatch.setattr(detect, "_nvr_call", _fake_nvr(calls, _quiet))
    cam = app_env.cameras["front_right"]
    monkeypatch.setitem(reolink._last_ptz, reolink._nvr_key(cam), 1e18)   # "just moved"
    prev = {"front_right": {"motion": True, "alerts": []}}
    states = asyncio.run(detect.poll_once(prev))
    assert all(ip != "10.0.0.1" for ip, _ in calls)   # moving NVR not queried
    assert states["front_right"] == prev["front_right"]   # last-known state kept


def test_nvr_error_keeps_previous_state(app_env, monkeypatch):
    async def boom(*a, **k):
        raise TimeoutError("nvr down")
    monkeypatch.setattr(detect, "_nvr_call", boom)
    prev = {"front_right": {"motion": False, "alerts": ["vehicle"]}}
    assert asyncio.run(detect.poll_once(prev))["front_right"] == prev["front_right"]


def test_detect_stream_requires_login(client, password):
    assert client.get("/detect/stream").status_code == 401
