#
# Version: 1.0
# Updated: 2026-05-18
#
# v1.0 - 2026-05-18 - Initial extraction from ptzlite.py monolith
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
# go2rtc.exe is a bundled read-only binary — use _BUNDLE_DIR (sys._MEIPASS) when frozen
GO2RTC_BIN  = _BUNDLE_DIR / "bin" / "go2rtc.exe"
GO2RTC_PORT = 1984
_STATIC_DIR = _BUNDLE_DIR / "static"

APP_VERSION = "5.33"
