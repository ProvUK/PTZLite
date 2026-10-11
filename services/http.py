#
# Version: 1.0
# Updated: 2026-10-11
#
# v1.0 - 2026-10-11 - Shared aiohttp session for camera API calls
#
"""One pooled HTTP client for all camera control calls.

Previously every PTZ command opened a brand-new aiohttp session, i.e. a fresh
TCP + TLS handshake to the NVR per click. A shared session keeps connections
alive between calls. Cameras use self-signed certificates, so TLS verification
is off (as it was before).
"""

import asyncio

import aiohttp

_session: aiohttp.ClientSession | None = None
_session_loop: asyncio.AbstractEventLoop | None = None

# Errors that mean "the pooled keep-alive connection was closed by the camera";
# safe to retry once on a fresh connection.
STALE_CONN_ERRORS = (aiohttp.ServerDisconnectedError, aiohttp.ClientOSError)


def get_session() -> aiohttp.ClientSession:
    """Return the shared session, (re)creating it for the current event loop."""
    global _session, _session_loop
    loop = asyncio.get_running_loop()
    if _session is None or _session.closed or _session_loop is not loop:
        connector = aiohttp.TCPConnector(
            ssl=False,
            limit_per_host=4,
            keepalive_timeout=15,   # cameras drop idle sockets; don't reuse stale ones
        )
        _session = aiohttp.ClientSession(connector=connector)
        _session_loop = loop
    return _session


async def close_session() -> None:
    global _session
    if _session is not None and not _session.closed:
        await _session.close()
    _session = None
