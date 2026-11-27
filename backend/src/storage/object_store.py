"""Object storage: reports, creatives, evidence. One prefix per tenant, versioned keys, content-addressed,
no public buckets, downloads by short-lived signed links. S3 with Object Lock in prod (approvals, activity log)."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol

from ..core.hashing import sha256_hex


class ObjectStore(Protocol):
    def put(self, tenant_id: str, name: str, data: bytes, *, content_type: str = "application/octet-stream",
            immutable: bool = False) -> str: ...
    def get(self, uri: str) -> bytes: ...
    def exists(self, uri: str) -> bool: ...


class LocalObjectStore:
    """Filesystem implementation for dev/test. URI form: obj://<tenant>/<sha256>/<name>."""

    def __init__(self, root: str):
        self.root = Path(root)

    def _path(self, uri: str) -> Path:
        assert uri.startswith("obj://")
        p = (self.root / uri[6:]).resolve()
        if not str(p).startswith(str(self.root.resolve())):
            raise ValueError("path traversal")
        return p

    def put(self, tenant_id, name, data, *, content_type="application/octet-stream", immutable=False) -> str:
        safe = os.path.basename(name)
        uri = f"obj://{tenant_id}/{sha256_hex(data)[:16]}/{safe}"
        p = self._path(uri)
        if p.exists():
            if immutable and p.read_bytes() != data:
                raise FileExistsError("immutable object differs")
            return uri
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        if immutable:
            p.chmod(0o444)
        return uri

    def get(self, uri: str) -> bytes:
        return self._path(uri).read_bytes()

    def exists(self, uri: str) -> bool:
        return self._path(uri).exists()


class S3ObjectStore:
    """boto3 is imported lazily (optional extra `aws`). Bucket must have versioning and Object Lock on."""

    def __init__(self, bucket: str):
        import boto3
        self.bucket, self.c = bucket, boto3.client("s3")

    def put(self, tenant_id, name, data, *, content_type="application/octet-stream", immutable=False) -> str:
        key = f"{tenant_id}/{sha256_hex(data)[:16]}/{os.path.basename(name)}"
        extra = {"ObjectLockMode": "COMPLIANCE", "ObjectLockRetainUntilDate": _five_years()} if immutable else {}
        self.c.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type, **extra)
        return f"obj://{key}"

    def get(self, uri: str) -> bytes:
        return self.c.get_object(Bucket=self.bucket, Key=uri[6:])["Body"].read()

    def exists(self, uri: str) -> bool:
        try:
            self.c.head_object(Bucket=self.bucket, Key=uri[6:])
            return True
        except Exception:  # noqa: BLE001
            return False


def _five_years():
    from datetime import timedelta

    from ..core.clock import utcnow
    return utcnow() + timedelta(days=365 * 5)  # confirm the books-and-records period with counsel
