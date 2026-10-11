#
# Version: 1.1
# Updated: 2026-07-28
#
# v1.0 - 2026-05-18 - Initial extraction from ptzlite.py monolith
# v1.1 - 2026-07-28 - Add _rtsp_url_talk (backchannel-enabled sub stream for two-way audio)
#

import base64
import hashlib
import logging
import re
import socket
import urllib.parse

logger = logging.getLogger("PTZLite.services.rtsp")


def _rtsp_url(cam: dict, stream: str | None = None) -> str:
    ch = cam.get("channel", 0)
    s = stream if stream is not None else cam.get("stream", "main")
    brand = cam.get("brand", "reolink").lower()
    # #backchannel=0 disables two-way audio; #rtsp_transport=tcp forces TCP for RTP data
    # (go2rtc defaults to UDP which many Reolink/Dahua NVRs silently drop, causing CONN-state loops)
    _opts = "#backchannel=0#rtsp_transport=tcp"
    if brand == "dahua":
        subtype = 0 if s == "main" else 1
        return (
            f"rtsp://{cam['username']}:{cam['password']}"
            f"@{cam['ip']}:554/cam/realmonitor?channel={ch}&subtype={subtype}{_opts}"
        )
    # Reolink: channel is 0-indexed in config, 1-indexed in URL
    return (
        f"rtsp://{cam['username']}:{cam['password']}"
        f"@{cam['ip']}:554/h264Preview_{ch + 1:02d}_{s}{_opts}"
    )


def _rtsp_url_talk(cam: dict) -> str:
    """Backchannel-ENABLED sub-stream URL for two-way audio (push-to-talk).

    Same as the sub stream but with #backchannel=1 so go2rtc routes the browser's
    microphone audio into the camera's RTSP backchannel (its speaker).
    """
    ch = cam.get("channel", 0)
    _opts = "#backchannel=1#rtsp_transport=tcp"
    if cam.get("brand", "reolink").lower() == "dahua":
        return (
            f"rtsp://{cam['username']}:{cam['password']}"
            f"@{cam['ip']}:554/cam/realmonitor?channel={ch}&subtype=1{_opts}"
        )
    return (
        f"rtsp://{cam['username']}:{cam['password']}"
        f"@{cam['ip']}:554/h264Preview_{ch + 1:02d}_sub{_opts}"
    )


def _clean_rtsp_url(cam: dict, stream: str) -> str:
    """RTSP URL without embedded credentials and without go2rtc-specific options.
    Used for the DESCRIBE test — real cameras don't understand the #opts syntax.
    NOTE: cam['port'] is the HTTP API port (e.g. 443), NOT the RTSP port.
    RTSP is always on port 554."""
    ch    = cam.get("channel", 0)
    brand = cam.get("brand", "reolink").lower()
    ip    = cam.get("ip", "")
    if brand == "dahua":
        subtype = 0 if stream == "main" else 1
        return f"rtsp://{ip}:554/cam/realmonitor?channel={ch}&subtype={subtype}"
    return f"rtsp://{ip}:554/h264Preview_{ch + 1:02d}_{stream}"


def _rtsp_describe_sync(url: str, user: str, password: str, timeout: float = 5.0) -> dict:
    """
    Send RTSP DESCRIBE to the exact stream URL with Digest/Basic auth.
    Returns {"ok": True} or {"ok": False, "error": "..."}.
    Uses raw sockets so we can handle the 401 -> re-auth dance without ffmpeg.
    """
    parsed = urllib.parse.urlparse(url)
    host = parsed.hostname or ""
    port = parsed.port or 554

    def _send_recv(sock: socket.socket, msg: str) -> str:
        sock.sendall(msg.encode())
        buf = b""
        sock.settimeout(timeout)
        try:
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                buf += chunk
                # RTSP responses end with \r\n\r\n (headers done)
                if b"\r\n\r\n" in buf:
                    break
        except socket.timeout:
            pass
        return buf.decode("utf-8", errors="replace")

    try:
        sock = socket.create_connection((host, port), timeout=timeout)
    except OSError as e:
        return {"ok": False, "error": f"Cannot reach {host}:{port} — {e}"}

    try:
        # Step 1: unauthenticated DESCRIBE to get the WWW-Authenticate challenge
        req1 = (
            f"DESCRIBE {url} RTSP/1.0\r\n"
            f"CSeq: 1\r\n"
            f"User-Agent: ReoViewer/1.0\r\n"
            f"Accept: application/sdp\r\n\r\n"
        )
        resp1 = _send_recv(sock, req1)

        if not resp1:
            return {"ok": False, "error": f"No response from {host}:{port}"}
        if "RTSP/1.0" not in resp1:
            return {"ok": False, "error": f"Not an RTSP server at {host}:{port}"}

        status_line = resp1.split("\r\n")[0]
        code = int(status_line.split()[1]) if len(status_line.split()) >= 2 else 0

        # 200 with no auth required
        if code == 200:
            return {"ok": True, "message": f"Stream confirmed at {url}"}

        # 404 / 4xx means the channel/URL doesn't exist
        if code == 404:
            return {"ok": False, "error": f"Stream URL not found (channel may not exist): {url}"}
        if code not in (401, 403):
            return {"ok": False, "error": f"RTSP server returned {code} for {url}"}

        # Step 2: parse WWW-Authenticate and retry with credentials
        auth_header = ""
        for line in resp1.split("\r\n"):
            if line.lower().startswith("www-authenticate:"):
                auth_header = line[len("www-authenticate:"):].strip()
                break

        auth_str = ""
        if auth_header.lower().startswith("digest"):
            # Digest auth
            realm_m  = re.search(r'realm="([^"]*)"',  auth_header)
            nonce_m  = re.search(r'nonce="([^"]*)"',  auth_header)
            realm = realm_m.group(1)  if realm_m  else ""
            nonce = nonce_m.group(1)  if nonce_m  else ""
            ha1 = hashlib.md5(f"{user}:{realm}:{password}".encode()).hexdigest()
            ha2 = hashlib.md5(f"DESCRIBE:{url}".encode()).hexdigest()
            resp_hash = hashlib.md5(f"{ha1}:{nonce}:{ha2}".encode()).hexdigest()
            auth_str = (
                f'Digest username="{user}", realm="{realm}", nonce="{nonce}", '
                f'uri="{url}", response="{resp_hash}"'
            )
        else:
            # Basic auth fallback
            creds = base64.b64encode(f"{user}:{password}".encode()).decode()
            auth_str = f"Basic {creds}"

        req2 = (
            f"DESCRIBE {url} RTSP/1.0\r\n"
            f"CSeq: 2\r\n"
            f"User-Agent: ReoViewer/1.0\r\n"
            f"Accept: application/sdp\r\n"
            f"Authorization: {auth_str}\r\n\r\n"
        )
        resp2 = _send_recv(sock, req2)
        code2 = 0
        if resp2:
            sl = resp2.split("\r\n")[0]
            code2 = int(sl.split()[1]) if len(sl.split()) >= 2 else 0

        if code2 == 200:
            return {"ok": True, "message": f"Stream confirmed at {url}"}
        if code2 == 401:
            return {"ok": False, "error": "Authentication failed — check username/password"}
        if code2 == 404:
            return {"ok": False, "error": f"Stream URL not found (channel may not exist): {url}"}
        return {"ok": False, "error": f"RTSP server returned {code2} (expected 200)"}

    finally:
        try:
            sock.close()
        except Exception:
            pass
