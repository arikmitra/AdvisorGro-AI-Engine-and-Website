"""Per-firm knowledge store: facts, approved language and past CCO decisions. Retrieval is per firm only.
pgvector in Postgres; here a deterministic hashed embedding so dev and CI need no embedding API."""
from __future__ import annotations

import hashlib
import math
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...db.models import KnowledgeFact

DIM = 64


def embed(text: str) -> list[float]:
    v = [0.0] * DIM
    for tok in re.findall(r"[a-z0-9]+", text.lower()):
        h = int(hashlib.md5(tok.encode(), usedforsecurity=False).hexdigest(), 16)  # noqa: S324
        v[h % DIM] += 1.0 if (h >> 8) & 1 else -1.0
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def add(s: Session, firm_id: str, kind: str, text: str, key: str = "") -> KnowledgeFact:
    f = KnowledgeFact(firm_id=firm_id, kind=kind, key=key, text=text, embedding=embed(text))
    s.add(f)
    s.flush()
    return f


def facts(s: Session, firm_id: str, kinds: tuple[str, ...] = ("fact",)) -> list[KnowledgeFact]:
    return list(s.execute(select(KnowledgeFact).where(KnowledgeFact.firm_id == firm_id, KnowledgeFact.kind.in_(kinds))).scalars())


def search(s: Session, firm_id: str, query: str, k: int = 5, kinds=("fact", "approved_language", "cco_decision")) -> list[KnowledgeFact]:
    q = embed(query)
    rows = facts(s, firm_id, kinds)
    return sorted(rows, key=lambda r: -sum(a * b for a, b in zip(q, r.embedding or [0.0] * DIM, strict=False)))[:k]
