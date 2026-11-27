"""Public-input guard: which domains the public form and Shield snapshot may point the engine at.

check_public_site(domain) returns "" when the domain is fine, otherwise a message for the visitor.
Blocks IP literals, single-label and internal names, and anything resolving to a private, loopback,
link-local or reserved address, so the form cannot be used to make the server probe its own network.
"""
from __future__ import annotations

import ipaddress
import re
import socket

NOT_FOUND = "We could not find that website. Please check the address, for example yourfirm.com."
NOT_PUBLIC = "Please enter your firm\u2019s public website address, for example yourfirm.com."
_LABEL = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
_INTERNAL_SUFFIXES = (".local", ".localhost", ".internal", ".lan", ".home", ".corp", ".intranet", ".test", ".invalid")


def _public_ip(addr: str) -> bool:
    try:
        ip = ipaddress.ip_address(addr.split("%")[0])
    except ValueError:
        return False
    return ip.is_global


def check_public_site(domain: str, resolve=socket.getaddrinfo) -> str:
    d = (domain or "").strip().lower().rstrip(".")
    if not d or len(d) > 253 or "." not in d:
        return NOT_PUBLIC
    try:
        ipaddress.ip_address(d)
        return NOT_PUBLIC                      # an IP address, not a name
    except ValueError:
        pass
    if d.endswith(_INTERNAL_SUFFIXES) or not all(_LABEL.match(p) for p in d.split(".")):
        return NOT_PUBLIC
    try:
        infos = resolve(d, 443, type=socket.SOCK_STREAM)
    except OSError:
        return NOT_FOUND
    addrs = {i[4][0] for i in infos}
    if not addrs:
        return NOT_FOUND
    if not all(_public_ip(a) for a in addrs):
        return NOT_PUBLIC
    return ""