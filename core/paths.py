#
# Version: 1.3
# Updated: 2026-10-29
#
# v1.0 - 2026-05-18 - Initial extraction from ptzlite.py monolith
# v1.1 - 2026-10-12 - go2rtc.exe found in bin\ or project root; APP_VERSION 5.45 (single source)
# v1.2 - 2026-10-12 - APP_VERSION 5.46 (disclaimer accept page, no Google Fonts)
# v1.3 - 2026-10-29 - APP_VERSION 5.47 (port clash handling)
#

import sys
from pathlib import Path

# When frozen by PyInstaller: user files (config, go2rtc) live next to the .exe;
# bundled resources (static/, bin/) are extracted to sys._MEIPASS (_internal/) at runtime.
if getattr(sys, 'frozen', False):
    _USER_DIR   = Path(sys.executable).parent
    _BUNDLE_DIR = Path(sys._MEIPASS)        # type: ignore[attr-defined]
else:
    # __file__ is <root>/core/paths.py — go up two levels to reach project root
    _USER_DIR   = Path(__file__).parent.parent
    _BUNDLE_DIR = Path(__file__).parent.parent

BASE        = _USER_DIR
CONFIG_PATH = BASE / "config.yaml"
# go2rtc.exe is a bundled read-only binary — use _BUNDLE_DIR (sys._MEIPASS) when frozen.
# From source, bin\go2rtc.exe or go2rtc.exe in the project root both work (the build
# scripts take it from the root and bundle it into bin\).
GO2RTC_BIN  = _BUNDLE_DIR / "bin" / "go2rtc.exe"
if not GO2RTC_BIN.exists() and (_BUNDLE_DIR / "go2rtc.exe").exists():
    GO2RTC_BIN = _BUNDLE_DIR / "go2rtc.exe"
GO2RTC_PORT = 1984
_STATIC_DIR = _BUNDLE_DIR / "static"

# The app's release version — the one place to change it. Shown in the viewer and
# About page, logged at startup, and stamped on the installer by build.bat.
APP_VERSION = "5.47"
