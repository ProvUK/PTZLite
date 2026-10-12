#
# Version: 1.7
# Updated: 2026-10-12
#
# v1.0 - 2026-05-18 - Initial extraction from ptzlite.py monolith
# v1.1 - 2026-05-20 - Fix pre-warm CPU: read first chunk only then hold via backpressure
#                     instead of consuming full MP4 stream non-stop (was loading go2rtc
#                     with 6 continuous remux jobs, slowing WebRTC negotiation)
# v1.2 - 2026-05-21 - Fix pre-warm dropout: hold connection for 1 hour instead of 55 s.
#                     The 55 s cycle caused all cameras to drop simultaneously every ~58 s,
#                     triggering false health-check failures and go2rtc restarts. Connection
#                     now only closes on error or intentional restart (_cancel_prewarm).
# v1.3 - 2026-05-21 - Pre-warm main streams as well as sub. Double-click fullscreen switches
#                     to main; without a live RTSP session go2rtc starts from scratch causing
#                     a 3-5 s freeze + burst. Main streams started 3 s after sub to avoid
#                     a simultaneous 12-session RTSP burst on startup.
# v1.4 - 2026-07-28 - Register per-camera _talk stream (backchannel on) for two-way audio.
# v1.5 - 2026-10-11 - Bind go2rtc API to 127.0.0.1 (browser goes via authenticated proxy)
# v1.6 - 2026-10-11 - Configurable pre-warm (both/main/sub/none)
# v1.7 - 2026-10-12 - Missing-go2rtc message names the right folders
#

import asyncio
import atexit
import collections
import logging
import subprocess
import sys
import threading
import time
import urllib.request

import aiohttp
import yaml

from core.config import _config
from core.paths import BASE, GO2RTC_BIN, GO2RTC_PORT, _STATIC_DIR
from services.rtsp import _rtsp_url, _rtsp_url_talk

logger = logging.getLogger("PTZLite.services.go2rtc")

_go2rtc_proc: subprocess.Popen | None = None
_go2rtc_log: collections.deque = collections.deque(maxlen=150)
_prewarm_tasks: list[asyncio.Task] = []
_prewarm_gen = 0   # bumped per launch; an older launch stops spawning


def _build_go2rtc_config(streams: dict) -> dict:
    """Build the go2rtc YAML config dict from a streams mapping."""
    return {
        "streams": streams,
        # API bound to localhost: browsers reach streams only via PTZLite's
        # authenticated /go2rtc/ws proxy, never go2rtc directly (it has no auth
        # and /api/streams would expose camera credentials).
        "api": {"listen": f"127.0.0.1:{GO2RTC_PORT}", "static_dir": ""},
        "log": {"level": "warn"},
        "rtsp": {"listen": ""},
        "webrtc": {"ice_servers": []},
    }


def _drain_go2rtc_output(proc: subprocess.Popen) -> None:
    """Reads go2rtc stdout+stderr into _go2rtc_log; exits when the process closes its pipe."""
    try:
        for raw in proc.stdout:  # type: ignore[union-attr]
            _go2rtc_log.append(raw.decode("utf-8", errors="replace").rstrip())
    except Exception:
        pass


def _copy_go2rtc_js() -> None:
    """Download video-stream.js from go2rtc into static/ so we can serve it same-origin."""
    static = _STATIC_DIR
    for fname in ("video-stream.js", "video-rtc.js"):
        try:
            url = f"http://127.0.0.1:{GO2RTC_PORT}/{fname}"
            with urllib.request.urlopen(url, timeout=3) as r:
                (static / fname).write_bytes(r.read())
            logger.info("Copied %s from go2rtc to static/", fname)
        except Exception as e:
            logger.warning("Could not copy %s from go2rtc: %s", fname, e)


async def _prewarm_stream_loop(name: str) -> None:
    """Hold a persistent MP4 connection to keep go2rtc's RTSP session alive.

    go2rtc stops pulling RTSP the moment all clients disconnect, so we maintain
    one connection per camera.  We read only the first chunk (~32 KB) to let
    go2rtc establish the RTSP session, then stop reading.  TCP backpressure causes
    go2rtc to pause MP4 remuxing (near-zero CPU) while the connection stays open.

    The connection is held for up to 1 hour then naturally re-established.
    It is also closed and reconnected immediately on any network error.
    Intentional go2rtc restarts cancel these tasks via _cancel_prewarm() before
    killing the process, so there is never a gap caused by a planned reconnect.
    """
    url = f"http://127.0.0.1:{GO2RTC_PORT}/api/stream.mp4?src={name}"
    while True:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, timeout=aiohttp.ClientTimeout(connect=5, total=None)
                ) as resp:
                    if resp.status == 200:
                        await resp.content.read(32768)  # prime the RTSP session
                        await asyncio.sleep(3600)       # hold open ~1 h; reconnect only on error
        except asyncio.CancelledError:
            return
        except Exception:
            pass
        await asyncio.sleep(3)


def _cancel_prewarm() -> None:
    for t in _prewarm_tasks:
        t.cancel()
    _prewarm_tasks.clear()


PREWARM_MODES = ("both", "main", "sub", "none")


def prewarm_mode() -> str:
    """Which streams to keep warm: both (default), main, sub, or none.

    Each warm stream holds one RTSP session open to the NVR permanently, so
    "both" costs two sessions per camera in exchange for instant stream opens.
    """
    mode = _config.get("prewarm", "both")
    return mode if mode in PREWARM_MODES else "both"


async def _launch_prewarm(delay: float = 3.0) -> None:
    global _prewarm_gen
    _prewarm_gen += 1
    gen = _prewarm_gen
    _cancel_prewarm()
    await asyncio.sleep(delay)
    if gen != _prewarm_gen:
        return
    enabled = [c for c in _config["cameras"] if c.get("enabled", True)]
    mode = prewarm_mode()
    if mode == "none":
        logger.info("Pre-warming disabled")
        return

    # Sub streams first
    for i, cam in enumerate(enabled if mode in ("both", "sub") else []):
        if i > 0:
            await asyncio.sleep(0.4)
        if gen != _prewarm_gen:
            return
        name = f"{cam['id']}_sub"
        t = asyncio.create_task(_prewarm_stream_loop(name))
        _prewarm_tasks.append(t)
        logger.info("Pre-warming RTSP: %s", name)

    # Main streams after a short pause — keeps go2rtc's RTSP session alive so that
    # switching to main (e.g. double-click fullscreen) connects instantly rather than
    # waiting 3-5 s for RTSP negotiation + keyframe on a cold stream.
    if mode == "both":
        await asyncio.sleep(3.0)
    for i, cam in enumerate(enabled if mode in ("both", "main") else []):
        if i > 0:
            await asyncio.sleep(0.4)
        if gen != _prewarm_gen:
            return
        name = f"{cam['id']}_main"
        t = asyncio.create_task(_prewarm_stream_loop(name))
        _prewarm_tasks.append(t)
        logger.info("Pre-warming RTSP: %s", name)


def start_go2rtc() -> None:
    global _go2rtc_proc

    if not GO2RTC_BIN.exists():
        logger.error(
            "\n  ERROR: go2rtc.exe not found at %s\n"
            "  Download from https://github.com/AlexxIT/go2rtc/releases\n"
            "  and place go2rtc.exe in the PTZLite folder (or its bin\\ subfolder)",
            GO2RTC_BIN,
        )
        sys.exit(1)

    # Register both _main and _sub streams for every enabled camera
    streams = {}
    for cam in _config["cameras"]:
        if not cam.get("enabled", True):
            continue
        streams[f"{cam['id']}_main"] = _rtsp_url(cam, "main")
        streams[f"{cam['id']}_sub"]  = _rtsp_url(cam, "sub")
        # Backchannel-enabled stream for two-way audio (Reolink only; pulled on demand)
        if cam.get("brand", "reolink").lower() == "reolink":
            streams[f"{cam['id']}_talk"] = _rtsp_url_talk(cam)

    go2rtc_cfg = _build_go2rtc_config(streams)
    cfg_file = BASE / "go2rtc_generated.yaml"
    with open(cfg_file, "w") as f:
        yaml.dump(go2rtc_cfg, f)

    _go2rtc_proc = subprocess.Popen(
        [str(GO2RTC_BIN), "-config", str(cfg_file)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    threading.Thread(target=_drain_go2rtc_output, args=(_go2rtc_proc,), daemon=True).start()
    atexit.register(stop_go2rtc)

    for _ in range(20):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{GO2RTC_PORT}/api/streams", timeout=1)
            break
        except Exception:
            time.sleep(0.3)
    else:
        logger.warning("go2rtc did not start in time — streams may not work immediately")

    _copy_go2rtc_js()


def stop_go2rtc() -> None:
    if _go2rtc_proc and _go2rtc_proc.poll() is None:
        _go2rtc_proc.terminate()
        try:
            _go2rtc_proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            _go2rtc_proc.kill()


def _reload_go2rtc() -> None:
    """Rewrite go2rtc config from current _config and restart the process."""
    global _go2rtc_proc

    streams = {}
    for cam in _config["cameras"]:
        if not cam.get("enabled", True):
            continue
        streams[f"{cam['id']}_main"] = _rtsp_url(cam, "main")
        streams[f"{cam['id']}_sub"]  = _rtsp_url(cam, "sub")
        # Backchannel-enabled stream for two-way audio (Reolink only; pulled on demand)
        if cam.get("brand", "reolink").lower() == "reolink":
            streams[f"{cam['id']}_talk"] = _rtsp_url_talk(cam)

    go2rtc_cfg = _build_go2rtc_config(streams)
    cfg_file = BASE / "go2rtc_generated.yaml"
    with open(cfg_file, "w") as f:
        yaml.dump(go2rtc_cfg, f)

    if _go2rtc_proc and _go2rtc_proc.poll() is None:
        _go2rtc_proc.terminate()
        try:
            _go2rtc_proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            _go2rtc_proc.kill()

    _go2rtc_proc = subprocess.Popen(
        [str(GO2RTC_BIN), "-config", str(cfg_file)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    threading.Thread(target=_drain_go2rtc_output, args=(_go2rtc_proc,), daemon=True).start()

    for _ in range(20):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{GO2RTC_PORT}/api/streams", timeout=1)
            break
        except Exception:
            time.sleep(0.3)
    else:
        logger.warning("go2rtc did not restart in time")

    _copy_go2rtc_js()
    logger.info("go2rtc reloaded")
