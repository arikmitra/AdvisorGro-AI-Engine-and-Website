"""DNS and TLS probes for Group 01. Stdlib only."""
from __future__ import annotations

import socket
import ssl
import time
from dataclasses import dataclass, field
from typing import Optional

from ..web import TRANSIENT_DNS_ERRNOS


@dataclass
class DnsResult:
    ok: bool
    addresses: list[str]
    error: str = ""
    transient: bool = False
    records: dict = field(default_factory=dict)    # evidence only; never decides pass/fail


@dataclass
class TlsResult:
    ok: bool
    protocol: str = ""
    issuer: str = ""
    subject: str = ""
    not_after: str = ""
    days_left: Optional[int] = None
    error: str = ""
    transient: bool = False


def lookup_records(host: str, lifetime: float = 3.0) -> dict:
    """DNS records via dnspython, best effort. Absent record types are normal."""
    try:
        import dns.resolver
    except ImportError:
        return {"unavailable": "dnspython not installed"}
    r = dns.resolver.Resolver()
    r.lifetime = lifetime
    out: dict = {}
    for rtype in ("A", "AAAA", "CNAME", "MX", "NS"):
        try:
            out[rtype] = sorted(str(x).rstrip(".") for x in r.resolve(host, rtype))
        except Exception:                                  # noqa: BLE001
            pass
    return out or {"note": "the configured DNS resolver returned no records"}


CONTROL_HOST = "example.com"       # a name that must resolve if DNS works at all


def resolve(host: str, timeout: float = 5.0, control_host: str = CONTROL_HOST) -> DnsResult:
    """Pass/fail uses the system resolver, the same path a browser takes. dnspython
    only adds record evidence, so a resolver quirk can never fake a missing domain.

    "Name not found" is only believed if a control lookup succeeds. If the control
    also fails, DNS is broken on this machine and the result is transient, so the
    group reports unverified instead of blaming the firm's domain."""
    # getaddrinfo has no timeout of its own; the orchestrator's clock is the backstop
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        msg, transient = e.strerror or str(e), e.errno in TRANSIENT_DNS_ERRNOS
        if not transient:
            controls = [c for c in (control_host, "iana.org") if c and c != host]
            for c in controls:
                try:
                    socket.getaddrinfo(c, 443, proto=socket.IPPROTO_TCP)
                    break                                   # DNS works, so the name is truly absent
                except OSError:
                    continue
            else:
                msg, transient = f"{msg} (DNS looks unavailable from this machine)", True
        return DnsResult(False, [], msg, transient)
    return DnsResult(True, sorted({i[4][0] for i in infos}),
                     records=lookup_records(host, min(timeout, 3.0)))


def _rdn(seq, key: str) -> str:
    for rdn in seq or ():
        for k, v in rdn:
            if k == key:
                return v
    return ""


def tls_probe(host: str, timeout: float = 5.0, port: int = 443,
              context: Optional[ssl.SSLContext] = None) -> TlsResult:
    ctx = context or ssl.create_default_context()      # verifies chain, expiry, hostname
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as s:
                cert, proto = s.getpeercert(), s.version() or ""
    except ssl.SSLCertVerificationError as e:
        return TlsResult(False, error=f"certificate verification failed: {e.verify_message or e}")
    except ssl.SSLError as e:
        return TlsResult(False, error=f"TLS error: {e}")
    except ConnectionRefusedError:
        return TlsResult(False, error="nothing is listening on port 443")
    except (socket.timeout, TimeoutError):
        return TlsResult(False, error="timed out", transient=True)
    except OSError as e:
        return TlsResult(False, error=f"connection failed: {e}", transient=True)
    not_after = cert.get("notAfter", "")
    days = int((ssl.cert_time_to_seconds(not_after) - time.time()) // 86400) if not_after else None
    return TlsResult(True, proto,
                     _rdn(cert.get("issuer"), "organizationName") or _rdn(cert.get("issuer"), "commonName"),
                     _rdn(cert.get("subject"), "commonName"), not_after, days)
