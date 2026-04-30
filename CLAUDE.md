# CLAUDE.md — reolink-viewer

Guidance for Claude Code when working in this repository.

## Project Overview

Standalone Python/FastAPI camera viewer with PTZ control, presets, playback, and WebRTC streaming.
**Not part of the Home Assistant config repo** — separate git repo at `C:\Users\james\Documents\reolink-viewer`.

## File Versioning

Every edit to `reolink_viewer.py`, `static/index.html`, `static/settings.html` must increment the version header.

**Python files:**
```python
# Version: X.Y
# Updated: YYYY-MM-DD
# v1.0 - YYYY-MM-DD - Description
```

**HTML files:** version comment at top of `<head>`:
```html
<!-- Version: X.Y | Updated: YYYY-MM-DD -->
```

- Minor bump (X.Y → X.Y+1): any edit
- Major bump (X.0 → X+1.0): structural rewrites only
- Always add a changelog line

## Stack

| File | Purpose |
|------|---------|
| `reolink_viewer.py` | FastAPI + uvicorn backend (current: v3.6) |
| `go2rtc.exe` | Bundled RTSP→WebRTC proxy; config written to `go2rtc_generated.yaml` at startup |
| `static/index.html` | Single-page viewer (video-rtc web component, grid layout, PTZ, presets) |
| `static/settings.html` | Camera management UI (add/edit/delete/test cameras) |
| `config.yaml` | Camera list — **gitignored**, never commit |
| `config.example.yaml` | Committed template (no real credentials) |
| `requirements.txt` | `fastapi uvicorn[standard] aiohttp pyyaml pystray Pillow` |

## Running

```bash
cd C:\Users\james\Documents\reolink-viewer
C:\Python314\python.exe reolink_viewer.py
# → http://localhost:8765
```

## Camera Config (`config.yaml`)

```yaml
cameras:
- id: my_camera          # unique slug
  name: My Camera
  brand: reolink         # reolink | dahua
  ip: 192.168.0.x
  port: 443
  username: admin
  password: secret
  channel: 0             # NVR channel (0-indexed for Reolink, 1-indexed for Dahua)
  stream: sub            # sub | main
  ptz: true
  playback: true
  fov_h: 90              # horizontal FoV in degrees (affects click-to-centre accuracy)
  fov_v: 55              # vertical FoV in degrees
  ptz_speed: 40          # 1-63
  ptz_degrees_per_second: 20
  zoom_speed: 63
  zoom_step_s: 0.3
  pan_invert: false      # flip pan direction
  tilt_invert: false     # flip tilt direction
  monitor_preset: 0      # preset to go to on app start (null = none)

  # Dahua-only extras:
  onvif_profile: MediaProfile00000   # ONVIF profile token (channel 1=00000, 2=00008)
  dahua_default_pan: 0   # assumed starting pan (degrees) — seeds position tracker
  dahua_default_tilt: 0  # assumed starting tilt (degrees)
  dahua_click_zoom: 0    # zoom arg sent with PositionABS (0 = minimum)
```

## PTZ Architecture

### Reolink cameras
- Proprietary HTTP API: `POST /api.cgi?cmd=PtzCtrl` with session token
- Per-NVR serialised lock (`_nvr_locks`) to prevent concurrent request rejection
- Click-to-centre: timed `PtzCtrl` (Right/Left/Up/Down) with duration = degrees ÷ dps

### Dahua cameras (ONVIF + CGI)
- **Joystick / zoom**: ONVIF SOAP `ContinuousMove` / `Stop` via `/onvif/ptz_service`
  - WS-Security PasswordDigest auth (SHA-1 hash, not PasswordText)
- **Click-to-centre**: Dahua proprietary CGI `PositionABS`
  - `GET /cgi-bin/ptz.cgi?action=start&channel=N&code=PositionABS&arg1=PAN&arg2=TILT&arg3=ZOOM`
  - HTTP Digest auth via urllib (persistent opener cached in `_dahua_openers` per NVR)
  - arg3 = zoom position (NOT speed) — 0 = minimum zoom, omitting returns 400
  - Position tracked in `_dahua_tracked_pos` after each successful PositionABS
  - Fast path: tracked/default position used directly — no network call needed
  - Slow path (first click only): CGI `getStatus` attempted (many NVRs return 400)
  - Falls back to timed ONVIF ContinuousMove if PositionABS fails
- **Presets**: ONVIF `GetPresets` / `GotoPreset`

### Known Dahua NVR limitation
DHI-NVR5432-16P-I (192.168.0.50) returns HTTP 400 for CGI `getStatus` on IP camera channels.
Workaround: set `dahua_default_pan`/`dahua_default_tilt` in config.yaml to seed the tracker.

## go2rtc Integration

- go2rtc started as subprocess at app launch; config written to `go2rtc_generated.yaml`
- Streams registered as `{id}_main` and `{id}_sub`
- Health check polls `/api/go2rtc/health` every 30s; auto-restarts on failure (60s cooldown)
- Stream offline detection: per-card 5s interval checks `video.videoWidth`
- go2rtc restarted (not just reconfigured) on any camera add/update/delete

## Cameras

| ID | Name | Brand | IP | Channel | PTZ |
|----|------|-------|-----|---------|-----|
| `front_ptz` | Front PTZ | Reolink NVR | 192.168.0.245:443 | 0 | Yes |
| `front_overview` | Front Overview | Reolink NVR | 192.168.0.245:443 | 6 | Yes |
| `door_bell` | DoorBell | Reolink NVR | 192.168.0.245:443 | 1 | Yes |
| `dhinvr543216pi` | DHI-NVR5432-16P-I | Dahua NVR | 192.168.0.50:443 | 1 | Yes (ONVIF+CGI) |

## Known Issues / Quirks

- `door_bell` GetPresets fails with `'value'` key error — Reolink doorbell doesn't expose preset list via NVR API; returns empty list silently
- Dahua `pan_invert: true` / `tilt_invert: true` needed — this NVR's PositionABS pan/tilt axes are inverted relative to the default convention
- Dahua `dahua_click_zoom: 0` — arg3=0 maintains minimum zoom; adjust if camera needs a different rest zoom

## PyInstaller Build

```bat
build.bat
```
Produces a single `.exe` with go2rtc bundled. System tray icon (no CMD window).
