<p align="center">
  <img src="static/main-logo.png" alt="PTZLite" height="72">
</p>

<p align="center">
  <strong>A fast, lightweight IP camera viewer for Windows — with click-to-centre PTZ, live detection alerts and a phone-friendly web UI.</strong>
</p>

---

PTZLite runs quietly in your system tray and shows all your cameras in one low-latency grid. It talks to **Reolink**, **Dahua** and other **ONVIF** cameras directly over your local network: no cloud, no account, nothing leaves your network.

Video is handled by the excellent [go2rtc](https://github.com/AlexxIT/go2rtc) (RTSP → WebRTC/MSE, no transcoding), and the viewer is a web page, so you can open it from the PC app window **or** from any phone, tablet or browser on your network.

## Contents

- [Features](#features)
- [Supported cameras](#supported-cameras)
- [Requirements](#requirements)
- [Installation](#installation)
  - [Option 1 — Windows installer (recommended)](#option-1--windows-installer-recommended)
  - [Option 2 — Run from source](#option-2--run-from-source)
  - [Option 3 — Build the installer yourself](#option-3--build-the-installer-yourself)
- [First run](#first-run)
- [Using PTZLite](#using-ptzlite)
- [Viewing on your phone](#viewing-on-your-phone)
- [Calibrating click-to-centre](#calibrating-click-to-centre)
- [Configuration reference](#configuration-reference)
- [Network, ports & security](#network-ports--security)
- [Updating](#updating)
- [Uninstalling](#uninstalling)
- [Troubleshooting](#troubleshooting)
- [Development](#development)
- [Credits](#credits)

---

## Features

**Viewing**
- Multi-camera grid: 1×1, 2×1, 2×2, 3×2, 3×3 and 4×4, with named pages/groups and paging
- Main / Sub stream switch (full quality vs low bandwidth), per-camera fullscreen with its own Main/Sub toggle
- Low-latency WebRTC with automatic MSE fallback; streams pre-warmed in the background so switching is instant
- Digital zoom & pan (scroll wheel), fill-frame crop mode, labels on/off, snapshot, audio on/off
- "Keep live" mode so streams don't pause when the window is hidden
- Automatic reconnect with stream-health indicators

**PTZ control** (detected per camera at runtime: features appear only on cameras that support them)
- **Double-click to centre**: the clicked point becomes the middle of the picture
- **Drag joystick**: hold and drag for continuous pan/tilt at variable speed
- **Optical zoom**: right-button + scroll, or the zoom slider in the camera's controls menu
- **Presets**: go to, and save the current view to the next free slot; "monitor" preset to return to
- Zoom-aware aiming: reads the camera's current zoom so click-to-centre stays accurate when zoomed in
- Built-in **auto** and **manual** aim calibration

**Camera controls** (in each camera's **Camera controls** menu on the tile, where supported)
- Two-way talk (tap to talk through the camera's speaker)
- Floodlight / spotlight on/off and brightness, IR night-vision mode (Auto / On / Off)
- Siren, patrol start/stop, guard position (auto-return), AI auto-tracking

**Detection & alerts**
- Live motion / person / vehicle / animal detection from Reolink cameras, shown as a badge and glowing border on the tile
- Header alert chip (click to jump to the camera) and an optional chime

**Everywhere**
- Phone and tablet layout with a bottom navigation bar; turn the phone sideways for an edge-to-edge picture
- Skins: **Neon** (default), **Aurora**, **Tactical** and **Classic** (with light mode)
- Optional password protection with login page
- System tray icon and **Ctrl+Alt+Z** global hotkey to open the viewer
- Network scan to discover cameras, camera export/import, enable/disable/reorder cameras

## Supported cameras

| Brand | Video | PTZ / click-to-centre | Extras |
|---|---|---|---|
| **Reolink** (cameras, NVRs, Home Hub) | RTSP | Reolink HTTP API, timed moves + calibration | Presets, zoom, talk, lights, siren, patrol, guard, tracking, detection |
| **Dahua** (cameras, NVRs) | RTSP | Absolute positioning (exact), falls back to ONVIF | Presets, zoom |
| **Other ONVIF** (Hikvision, etc.) | RTSP | ONVIF ContinuousMove, timed moves + calibration | Presets, zoom |

Reolink and Dahua are chosen in the Settings page. For another ONVIF camera, add it as Dahua and
then change `brand: dahua` to `brand: onvif` in `config.yaml` (any brand other than `reolink` uses
ONVIF for PTZ; `dahua` additionally tries Dahua's exact positioning first).

## Requirements

- **Windows 10 or 11** (64-bit)
- Cameras on the **same local network** as the PC, with **RTSP enabled** in each camera's network settings
- A modern browser for phone/tablet viewing (Chrome, Edge, Safari, Firefox)

To run from source or build it yourself you also need:

- [**Git**](https://git-scm.com/download/win)
- [**Python 3.11 or newer**](https://www.python.org/downloads/windows/) (tick *"Add python.exe to PATH"* during setup)
- [**go2rtc for Windows**](https://github.com/AlexxIT/go2rtc/releases): `go2rtc_win64.zip` (tested with 1.9.14)
- [**Inno Setup 6**](https://jrsoftware.org/isinfo.php) (only to build the installer)

---

## Installation

### Option 1 — Windows installer (recommended)

1. Download **`PTZLite Setup.exe`** from the [Releases](https://github.com/ProvUK/PTZLite/releases) page.
   *(If there's no release yet, build it with [Option 3](#option-3--build-the-installer-yourself).)*
2. Run it. No admin rights are needed: PTZLite installs per-user to
   `%LOCALAPPDATA%\Programs\PTZLite`.
3. Optionally tick **Create a desktop shortcut** and **Start automatically with Windows**.
4. If Windows asks to allow elevation, accepting lets the installer add a **firewall rule** so camera
   discovery works and phones can connect. Declining is fine; see [Network, ports & security](#network-ports--security).
5. Finish, and PTZLite starts and opens the viewer.

Your settings live in `config.yaml` in the install folder. **Upgrading never overwrites it.**

### Option 2 — Run from source

Open **PowerShell** and run:

```powershell
# 1. Get the code
git clone https://github.com/ProvUK/PTZLite.git
cd PTZLite

# 2. (Recommended) create an isolated Python environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 3. Install the Python dependencies
pip install -r requirements.txt

# 4. Put go2rtc where the app expects it (see note below)
#    Download go2rtc_win64.zip from https://github.com/AlexxIT/go2rtc/releases,
#    extract go2rtc.exe into this folder, then:
mkdir bin
copy go2rtc.exe bin\go2rtc.exe

# 5. Create your config from the example
copy config.example.yaml config.yaml

# 6. Run it
python ptzlite.py
```

The viewer opens at **http://localhost:8765**. Add cameras from **Settings** (gear icon), or edit `config.yaml` directly.

> **go2rtc location:** when running from source the app looks for `bin\go2rtc.exe`; the build scripts
> look for `go2rtc.exe` in the project root (they bundle it into `bin\`). Keeping a copy in both places
> lets you do either. Both are ignored by Git.

> **PowerShell says scripts are disabled?** Run
> `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once, or activate the venv from
> Command Prompt with `.venv\Scripts\activate.bat`.

### Option 3 — Build the installer yourself

```powershell
git clone https://github.com/ProvUK/PTZLite.git
cd PTZLite
# place go2rtc.exe in this folder (from go2rtc_win64.zip)
.\build.bat
```

`build.bat` installs the dependencies plus PyInstaller, builds the app into `dist\PTZLite\`, and, if
Inno Setup 6 is installed, produces **`PTZLite Setup.exe`** in the project folder. Run that installer
as in [Option 1](#option-1--windows-installer-recommended).

Without Inno Setup you can run the build directly: `dist\PTZLite\PTZLite.exe` (copy
`config.example.yaml` next to it as `config.yaml` first).

---

## First run

1. PTZLite starts in the **system tray** and opens the viewer. The grid is empty until you add cameras.
2. Click **Settings** (gear icon, top right) → **Add camera** and fill in:
   - **Brand**: Reolink or Dahua (see [Supported cameras](#supported-cameras) for other ONVIF cameras)
   - **IP**: the camera's (or NVR/Hub's) IP address
   - **Port**: management port, usually **443** (HTTPS) or **80**
   - **Username / password**: the camera's login
   - **Channel**: Reolink is **0-based** (first camera = 0); Dahua is **1-based** (first camera = 1)
   - **PTZ**: tick for pan/tilt cameras and set the field of view (see below)
3. Click **Test** to check the stream, then **Save**. The camera appears straight away.

Or use **Scan network** in Settings to find cameras automatically.

> **Reolink Home Hub / NVR:** use the Hub's IP and login, and one entry per camera channel.
> If a camera shows a black screen, switch to the **Sub** stream (some main streams are H.265,
> which not every browser can decode).

## Using PTZLite

| Action | Mouse | Touch |
|---|---|---|
| Centre the camera on a point | **Double-click** | **Double-tap** |
| Continuous pan/tilt | Click and **drag** | Touch and drag |
| Digital zoom / pan | Scroll wheel, then drag | — |
| Optical zoom | Hold **right button + scroll** | Zoom slider in Camera controls |
| Fullscreen one camera | **Right-click** (again or Esc to exit) | ⛶ button / turn sideways |
| Camera controls (lights, talk, siren…) | **Camera controls** button on the tile | Same |
| Open viewer from anywhere | **Ctrl+Alt+Z** | — |

A single click never moves a camera, so a stray click is harmless. The full guide is built in: click **?** in the viewer header.

## Viewing on your phone

1. Make sure the phone is on the **same Wi-Fi/network** as the PC running PTZLite.
2. Find the PC's IP address (`ipconfig` in PowerShell → *IPv4 Address*, e.g. `192.168.0.20`).
3. On the phone, open **`http://<PC-IP>:8765`**, e.g. `http://192.168.0.20:8765`.
4. Log in if you've set a password.

The phone layout shows one camera at a time, with arrows to step through cameras. **Turn the phone sideways** to hide the bars; on Android, one tap then goes fully fullscreen.

> Two-way talk from a phone needs microphone access, which browsers only allow on HTTPS pages,
> so talk works from the PC app but not from a phone over plain `http://`.

If the phone can't connect, see [Network, ports & security](#network-ports--security).

## Calibrating click-to-centre

Most cameras don't report their exact position, so PTZLite times each move. For accurate centring:

1. Set the camera's **Horizontal / Vertical FoV** in Settings (from the camera's datasheet).
2. On the camera tile, click the **Calibrate** button (tooltip: *Calibrate click-to-centre aim*):
   - **Auto**: measures the motor speed itself by watching the picture. Best on a scene with clear,
     non-repeating detail (cars, buildings), not sky or plain walls.
   - **Manual**: double-click an object near the edge, then click where it ended up; repeat a couple
     of times until it lands in the middle.
   - **Reset**: back to defaults.
3. **Zoom cameras:** set **Optical zoom (×)** to the camera's spec (e.g. 4 for a 4× camera). Then zoom
   **fully in** and run **Manual** calibration. While zoomed in it fine-tunes the zoom figure instead of
   the aim.

Dahua cameras that support absolute positioning don't need calibration.

## Configuration reference

All settings can be changed in the **Settings** page; they're stored in `config.yaml` (next to
`PTZLite.exe`, or in the project folder when running from source). See
[`config.example.yaml`](config.example.yaml) for a template.

| Key | Default | Meaning |
|---|---|---|
| `id` | — | Unique short name used in URLs (e.g. `front_door`) |
| `name` | — | Display name |
| `brand` | `reolink` | `reolink`, `dahua`, or `onvif` for other ONVIF cameras |
| `ip` / `port` | — / `443` | Camera or NVR address and management port |
| `username` / `password` | — | Camera login |
| `channel` | `0` | Reolink 0-based, Dahua 1-based |
| `stream` | `main` | Default stream: `main` or `sub` |
| `enabled` | `true` | Hide the camera without deleting it |
| `page` | — | Page/group name to show the camera on |
| `ptz` | `false` | Enable PTZ controls |
| `fov_h` / `fov_v` | `90` / `55` | Field of view in degrees, used by click-to-centre |
| `zoom_max_x` | `1` | Optical zoom at full zoom-in (1 = don't adjust aim for zoom) |
| `ptz_speed` | `20` | Motor speed 1–63 |
| `ptz_degrees_per_second` | `20` | Rotation speed used to time moves (set by auto-calibration per axis) |
| `pan_degrees_per_second` / `tilt_degrees_per_second` | — | Optional per-axis overrides |
| `pan_cal` / `tilt_cal` | `1.0` | Fine-tuning set by manual calibration |
| `ptz_min_move_s` | `0.1` | Shortest motor pulse |
| `pan_invert` / `tilt_invert` | `false` | Reverse an axis (ceiling-mounted cameras) |
| `zoom_speed` / `zoom_step_s` | `40` / `0.3` | Optical zoom speed and time per scroll step |
| `monitor_preset` | — | Preset to return to |
| `ptz_ip` / `ptz_port` | — / `80` | Send PTZ straight to the camera instead of via the NVR |
| `onvif_profile` / `onvif_ptz_path` | auto / `/onvif/ptz_service` | ONVIF overrides |

## Network, ports & security

| Port | Used for | Who connects |
|---|---|---|
| **8765** TCP | PTZLite web UI and API | Your browser / phone |
| **8555** TCP/UDP | WebRTC video | Your browser / phone |
| 1984 | go2rtc internal API | **Local only**: not reachable from the network |
| 554 / 443 / 80 | RTSP and camera API | PTZLite → cameras |

- **Password:** set one in **Settings → Security**. Every page, API call and video stream then requires
  login, from any device. Saved passwords are never sent back to the browser.
- **Phone can't connect?** Allow PTZLite through Windows Firewall (the installer does this when allowed to elevate). To add the rule manually, run PowerShell **as administrator**:
  ```powershell
  New-NetFirewallRule -DisplayName "PTZLite" -Direction Inbound -Action Allow `
    -Program "$env:LOCALAPPDATA\Programs\PTZLite\PTZLite.exe" -Profile Private,Domain
  ```
- PTZLite is designed for your **home/office network**. Don't expose port 8765 to the internet; use
  a VPN (e.g. WireGuard/Tailscale) for remote access.

## Updating

- **Installer:** install the new `PTZLite Setup.exe` over the top (close PTZLite first). Your
  `config.yaml` and cameras are kept.
- **From source:**
  ```powershell
  cd PTZLite
  git pull
  pip install -r requirements.txt
  python ptzlite.py
  ```

## Uninstalling

Use **Settings → Apps → Installed apps → PTZLite → Uninstall** (this also removes the firewall rule).
Your `config.yaml` is left in `%LOCALAPPDATA%\Programs\PTZLite` in case you reinstall; delete
that folder to remove it.

## Troubleshooting

| Problem | Try |
|---|---|
| **Black screen / "stream offline"** | Check RTSP is enabled on the camera; try the **Sub** stream (H.265 main streams don't play in every browser); click the tile's reconnect |
| **Every feed offline after an update** | Press **Ctrl+F5** to reload the viewer |
| **Double-click moves the wrong way** | Tick **Pan/Tilt invert** for that camera |
| **Double-click over/undershoots** | Check the FoV values, then run **Calibrate** |
| **Overshoots badly when zoomed in** | Set **Optical zoom (×)** and calibrate while fully zoomed in |
| **Phone can't open the page** | Same network? Correct PC IP? Firewall rule? (see above) |
| **Ctrl+Alt+Z does nothing** | Another app has claimed the shortcut; use the tray icon |
| **Anything else** | **Settings → View log**, or open `ptzlite.log` in the install folder |

## Development

```powershell
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest -q          # test suite (uses a temporary config, never your real one)
.\devbuild.bat               # fast rebuild of dist\PTZLite (no installer)
```

**Project layout**

```
ptzlite.py            App entry: FastAPI app, auth middleware, tray icon, hotkey, startup
core/                 Config load/save, paths, logging
api/                  HTTP routes: auth, cameras, PTZ & camera controls, detection (SSE), go2rtc proxy
services/             go2rtc process, RTSP URLs, discovery, browser launch, shared HTTP session
services/ptz/         Reolink HTTP API, Dahua CGI, ONVIF SOAP
static/               Web UI: index.html, settings, help, login; js/ modules; css/ + skins/
tests/                pytest suite
build.bat             Full build: PyInstaller + Inno Setup installer (installer.iss)
devbuild.bat          Quick PyInstaller rebuild for testing
```

**Branches:** `main` is the stable release branch; day-to-day work happens on `dev` and is merged
into `main` when tested.

## Credits

- [go2rtc](https://github.com/AlexxIT/go2rtc) by AlexxIT: the streaming engine that makes this possible
- [FastAPI](https://fastapi.tiangolo.com/), [Uvicorn](https://www.uvicorn.org/), [aiohttp](https://docs.aiohttp.org/),
  [pystray](https://github.com/moses-palmer/pystray), [Pillow](https://python-pillow.org/)

Reolink and Dahua are trademarks of their respective owners. PTZLite is an independent project and is
not affiliated with or endorsed by either company.
