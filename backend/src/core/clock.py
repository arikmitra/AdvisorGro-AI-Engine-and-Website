from __future__ import annotations

from datetime import UTC, datetime


def utcnow() -> datetime:
    """Timezone-aware UTC. The DB layer stores naive UTC and re-attaches tzinfo on read."""
    return datetime.now(UTC)
