"""Outbound-address guard (the MVP's guard.py). Workers also run in an egress-allow-listed subnet."""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

from .errors import ValidationFailed

_BLOCKED_HOSTS = {"localhost", "metadata.google.internal", "metadata", "instance-data"}
_METADATA_IPS = {ipaddress.ip_address("169.254.169.254"), ipaddress.ip_address("fd00:ec2::254")}


def is_public_ip(ip: str) -> bool:
    a = ipaddress.ip_address(ip)
    if a in _METADATA_IPS:
        return False
    return not (a.is_private or a.is_loopback or a.is_link_local or a.is_reserved
                or a.is_multicast or a.is_unspecified)


def normalise_domain(raw: str) -> str:
    """'https://www.Firm.com/path' -> 'firm.com'. Raises ValidationFailed for anything that is not a domain."""
    s = raw.strip().lower()
    if "://" not in s:
        s = "https://" + s
    host = (urlparse(s).hostname or "").strip(".")
    if host.startswith("www."):
        host = host[4:]
    if not host or "." not in host or len(host) > 253 or any(c in host for c in " _/\\@"):
        raise ValidationFailed("invalid domain")
    try:
        ipaddress.ip_address(host)
        raise ValidationFailed("IP addresses are not accepted")
    except ValueError:
        pass
    return host


def assert_public_url(url: str, *, resolve: bool = True) -> str:
    u = urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise ValidationFailed("only http(s) URLs are allowed")
    host = u.hostname.lower()
    if host in _BLOCKED_HOSTS or host.endswith((".internal", ".local", ".localhost")):
        raise ValidationFailed("blocked host")
    try:
        ips = [host] if _is_ip(host) else ([ai[4][0] for ai in socket.getaddrinfo(host, None)] if resolve else [])
    except socket.gaierror as e:
        raise ValidationFailed("host does not resolve") from e
    for ip in ips:
        if not is_public_ip(ip):
            raise ValidationFailed("address is not public")
    return url


def _is_ip(s: str) -> bool:
    try:
        ipaddress.ip_address(s)
        return True
    except ValueError:
        return False
