#
# Version: 1.0
# Updated: 2026-05-18
#
# v1.0 - 2026-05-18 - Initial extraction from ptzlite.py monolith
#

import logging
import sys
from pathlib import Path

from core.paths import _USER_DIR


def setup_logging() -> None:
    """Configure the root 'PTZLite' logger with FileHandler and StreamHandler.
    Must be called at the top of main() before anything else.
    """
    log_path = _USER_DIR / "ptzlite.log"
    root = logging.getLogger("PTZLite")
    root.setLevel(logging.DEBUG)

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # File handler — always write to ptzlite.log
    try:
        fh = logging.FileHandler(log_path, encoding="utf-8", errors="replace")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except Exception as e:
        print(f"WARNING: Could not open log file {log_path}: {e}", file=sys.stderr)

    # Stream handler — stdout (visible in console / dev mode)
    sh = logging.StreamHandler(sys.stdout)
    sh.setLevel(logging.INFO)
    sh.setFormatter(fmt)
    root.addHandler(sh)


def get_logger(name: str) -> logging.Logger:
    """Return a child logger under the PTZLite hierarchy."""
    return logging.getLogger(f"PTZLite.{name}")
