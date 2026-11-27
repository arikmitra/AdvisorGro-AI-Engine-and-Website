"""Shared kernel: the thin platform every check group plugs into."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Protocol


class Budget:
    """Shared money cap and wall-clock deadline for one run.

    `slice()` hands a group its own share while still charging the parent,
    so one greedy group cannot starve the others.
    """

    def __init__(self, usd_cap: float = 0.10, seconds_cap: float = 120.0,
                 clock: Callable[[], float] = time.monotonic,
                 parent: Optional["Budget"] = None):
        self.usd_cap, self.seconds_cap = usd_cap, seconds_cap
        self._clock, self._start = clock, clock()
        self.parent, self.spent_usd = parent, 0.0
        self._lock = threading.Lock()

    @property
    def remaining_usd(self) -> float:
        own = self.usd_cap - self.spent_usd
        return min(own, self.parent.remaining_usd) if self.parent else own

    @property
    def remaining_seconds(self) -> float:
        own = self.seconds_cap - (self._clock() - self._start)
        return min(own, self.parent.remaining_seconds) if self.parent else own

    def can_afford(self, usd: float) -> bool:
        return usd <= self.remaining_usd + 1e-12 and self.remaining_seconds > 0

    @property
    def exhausted(self) -> bool:
        return self.remaining_usd <= 1e-12 or self.remaining_seconds <= 0

    def charge(self, usd: float) -> None:
        with self._lock:
            self.spent_usd += usd
        if self.parent:
            self.parent.charge(usd)

    def slice(self, usd_share: float) -> "Budget":
        return Budget(self.usd_cap * usd_share, self.seconds_cap, self._clock, parent=self)


@dataclass
class RunContext:
    url: str
    firm_name: str
    area: str                      # plain input for now; see open issue 1
    budget: Budget = field(default_factory=Budget)
    domain: str = ""               # derived from url when empty
    aliases: tuple[str, ...] = ()  # alternative firm-name spellings

    def __post_init__(self):
        if not self.domain:
            host = self.url.split("://")[-1].split("/")[0].lower()
            self.domain = host[4:] if host.startswith("www.") else host


@dataclass
class Finding:
    check: str
    passed: bool
    points: float
    max_points: float
    detail: str = ""
    status: str = "ok"             # ok | unverified | partial


@dataclass
class GroupResult:
    group_id: str
    score: float
    max_points: float
    findings: list[Finding] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)
    partial: bool = False


class GroupModule(Protocol):
    group_id: str
    max_points: float

    def run(self, ctx: RunContext) -> GroupResult: ...


ACTIVE_MODULES: list[GroupModule] = []


def register(module: GroupModule) -> GroupModule:
    ACTIVE_MODULES.append(module)
    return module


class EvidenceStore:
    """In-memory stand-in for run storage (swap for SQLite later)."""

    def __init__(self):
        self._rows: list[tuple[str, str, Any]] = []

    def put(self, group_id: str, kind: str, payload: Any) -> None:
        self._rows.append((group_id, kind, payload))

    def get(self, group_id: str) -> list[tuple[str, Any]]:
        return [(k, p) for g, k, p in self._rows if g == group_id]
