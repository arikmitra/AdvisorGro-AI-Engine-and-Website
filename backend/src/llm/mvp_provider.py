"""Adapts the MVP's multi-provider clients (Anthropic, OpenAI, Gemini; pricing, retries) to the gateway."""
from __future__ import annotations

import asyncio

from ..core.errors import ProviderError, ProviderUnavailable
from ..engine.shield.mvp.llm import LLMError, make_llm_client
from .gateway import LLMGateway, LLMResult


class MvpClientProvider:
    def __init__(self, name: str, client):
        self.name, self.client = name, client

    async def complete(self, system, prompt, *, model, max_tokens, temperature, json_mode, timeout) -> LLMResult:
        try:
            r = await asyncio.to_thread(self.client.complete, prompt, system=system, max_tokens=max_tokens, timeout=timeout)
        except LLMError as e:
            raise (ProviderUnavailable if e.transient else ProviderError)(str(e)) from e
        return LLMResult(r.text, self.name, getattr(self.client, "model", ""), 0, 0, int(r.cost_usd * 1e6))


def build_gateway(settings, cfg, *, on_usage=None) -> LLMGateway:
    """Primary provider first (drafting), the others as fallbacks. `cfg` is the MVP Config."""
    order = []
    primary = cfg.primary_llm()
    for p in ([primary] if primary else []) + [x for x in cfg.llm_keys() if x != primary]:
        if cfg.key_for(p):
            order.append(MvpClientProvider(p, make_llm_client(p, cfg.key_for(p), cfg.model_for(p))))
    return LLMGateway(order, timeout_s=settings.llm_timeout_s, on_usage=on_usage)
