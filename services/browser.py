#
# Version: 1.0
# Updated: 2026-05-21
#
# v1.0 - 2026-05-21 - Browser detection and app-window launcher extracted from ptzlite.py
#

import logging
import os
import subprocess
import webbrowser
from pathlib import Path

from core.config import _config
from core.paths import BASE

logger = logging.getLogger("PTZLite.services.browser")


def find_app_browser() -> str | None:
    """Return the path to Chrome or Edge if either is installed on this machine, else None."""
    local = os.environ.get("LOCALAPPDATA", "")
    candidates = [
        # Chrome — user install (most common on Windows), then system-wide
        os.path.join(local, r"Google\Chrome\Application\chrome.exe"),
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        # Edge — bundled with Windows 10/11, usually in Program Files (x86)
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        os.path.join(local, r"Microsoft\Edge\Application\msedge.exe"),
    ]
    for path in candidates:
        if path and Path(path).exists():
            return path
    return None


def open_viewer(port: int) -> None:
    """Open the PTZLite viewer.

    When app_window mode is configured and Chrome/Edge is available, launches the
    browser with --app so the window has no address bar, tabs, or toolbar, and
    gets its own taskbar icon.  Falls back to the system default browser otherwise.

    A dedicated --user-data-dir keeps the profile separate from the user's main
    browser, ensuring the app window size is remembered between sessions.
    """
    url = f"http://localhost:{port}"
    if _config.get("app_window", False):
        browser = find_app_browser()
        if browser:
            profile_dir = str(BASE / "browser_profile")
            try:
                subprocess.Popen(
                    [browser, f"--app={url}", f"--user-data-dir={profile_dir}"],
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                logger.info("Opened viewer in app window (%s)", Path(browser).name)
                return
            except Exception as e:
                logger.warning("Could not launch app window: %s — falling back to browser", e)
        else:
            logger.warning(
                "App window mode is enabled but Chrome/Edge was not found — "
                "falling back to default browser"
            )
    webbrowser.open(url)
