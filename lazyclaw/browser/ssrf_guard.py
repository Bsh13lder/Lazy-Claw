"""SSRF guard for agent-fetched URLs.

SSRF (Server-Side Request Forgery): the *server* fetches a URL chosen by input
that may be untrusted — a web page told us to, a DM contained a link, a tool
result embedded one. Whoever controls that URL can reach things only the
server can: cloud metadata (``169.254.169.254`` → IAM credentials), the
gateway's own internal API, the database. Defense: inspect the target host and
refuse internal addresses *before* any fetch/navigation.

Two policies, picked by **who chose the URL**:

* :func:`is_blocked_ssrf_target` — STRICT. Blocks every internal range
  (loopback, private, link-local, reserved, multicast, unspecified) plus the
  metadata endpoints. Resolves DNS, so a public-looking name that maps to a
  private IP (DNS rebinding) is still caught. Use on CONTENT-TRIGGERED fetches
  (scraper / web_search): content has no legitimate reason to reach inside.

* :func:`is_metadata_or_linklocal` — MINIMAL, literal-only (no DNS, so it adds
  no latency to navigation). Blocks only the cloud-metadata hosts and
  link-local (``169.254.0.0/16`` / ``fe80::/10``). Use on USER-DRIVEN
  navigation: the human may legitimately open ``localhost:3000`` for their own
  dev server, but never has a reason to hit the metadata IP — a page that
  redirected them there is an attack.
"""

from __future__ import annotations

import ipaddress
import logging
import socket
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Cloud metadata services (AWS / Azure / GCP / Alibaba) — reaching these leaks
# instance credentials. GCP also exposes a hostname alias.
_METADATA_HOSTS = frozenset(
    {
        "169.254.169.254",  # AWS / Azure / GCP / DigitalOcean / OpenStack
        "metadata.google.internal",
        "100.100.100.200",  # Alibaba Cloud
    }
)

# Names that always resolve to the local box / Docker host.
_BLOCKED_HOSTNAMES = frozenset({"localhost", "host.docker.internal"})


def _host_of(url: str) -> str:
    return (urlparse(url).hostname or "").strip().lower().rstrip(".")


def _as_ip(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        return None


def _is_internal(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return bool(
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _resolved_ips(host: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    """Return the host's IP(s): the literal if it is one, else DNS-resolved."""
    literal = _as_ip(host)
    if literal is not None:
        return [literal]
    try:
        infos = socket.getaddrinfo(host, None)
    except (socket.gaierror, OSError):
        return []
    out: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for info in infos:
        ip = _as_ip(str(info[4][0]))
        if ip is not None:
            out.append(ip)
    return out


def is_blocked_ssrf_target(url: str) -> bool:
    """STRICT block: internal ranges + metadata, DNS-rebind aware.

    For content-triggered fetches where an internal address is never legitimate.
    """
    host = _host_of(url)
    if not host:
        return False
    if host in _BLOCKED_HOSTNAMES or host in _METADATA_HOSTS:
        logger.warning("SSRF guard blocked internal host: %s", host)
        return True
    ips = _resolved_ips(host)
    # Unresolvable host can't be fetched anyway — don't false-positive on it.
    if ips and any(_is_internal(ip) for ip in ips):
        logger.warning("SSRF guard blocked internal target: %s", host)
        return True
    return False


def is_metadata_or_linklocal(url: str) -> bool:
    """MINIMAL block: cloud-metadata + link-local only (literal, no DNS).

    For user-driven navigation, where reaching localhost/LAN is legitimate
    local-dev traffic but the metadata endpoint never is.
    """
    host = _host_of(url)
    if not host:
        return False
    if host in _METADATA_HOSTS:
        logger.warning("SSRF guard blocked metadata host: %s", host)
        return True
    ip = _as_ip(host)
    if ip is not None and ip.is_link_local:
        logger.warning("SSRF guard blocked link-local target: %s", host)
        return True
    return False
