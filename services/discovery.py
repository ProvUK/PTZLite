#
# Version: 1.0
# Updated: 2026-05-18
#
# v1.0 - 2026-05-18 - Initial extraction from ptzlite.py monolith
#

# ── ONVIF WS-Discovery ─────────────────────────────────────────────────────────

import logging
import re
import socket
import time
import uuid
import xml.etree.ElementTree as ET

logger = logging.getLogger("PTZLite.services.discovery")

_WSD_MCAST = ("239.255.255.250", 3702)

_WSD_PROBE_TMPL = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<Envelope xmlns="http://www.w3.org/2003/05/soap-envelope"'
    ' xmlns:dn="http://www.onvif.org/ver10/network/wsdl"'
    ' xmlns:wsa="http://schemas.xmlsoap.org/ws/2004/08/addressing"'
    ' xmlns:wsdd="http://schemas.xmlsoap.org/ws/2005/04/discovery">'
    "<Header>"
    "<wsa:Action>http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe</wsa:Action>"
    "<wsa:MessageID>urn:uuid:{uid}</wsa:MessageID>"
    "<wsa:To>urn:schemas-xmlsoap-org:ws:2005:04:discovery</wsa:To>"
    "</Header>"
    "<Body>"
    "<wsdd:Probe><wsdd:Types>dn:NetworkVideoTransmitter</wsdd:Types></wsdd:Probe>"
    "</Body>"
    "</Envelope>"
)

_NS_WSDD = "http://schemas.xmlsoap.org/ws/2005/04/discovery"


def _parse_wsd_response(data: bytes, sender_ip: str) -> dict | None:
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return None

    xaddrs_el = root.find(f".//{{{_NS_WSDD}}}XAddrs")
    xaddrs = xaddrs_el.text.strip() if xaddrs_el is not None and xaddrs_el.text else ""

    # Prefer IP from XAddr URL over UDP sender (avoids NAT surprises on same host)
    ip = sender_ip
    if xaddrs:
        m = re.search(r"https?://([0-9]+\.[0-9]+\.[0-9]+\.[0-9]+)", xaddrs)
        if m:
            ip = m.group(1)

    model = ""
    scopes_el = root.find(f".//{{{_NS_WSDD}}}Scopes")
    if scopes_el is not None and scopes_el.text:
        m = re.search(r"onvif://www\.onvif\.org/hardware/([^\s]+)", scopes_el.text)
        if m:
            model = m.group(1).replace("%20", " ").replace("+", " ")

    return {"ip": ip, "model": model}


def _local_ipv4_addrs() -> list[str]:
    """Return all non-loopback IPv4 addresses on this machine (one per interface)."""
    addrs: list[str] = []
    try:
        # getaddrinfo with the hostname gives one address per interface on most Windows configs
        for _family, _type, _proto, _canon, sockaddr in socket.getaddrinfo(
            socket.gethostname(), None, socket.AF_INET
        ):
            ip = sockaddr[0]
            if not ip.startswith("127.") and ip not in addrs:
                addrs.append(ip)
    except Exception as e:
        logger.error("WSD: getaddrinfo failed: %s", e)
    # Always include the wildcard as fallback so we have at least one socket
    if not addrs:
        addrs = ["0.0.0.0"]
    return addrs


def _ws_discover(timeout: float = 4.0) -> list[dict]:
    """Send WS-Discovery probe on every local interface and collect ProbeMatch replies."""
    probe = _WSD_PROBE_TMPL.format(uid=uuid.uuid4()).encode()
    results: list[dict] = []
    seen: set[str] = set()

    local_addrs = _local_ipv4_addrs()
    logger.info("WSD: probing via interfaces: %s", local_addrs)

    socks: list[socket.socket] = []
    for addr in local_addrs:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 4)
            if addr != "0.0.0.0":
                # Pin multicast to this specific interface
                s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF,
                             socket.inet_aton(addr))
            s.bind((addr, 0))
            s.settimeout(0.3)
            s.sendto(probe, _WSD_MCAST)
            socks.append(s)
            logger.info("WSD: probe sent from %s", addr)
        except Exception as e:
            logger.warning("WSD: skip %s: %s", addr, e)
            try:
                s.close()
            except Exception:
                pass

    if not socks:
        logger.warning("WSD: no sockets available")
        return results

    try:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for s in socks:
                try:
                    data, addr_tuple = s.recvfrom(65535)
                    result = _parse_wsd_response(data, addr_tuple[0])
                    if result and result["ip"] not in seen:
                        seen.add(result["ip"])
                        results.append(result)
                        logger.info("WSD found: %s  model=%r", result["ip"], result["model"])
                except socket.timeout:
                    pass
                except Exception as e:
                    logger.error("WSD recv error: %s", e)
    finally:
        for s in socks:
            s.close()

    logger.info("WSD: done, found %d device(s)", len(results))
    return results
