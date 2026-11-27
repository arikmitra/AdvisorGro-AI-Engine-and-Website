"""Data contracts (deck 9.4). Platform limits are enforced in code: a model that overruns triggers the gateway's
one repair retry, never silent truncation."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

GOOGLE_HEADLINE_MAX, GOOGLE_DESC_MAX = 30, 90
META_PRIMARY_MAX, META_HEADLINE_MAX = 125, 40
SMS_MAX = 160


class CampaignBrief(BaseModel):
    offer_id: str
    firm_id: str
    area: str
    angles: list[str] = Field(min_length=1, max_length=4)
    keyword_themes: list[str] = Field(default_factory=list, max_length=8)
    audiences: list[str] = Field(default_factory=list)
    monthly_budget_cents: int = Field(gt=0)
    budget_ceiling_cents: int = Field(gt=0)

    @model_validator(mode="after")
    def _cap(self):
        if self.monthly_budget_cents > self.budget_ceiling_cents:
            raise ValueError("monthly budget exceeds the client's ceiling")
        return self


class GoogleAdSet(BaseModel):
    headlines: list[str] = Field(min_length=3, max_length=15)
    descriptions: list[str] = Field(min_length=2, max_length=4)

    @field_validator("headlines")
    @classmethod
    def _h(cls, v):
        bad = [h for h in v if len(h) > GOOGLE_HEADLINE_MAX]
        if bad:
            raise ValueError(f"headline over {GOOGLE_HEADLINE_MAX} characters: {bad[0]!r}")
        return v

    @field_validator("descriptions")
    @classmethod
    def _d(cls, v):
        bad = [d for d in v if len(d) > GOOGLE_DESC_MAX]
        if bad:
            raise ValueError(f"description over {GOOGLE_DESC_MAX} characters")
        return v


class MetaVariant(BaseModel):
    primary_text: str = Field(max_length=META_PRIMARY_MAX)
    headline: str = Field(max_length=META_HEADLINE_MAX)
    cta: str = "Learn more"


class MetaAdSet(BaseModel):
    variants: list[MetaVariant] = Field(min_length=1, max_length=3)


class PageSection(BaseModel):
    type: Literal["hero", "benefits", "process", "faq", "cta", "about"] | str
    heading: str
    body_blocks: list[str] = Field(default_factory=list)
    cta: str = ""


class PageCopy(BaseModel):
    sections: list[PageSection] = Field(min_length=1)


class EmailItem(BaseModel):
    subject: str = Field(max_length=120)
    body: str


class FollowUpSet(BaseModel):
    emails: list[EmailItem] = Field(min_length=1, max_length=5)
    sms: list[str] = Field(min_length=1, max_length=2)

    @field_validator("sms")
    @classmethod
    def _s(cls, v):
        for m in v:
            if len(m) > SMS_MAX:
                raise ValueError(f"SMS over {SMS_MAX} characters")
            if "stop" not in m.lower():
                raise ValueError("SMS must include opt-out wording (Reply STOP)")
        return v


class CreativeSet(BaseModel):
    google: GoogleAdSet | None = None
    meta: MetaAdSet | None = None
    page: PageCopy
    followup: FollowUpSet


class Claim(BaseModel):
    text: str
    kind: str = "fact"


class ClaimList(BaseModel):
    claims: list[Claim] = Field(default_factory=list)


class AngleOut(BaseModel):
    angles: list[str] = Field(min_length=1, max_length=4)
    keyword_themes: list[str] = Field(default_factory=list, max_length=8)
