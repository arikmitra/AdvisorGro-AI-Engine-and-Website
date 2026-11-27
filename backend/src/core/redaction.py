"""Redaction before anything is logged, traced or sent to an LLM trace store.
Regex baseline here; Presidio plugs in through `set_engine` where installed."""
from __future__ import annotations

import re
from collections.abc import Callable

_PATTERNS = [
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "[email]"),
    (re.compile(r"(?<!\d)(?:\+?1[ .-]?)?\(?\d{3}\)?[ .-]?\d{3}[ .-]?\d{4}(?!\d)"), "[phone]"),
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[ssn]"),
    (re.compile(r"\b(?:\d[ -]?){13,16}\b"), "[card]"),
    (re.compile(r"(?i)\b(bearer|token|api[_-]?key|secret)\b(\s*[:=]\s*)\S+"), r"\1\2[secret]"),
]
_engine: Callable[[str], str] | None = None


def set_engine(fn: Callable[[str], str] | None) -> None:
    global _engine
    _engine = fn


def redact(text: str) -> str:
    for rx, rep in _PATTERNS:
        text = rx.sub(rep, text)
    return _engine(text) if _engine else text


def redact_obj(o):
    if isinstance(o, str):
        return redact(o)
    if isinstance(o, dict):
        return {k: ("[secret]" if re.search(r"(?i)secret|token|password|api_?key", str(k)) else redact_obj(v))
                for k, v in o.items()}
    if isinstance(o, list | tuple):
        return [redact_obj(v) for v in o]
    return o
