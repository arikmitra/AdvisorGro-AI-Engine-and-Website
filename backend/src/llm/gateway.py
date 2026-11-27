"""LLM gateway: primary model for drafting, fallback providers, timeouts, retries, schema validation with one
repair retry, and metering. Spend is metered per run/group/campaign, never capped (guide 3.3, 9.7).

Provider payloads follow each vendor's public HTTP API; verify against current vendor docs before go-live.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Protocol, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from ..core.errors import ProviderError, ProviderUnavailable

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)


@dataclass
class LLMResult:
    text: str
    provider: str
    model: str
    tokens_in: int = 0
    tokens_out: int = 0
    cost_micro_usd: int = 0


@dataclass
class Meter:
    """Collects usage for one run or campaign step. Persisted as usage_events by the caller."""
    events: list[dict] = field(default_factory=list)

    def add(self, *, provider: str, kind: str, units: float, cost_micro_usd: int = 0, **tags) -> None:
        self.events.append({"provider": provider, "kind": kind, "units": units, "cost_micro_usd": cost_micro_usd, **tags})


class Provider(Protocol):
    name: str

    async def complete(self, system: str, prompt: str, *, model: str | None, max_tokens: int, temperature: float,
                       json_mode: bool, timeout: float) -> LLMResult: ...


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.M)


def extract_json(text: str) -> object:
    t = _FENCE.sub("", text.strip())
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        a, b = t.find("{"), t.rfind("}")
        if a >= 0 and b > a:
            return json.loads(t[a:b + 1])
        raise


class LLMGateway:
    def __init__(self, providers: list[Provider], *, timeout_s: float = 45.0, retries: int = 1,
                 on_usage: Callable[[dict], None] | None = None):
        self.providers, self.timeout, self.retries, self.on_usage = providers, timeout_s, retries, on_usage
        self.disabled: set[str] = set()  # kill switch: disable a provider

    async def complete(self, system: str, prompt: str, *, model: str | None = None, max_tokens: int = 2000,
                       temperature: float = 0.2, json_mode: bool = False, meter: Meter | None = None,
                       tags: dict | None = None, deadline_s: float | None = None) -> LLMResult:
        last: Exception | None = None
        for p in self.providers:
            if p.name in self.disabled:
                continue
            for attempt in range(self.retries + 1):
                try:
                    res = await asyncio.wait_for(
                        p.complete(system, prompt, model=model if p is self.providers[0] else None,
                                   max_tokens=max_tokens, temperature=temperature, json_mode=json_mode,
                                   timeout=min(self.timeout, deadline_s or self.timeout)),
                        timeout=min(self.timeout, deadline_s or self.timeout) + 1)
                    ev = {"provider": res.provider, "kind": "llm", "units": res.tokens_in + res.tokens_out,
                          "cost_micro_usd": res.cost_micro_usd, **(tags or {})}
                    if meter:
                        meter.events.append(ev)
                    if self.on_usage:
                        self.on_usage(ev)
                    return res
                except (ProviderUnavailable, TimeoutError, httpx.TransportError) as e:
                    last = e
                    await asyncio.sleep(min(0.2 * 2**attempt, 2))
                except ProviderError as e:
                    last = e
                    break  # non-retryable on this provider; try the next one
        raise ProviderUnavailable(f"all LLM providers failed: {last}")

    async def complete_json(self, system: str, prompt: str, schema: type[T], **kw) -> T:
        """Schema-validated JSON with exactly one repair retry (guide 3.4, 9.4)."""
        res = await self.complete(system, prompt, json_mode=True, **kw)
        try:
            return schema.model_validate(extract_json(res.text))
        except (ValidationError, json.JSONDecodeError, ValueError) as e:
            repair = (f"{prompt}\n\nYour previous reply was invalid: {str(e)[:400]}\nReturn ONLY valid JSON matching "
                      f"the schema. Previous reply:\n{res.text[:2000]}")
            res2 = await self.complete(system, repair, json_mode=True, **kw)
            try:
                return schema.model_validate(extract_json(res2.text))
            except (ValidationError, json.JSONDecodeError, ValueError) as e2:
                raise ProviderError(f"invalid structured output after one repair retry: {str(e2)[:200]}", code="invalid_output") from e2


Callback = Callable[..., Awaitable]
