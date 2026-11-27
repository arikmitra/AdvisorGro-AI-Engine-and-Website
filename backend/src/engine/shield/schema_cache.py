from __future__ import annotations

import time
from typing import Any


class Cache:
    """1-hour answer/snapshot cache (the query_cache table in prod)."""

    def __init__(self) -> None:
        self.d: dict[str, tuple[float, Any]] = {}

    def get(self, k: str):
        v = self.d.get(k)
        return v[1] if v and v[0] > time.time() else None

    def set(self, k: str, v: Any, ttl: int = 3600) -> None:
        self.d[k] = (time.time() + ttl, v)
