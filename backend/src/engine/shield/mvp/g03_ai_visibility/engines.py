"""Answer-engine interface plus a deterministic stub. Real adapters go here later."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class Answer:
    engine: str
    prompt: str
    repeat: int
    text: str
    cited_urls: tuple[str, ...] = ()
    cost_usd: float = 0.0


class AnswerEngine(Protocol):
    name: str
    cost_per_call: float            # estimate used for planning

    def ask(self, prompt: str, repeat: int) -> Answer: ...


@dataclass
class StubEngine:
    """Names the target firm on a deterministic fraction of calls."""
    name: str
    target_firm: str
    target_domain: str
    hit_rate: float = 0.4
    cost_per_call: float = 0.001
    others: tuple[str, ...] = ("Alpha Advisors", "Birch Capital", "Cedar Wealth")

    def ask(self, prompt: str, repeat: int) -> Answer:
        h = int(hashlib.sha256(f"{self.name}|{prompt}|{repeat}".encode()).hexdigest(), 16)
        hit = (h % 1000) / 1000 < self.hit_rate
        names = list(self.others[: 1 + h % 3])
        cited: tuple[str, ...] = ()
        if hit:
            names.insert(0, self.target_firm)
            cited = (f"https://{self.target_domain}/about",)
        return Answer(self.name, prompt, repeat, "Top picks: " + ", ".join(names),
                      cited, self.cost_per_call)


# --- real engines -------------------------------------------------------------------------
SYSTEM = ("You are a helpful assistant answering a consumer's question about financial advisors. "
          "Answer directly and concisely, and name specific firms where you can.")


@dataclass
class ClaudeEngine:
    """Claude as an answer engine. search=False measures what the model itself knows
    (parametric); search=True lets it retrieve live sources, which costs about a cent a search."""
    client: "object"                      # llm.AnthropicClient
    name: str = "claude"
    search: bool = False
    max_tokens: int = 350
    est_input_tokens: int = 150

    @property
    def cost_per_call(self) -> float:     # worst-case estimate, used for planning
        return self.client.estimate_cost(self.est_input_tokens, self.max_tokens, self.search)

    def ask(self, prompt: str, repeat: int) -> Answer:
        r = self.client.complete(prompt, system=SYSTEM, max_tokens=self.max_tokens,
                                 search=self.search, max_searches=1, timeout=45.0)
        return Answer(self.name, prompt, repeat, r.text, tuple(r.urls), r.cost_usd)


@dataclass
class OpenAICompatEngine:
    client: "object"                      # llm.OpenAICompatClient
    name: str
    max_tokens: int = 350
    est_input_tokens: int = 150

    @property
    def cost_per_call(self) -> float:
        c = self.client
        return (self.est_input_tokens * c.price_in + self.max_tokens * c.price_out) / 1e6 + c.per_request

    def ask(self, prompt: str, repeat: int) -> Answer:
        r = self.client.complete(prompt, system=SYSTEM, max_tokens=self.max_tokens, timeout=45.0)
        return Answer(self.name, prompt, repeat, r.text, tuple(r.urls), r.cost_usd)
