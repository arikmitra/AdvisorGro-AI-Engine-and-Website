from __future__ import annotations

import json
import logging
import sys

from .clock import utcnow
from .ids import correlation_id_var
from .redaction import redact


class JsonRedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        base = {
            "ts": utcnow().isoformat(), "level": record.levelname, "logger": record.name,
            "msg": redact(record.getMessage()), "correlation_id": correlation_id_var.get(),
        }
        for k in ("tenant_id", "firm_id", "run_id", "campaign_id", "event"):
            if hasattr(record, k):
                base[k] = getattr(record, k)
        if record.exc_info:
            base["exc"] = redact(self.formatException(record.exc_info))
        return json.dumps(base)


def configure_logging(level: str = "INFO") -> None:
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(JsonRedactingFormatter())
    root = logging.getLogger()
    root.handlers[:] = [h]
    root.setLevel(level)
