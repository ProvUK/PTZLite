#
# Version: 1.0
# Updated: 2026-10-11
#
# v1.0 - 2026-10-11 - Isolated test config; app/client fixtures
#
"""Test fixtures.

core.config reads CONFIG_PATH at import time, so we redirect it to a temp file
BEFORE any app module is imported. Tests therefore never touch the real
config.yaml, and saves / go2rtc reloads are stubbed out.
"""

import sys
import tempfile
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_TMP = Path(tempfile.mkdtemp(prefix="ptzlite-test-"))
_CFG = _TMP / "config.yaml"

BASE_CAMERAS = [
    {"id": "front_right", "name": "Front Right", "brand": "reolink", "ip": "10.0.0.1",
     "port": 443, "username": "admin", "password": "s3cret", "channel": 0, "ptz": True},
    {"id": "trackmix_tele", "name": "TrackMix Tele", "brand": "reolink", "ip": "10.0.0.2",
     "port": 443, "username": "admin", "password": "", "channel": 1, "ptz": False},
    {"id": "garden", "name": "Garden", "brand": "dahua", "ip": "10.0.0.3",
     "port": 443, "username": "admin", "password": "d4hua", "channel": 1, "ptz": True},
]
_CFG.write_text(yaml.safe_dump({"cameras": BASE_CAMERAS}))

import core.paths  # noqa: E402
core.paths.CONFIG_PATH = _CFG
core.paths.BASE = _TMP

import copy  # noqa: E402

import core.config  # noqa: E402  (now loads the temp config)


@pytest.fixture
def app_env(monkeypatch):
    """Fresh camera config per test; no disk writes, no go2rtc restarts."""
    import api.auth
    import api.cameras
    import api.ptz

    core.config._config.clear()
    core.config._config.update({"cameras": copy.deepcopy(BASE_CAMERAS)})
    core.config.cameras.clear()
    core.config.cameras.update({c["id"]: c for c in core.config._config["cameras"]})

    def fake_save():
        # Same as the real _save_config minus the disk write: rebuild the lookup.
        core.config.cameras.clear()
        core.config.cameras.update({c["id"]: c for c in core.config._config["cameras"]})

    for mod in (api.cameras, api.auth, api.ptz, core.config):
        if hasattr(mod, "_save_config"):
            monkeypatch.setattr(mod, "_save_config", fake_save)
    monkeypatch.setattr(api.cameras, "_reload_go2rtc", lambda *a, **k: None)
    return core.config


@pytest.fixture
def client(app_env):
    from fastapi.testclient import TestClient
    import ptzlite
    # Not used as a context manager, so startup events (go2rtc pre-warm) don't run.
    return TestClient(ptzlite.app)


@pytest.fixture
def password(app_env):
    """Enable password protection for the duration of a test."""
    app_env._config["password"] = "letmein"
    yield "letmein"
    app_env._config.pop("password", None)
