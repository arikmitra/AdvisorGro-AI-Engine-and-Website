"""Envelope encryption for Restricted fields and keyed hashes for dedupe.

Per-tenant data keys are derived from a key-encryption key (HKDF). In prod the KEK lives in the cloud key
service and `KeyProvider.kek()` calls it; the rest is unchanged. Keyed HMAC lets us find duplicates without
storing plain text in indexes.
"""
from __future__ import annotations

import base64
import os
from typing import Protocol

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from ..core.hashing import hmac_hex


class KeyProvider(Protocol):
    def kek(self) -> bytes: ...
    def hmac_key(self) -> bytes: ...


class LocalKeyProvider:
    def __init__(self, kek_b64: str, hmac_b64: str):
        self._kek, self._hmac = base64.b64decode(kek_b64), base64.b64decode(hmac_b64)

    def kek(self) -> bytes:
        return self._kek

    def hmac_key(self) -> bytes:
        return self._hmac


class Crypto:
    def __init__(self, keys: KeyProvider):
        self.keys = keys

    def _dek(self, tenant_id: str) -> bytes:
        return HKDF(algorithm=hashes.SHA256(), length=32, salt=None,
                    info=f"advisorgro/tenant/{tenant_id}".encode()).derive(self.keys.kek())

    def encrypt(self, tenant_id: str, plaintext: str) -> str:
        if plaintext == "":
            return ""
        nonce = os.urandom(12)
        ct = AESGCM(self._dek(tenant_id)).encrypt(nonce, plaintext.encode(), tenant_id.encode())
        return base64.urlsafe_b64encode(b"\x01" + nonce + ct).decode()

    def decrypt(self, tenant_id: str, blob: str) -> str:
        if not blob:
            return ""
        raw = base64.urlsafe_b64decode(blob.encode())
        return AESGCM(self._dek(tenant_id)).decrypt(raw[1:13], raw[13:], tenant_id.encode()).decode()

    def keyed_hash(self, value: str) -> str:
        return hmac_hex(self.keys.hmac_key(), value.strip().lower())


def normalise_email(e: str) -> str:
    return e.strip().lower()


def normalise_phone(p: str) -> str:
    d = "".join(c for c in p if c.isdigit())
    return d[-10:] if len(d) >= 10 else d


def mask_email(e: str) -> str:
    if "@" not in e:
        return "***"
    u, d = e.split("@", 1)
    return f"{u[:1]}***@{d}"


def mask_phone(p: str) -> str:
    return "***-***-" + p[-4:] if len(p) >= 4 else "***"
