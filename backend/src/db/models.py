"""Tables from guide section 3.8 / 15.8 / 19.5. D0..D4 are logical stores over these tables."""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..core.clock import utcnow
from .base import Base, InsertOnly, TenantMixin, pk


def now_col(**kw):
    return mapped_column(default=utcnow, **kw)


# ---------------------------------------------------------------- D0 firms, users, roles
class Tenant(Base):
    __tablename__ = "tenants"
    id: Mapped[str] = pk()
    name: Mapped[str] = mapped_column(String(200))
    plan: Mapped[str] = mapped_column(String(30), default="audit")  # audit | shield | gro
    isolation: Mapped[str] = mapped_column(String(20), default="pooled")  # pooled | schema | database
    status: Mapped[str] = mapped_column(String(20), default="active")
    created_at: Mapped[datetime] = now_col()


class Network(Base):
    __tablename__ = "networks"
    id: Mapped[str] = pk()
    name: Mapped[str] = mapped_column(String(200))


class Firm(TenantMixin, Base):
    __tablename__ = "firms"
    id: Mapped[str] = pk()
    name: Mapped[str] = mapped_column(String(200))
    domain: Mapped[str] = mapped_column(String(253))
    area: Mapped[str] = mapped_column(String(200), default="")
    aum_band: Mapped[str] = mapped_column(String(40), default="")
    profile: Mapped[dict] = mapped_column(JSON, default=dict)
    network_id: Mapped[str | None] = mapped_column(ForeignKey("networks.id"), nullable=True)
    created_at: Mapped[datetime] = now_col()
    __table_args__ = (UniqueConstraint("tenant_id", "domain"),)


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = pk()
    email: Mapped[str] = mapped_column(String(320), unique=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    idp_subject: Mapped[str | None] = mapped_column(String(200), unique=True, nullable=True)
    is_staff: Mapped[bool] = mapped_column(Boolean, default=False)
    staff_role: Mapped[str | None] = mapped_column(String(30), nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(nullable=True)
    disabled: Mapped[bool] = mapped_column(Boolean, default=False)


class Membership(TenantMixin, Base):
    __tablename__ = "memberships"
    id: Mapped[str] = pk()
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    firm_id: Mapped[str | None] = mapped_column(ForeignKey("firms.id"), nullable=True)
    role: Mapped[str] = mapped_column(String(20))  # firm_admin|cco|marketing|advisor|read_only
    __table_args__ = (UniqueConstraint("tenant_id", "user_id", "role"),)


class Delegation(TenantMixin, Base):
    __tablename__ = "delegations"
    id: Mapped[str] = pk()
    approver_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    deputy_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    starts_at: Mapped[datetime]
    ends_at: Mapped[datetime]


class ApiKey(TenantMixin, Base):
    __tablename__ = "api_keys"
    id: Mapped[str] = pk()
    scope: Mapped[str] = mapped_column(String(100))
    key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime | None] = mapped_column(nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(nullable=True)


class BreakGlassGrant(Base):
    __tablename__ = "break_glass_grants"
    id: Mapped[str] = pk()
    engineer_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    approver_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    tenant_id: Mapped[str] = mapped_column(String(36))
    ticket: Mapped[str] = mapped_column(String(100))
    expires_at: Mapped[datetime]
    created_at: Mapped[datetime] = now_col()


# ---------------------------------------------------------------- D1 runs, evidence (Shield)
class AuditRequest(TenantMixin, Base):
    __tablename__ = "audit_requests"
    id: Mapped[str] = pk()
    domain: Mapped[str] = mapped_column(String(253))
    firm_name: Mapped[str] = mapped_column(String(200), default="")
    area: Mapped[str] = mapped_column(String(200), default="")
    email_enc: Mapped[str] = mapped_column(Text)
    email_hmac: Mapped[str] = mapped_column(String(64), index=True)
    consent_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    contact_name_enc: Mapped[str] = mapped_column(Text, default="")
    crd: Mapped[str] = mapped_column(String(12), default="")
    aum: Mapped[str] = mapped_column(String(60), default="")
    goal: Mapped[str] = mapped_column(String(60), default="")
    run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = now_col()


class Run(TenantMixin, Base):
    __tablename__ = "runs"
    id: Mapped[str] = pk()
    firm_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    domain: Mapped[str] = mapped_column(String(253), index=True)
    firm_name: Mapped[str] = mapped_column(String(200), default="")
    area: Mapped[str] = mapped_column(String(200), default="")
    trigger: Mapped[str] = mapped_column(String(20))  # free_audit | shield | portfolio
    priority: Mapped[int] = mapped_column(Integer, default=5)
    # Queued, Running, Partial, Scored, InReview, Released, Delivered, Failed
    state: Mapped[str] = mapped_column(String(20), default="Queued", index=True)
    period: Mapped[str] = mapped_column(String(20), default="")
    idempotency_key: Mapped[str] = mapped_column(String(200), unique=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)  # MVP scorecard total (a lower bound if partial)
    score_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    partial: Mapped[bool] = mapped_column(Boolean, default=False)
    scorecard: Mapped[dict] = mapped_column(JSON, default=dict)
    release_status: Mapped[str] = mapped_column(String(30), default="")  # releasable | pending_cco_review | compliance_unverified
    held_flags: Mapped[list] = mapped_column(JSON, default=list)  # compliance items, never shown to the prospect
    report_token_hash: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    token_expires_at: Mapped[datetime | None] = mapped_column(nullable=True)
    token_revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    notify_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    spend: Mapped[dict] = mapped_column(JSON, default=dict)
    correlation_id: Mapped[str] = mapped_column(String(32), default="-")
    created_at: Mapped[datetime] = now_col()
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)


class GroupResult(TenantMixin, Base):
    __tablename__ = "group_results"
    id: Mapped[str] = pk()
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    group_key: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(12))  # verified | partial | unverified (never "failed")
    points: Mapped[float] = mapped_column(Float, default=0)
    max_points: Mapped[float] = mapped_column(Float)
    checks: Mapped[list] = mapped_column(JSON, default=list)
    evidence_uri: Mapped[str | None] = mapped_column(String(500), nullable=True)
    unverified_reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    providers: Mapped[list] = mapped_column(JSON, default=list)
    sample_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    __table_args__ = (UniqueConstraint("run_id", "group_key"),)


class Finding(TenantMixin, Base):
    __tablename__ = "findings"
    id: Mapped[str] = pk()
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    group_key: Mapped[str] = mapped_column(String(30))
    code: Mapped[str] = mapped_column(String(60))
    severity: Mapped[str] = mapped_column(String(10))  # high | medium | low (fix priority band)
    title: Mapped[str] = mapped_column(String(300))
    detail: Mapped[str] = mapped_column(Text, default="")
    fix: Mapped[str] = mapped_column(Text, default="")
    impact: Mapped[int] = mapped_column(Integer, default=1)  # 1-5
    effort: Mapped[int] = mapped_column(Integer, default=1)  # 1-5
    is_compliance: Mapped[bool] = mapped_column(Boolean, default=False)
    quoted_text: Mapped[str | None] = mapped_column(Text, nullable=True)


class Report(TenantMixin, Base):
    __tablename__ = "reports"
    id: Mapped[str] = pk()
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    kind: Mapped[str] = mapped_column(String(10))  # client | evidence
    uri: Mapped[str] = mapped_column(String(500))
    content_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = now_col()


class ShieldEnrollment(TenantMixin, Base):
    __tablename__ = "shield_enrollments"
    id: Mapped[str] = pk()
    firm_id: Mapped[str] = mapped_column(ForeignKey("firms.id"), unique=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    next_run_at: Mapped[datetime]
    last_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)


class Alert(TenantMixin, InsertOnly, Base):
    __tablename__ = "alerts"
    id: Mapped[str] = pk()
    firm_id: Mapped[str] = mapped_column(String(36))
    run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    kind: Mapped[str] = mapped_column(String(60))
    route: Mapped[str] = mapped_column(String(10))  # client | internal  (technical -> client, compliance -> reviewer/CCO only)
    week_key: Mapped[str] = mapped_column(String(10))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = now_col()
    __table_args__ = (UniqueConstraint("firm_id", "kind", "week_key"),)


class AlertRule(Base):
    """Thresholds live in a table so they can be tuned without a deploy."""
    __tablename__ = "alert_rules"
    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    threshold: Mapped[float] = mapped_column(Float)
    route: Mapped[str] = mapped_column(String(10), default="client")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class QueryCache(Base):
    __tablename__ = "query_cache"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)
    expires_at: Mapped[datetime] = mapped_column(index=True)


class DirectorySnapshot(TenantMixin, Base):
    __tablename__ = "directory_snapshot"
    id: Mapped[str] = pk()
    firm_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    domain: Mapped[str] = mapped_column(String(253), index=True)
    directory: Mapped[str] = mapped_column(String(60))
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    taken_at: Mapped[datetime] = now_col()


class ReviewItem(TenantMixin, Base):
    """Review queue: compliance flags go to the CCO or reviewer before release."""
    __tablename__ = "review_items"
    id: Mapped[str] = pk()
    kind: Mapped[str] = mapped_column(String(20))  # shield_run | campaign_version
    ref_id: Mapped[str] = mapped_column(String(36), index=True)
    state: Mapped[str] = mapped_column(String(12), default="pending")  # pending | released | rejected
    assigned_role: Mapped[str] = mapped_column(String(20), default="reviewer")  # reviewer | cco
    flags: Mapped[list] = mapped_column(JSON, default=list)
    decided_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = now_col()


# ---------------------------------------------------------------- D2 campaigns, approvals (Gro)
class Campaign(TenantMixin, Base):
    __tablename__ = "campaigns"
    id: Mapped[str] = pk()
    firm_id: Mapped[str] = mapped_column(ForeignKey("firms.id"))
    offer_id: Mapped[str] = mapped_column(String(60))
    platform: Mapped[str] = mapped_column(String(10), default="google")
    state: Mapped[str] = mapped_column(String(20), default="Draft", index=True)
    current_version: Mapped[int] = mapped_column(Integer, default=0)
    live_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    monthly_budget_cents: Mapped[int] = mapped_column(BigInteger)  # client-set
    budget_ceiling_cents: Mapped[int] = mapped_column(BigInteger)  # client-set cap the optimiser cannot raise
    pause_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_by: Mapped[str] = mapped_column(String(36))
    created_at: Mapped[datetime] = now_col()
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class CampaignVersion(TenantMixin, InsertOnly, Base):
    __tablename__ = "campaign_versions"
    id: Mapped[str] = pk()
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    brief: Mapped[dict] = mapped_column(JSON)
    creative_set: Mapped[dict] = mapped_column(JSON)
    parent_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    change_note: Mapped[str] = mapped_column(String(300), default="")
    created_by: Mapped[str] = mapped_column(String(36))
    created_at: Mapped[datetime] = now_col()
    __table_args__ = (UniqueConstraint("campaign_id", "version"),)


class PreCheckRecord(TenantMixin, InsertOnly, Base):
    __tablename__ = "precheck_reports"
    id: Mapped[str] = pk()
    campaign_id: Mapped[str] = mapped_column(String(36), index=True)
    version: Mapped[int] = mapped_column(Integer)
    report: Mapped[dict] = mapped_column(JSON)
    ai_review_missing: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = now_col()


class RenderedSet(TenantMixin, InsertOnly, Base):
    """Stage 5 output: page, ad previews and emails with one content hash."""
    __tablename__ = "rendered_sets"
    id: Mapped[str] = pk()
    campaign_id: Mapped[str] = mapped_column(String(36), index=True)
    version: Mapped[int] = mapped_column(Integer)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    parts: Mapped[dict] = mapped_column(JSON)  # normalised parts the hash is over
    artefact_uris: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = now_col()
    __table_args__ = (UniqueConstraint("campaign_id", "version"),)


class Approval(TenantMixin, InsertOnly, Base):
    __tablename__ = "approvals"
    id: Mapped[str] = pk()
    campaign_id: Mapped[str] = mapped_column(String(36), index=True)
    version: Mapped[int] = mapped_column(Integer)
    content_hash: Mapped[str] = mapped_column(String(64))
    approver_id: Mapped[str] = mapped_column(String(36))
    decision: Mapped[str] = mapped_column(String(20))  # approve | request_changes | reject
    comment: Mapped[str] = mapped_column(Text, default="")
    decided_at: Mapped[datetime] = now_col()
    expires_at: Mapped[datetime | None] = mapped_column(nullable=True)


class PromptVersion(Base):
    __tablename__ = "prompt_versions"
    id: Mapped[str] = pk()
    name: Mapped[str] = mapped_column(String(80))
    version: Mapped[int] = mapped_column(Integer)
    sha: Mapped[str] = mapped_column(String(64))
    template: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(12), default="draft")  # draft | approved
    signed_off_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    __table_args__ = (UniqueConstraint("name", "version"),)


class KnowledgeFact(TenantMixin, Base):
    """Per-firm knowledge store (pgvector in Postgres; JSON embedding column elsewhere). Retrieval is per firm only."""
    __tablename__ = "knowledge_facts"
    id: Mapped[str] = pk()
    firm_id: Mapped[str] = mapped_column(String(36), index=True)
    kind: Mapped[str] = mapped_column(String(20))  # fact | approved_language | cco_decision
    key: Mapped[str] = mapped_column(String(100), default="")
    text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = now_col()


# ---------------------------------------------------------------- D3 leads, consent
class Consent(TenantMixin, InsertOnly, Base):
    __tablename__ = "consents"
    id: Mapped[str] = pk()
    text_id: Mapped[str] = mapped_column(String(60))
    version: Mapped[str] = mapped_column(String(20))
    text: Mapped[str] = mapped_column(Text)
    page: Mapped[str] = mapped_column(String(500), default="")
    email_ok: Mapped[bool] = mapped_column(Boolean, default=False)
    sms_ok: Mapped[bool] = mapped_column(Boolean, default=False)
    at: Mapped[datetime] = now_col()


class Lead(TenantMixin, Base):
    __tablename__ = "leads"
    id: Mapped[str] = pk()
    firm_id: Mapped[str] = mapped_column(String(36), index=True)
    campaign_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    page_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    name_enc: Mapped[str] = mapped_column(Text, default="")
    email_enc: Mapped[str] = mapped_column(Text, default="")
    phone_enc: Mapped[str] = mapped_column(Text, default="")
    email_hmac: Mapped[str] = mapped_column(String(64), default="")
    phone_hmac: Mapped[str] = mapped_column(String(64), default="")
    interest: Mapped[str] = mapped_column(String(200), default="")
    consent_id: Mapped[str] = mapped_column(ForeignKey("consents.id"))
    click_ids: Mapped[dict] = mapped_column(JSON, default=dict)
    utm: Mapped[dict] = mapped_column(JSON, default=dict)
    state: Mapped[str] = mapped_column(String(20), default="new")  # new|contacted|booked|opted_out
    dedupe_key: Mapped[str] = mapped_column(String(64))
    assigned_advisor_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = now_col()
    first_contact_at: Mapped[datetime | None] = mapped_column(nullable=True)
    __table_args__ = (UniqueConstraint("tenant_id", "dedupe_key"),)


class LeadEvent(TenantMixin, InsertOnly, Base):
    __tablename__ = "lead_events"
    id: Mapped[str] = pk()
    lead_id: Mapped[str] = mapped_column(String(36), index=True)
    type: Mapped[str] = mapped_column(String(40))
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    at: Mapped[datetime] = now_col()


class Sequence(TenantMixin, Base):
    __tablename__ = "sequences"
    id: Mapped[str] = pk()
    lead_id: Mapped[str] = mapped_column(String(36), index=True)
    kind: Mapped[str] = mapped_column(String(20), default="checkin")
    step: Mapped[int] = mapped_column(Integer, default=0)
    next_at: Mapped[datetime | None] = mapped_column(nullable=True, index=True)
    state: Mapped[str] = mapped_column(String(12), default="active")  # active | stopped | done


class Booking(TenantMixin, Base):
    __tablename__ = "bookings"
    id: Mapped[str] = pk()
    lead_id: Mapped[str] = mapped_column(String(36), index=True)
    external_id: Mapped[str] = mapped_column(String(100), unique=True)
    starts_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = now_col()


# ---------------------------------------------------------------- D4 ads, metrics
class AdLink(TenantMixin, Base):
    """Link status only; never client passwords. Tokens live in the secrets manager."""
    __tablename__ = "ad_links"
    id: Mapped[str] = pk()
    firm_id: Mapped[str] = mapped_column(String(36))
    platform: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|linked|revoked|error
    external_account_id: Mapped[str] = mapped_column(String(60), default="")
    secret_ref: Mapped[str] = mapped_column(String(200), default="")
    __table_args__ = (UniqueConstraint("tenant_id", "firm_id", "platform"),)


class AdObject(TenantMixin, Base):
    __tablename__ = "ad_objects"
    id: Mapped[str] = pk()
    campaign_id: Mapped[str] = mapped_column(String(36), index=True)
    version: Mapped[int] = mapped_column(Integer)
    platform: Mapped[str] = mapped_column(String(10))
    object_type: Mapped[str] = mapped_column(String(30))
    platform_id: Mapped[str] = mapped_column(String(100))
    request_key: Mapped[str] = mapped_column(String(120), unique=True)
    read_back_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(String(12), default="active")


class MetricsDaily(TenantMixin, Base):
    __tablename__ = "metrics_daily"
    id: Mapped[str] = pk()
    campaign_id: Mapped[str] = mapped_column(String(36))
    day: Mapped[date] = mapped_column(Date)
    platform: Mapped[str] = mapped_column(String(10))
    spend_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    impressions: Mapped[int] = mapped_column(Integer, default=0)
    clicks: Mapped[int] = mapped_column(Integer, default=0)
    platform_conversions: Mapped[int] = mapped_column(Integer, default=0)
    verified: Mapped[bool] = mapped_column(Boolean, default=True)  # False = gap ("not verified")
    __table_args__ = (UniqueConstraint("campaign_id", "day", "platform"),)


class ConversionSent(TenantMixin, Base):
    __tablename__ = "conversions_sent"
    id: Mapped[str] = pk()
    lead_id: Mapped[str] = mapped_column(String(36))
    platform: Mapped[str] = mapped_column(String(10))
    event: Mapped[str] = mapped_column(String(30))
    request_key: Mapped[str] = mapped_column(String(120), unique=True)
    sent_at: Mapped[datetime] = now_col()


class OptimiserProposal(TenantMixin, Base):
    __tablename__ = "optimiser_proposals"
    id: Mapped[str] = pk()
    campaign_id: Mapped[str] = mapped_column(String(36), index=True)
    kind: Mapped[str] = mapped_column(String(30))  # pause_ad|budget_shift|negative_keyword|new_variant
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    explanation: Mapped[str] = mapped_column(Text, default="")
    state: Mapped[str] = mapped_column(String(12), default="proposed")  # proposed|applied|needs_precheck|rejected
    created_at: Mapped[datetime] = now_col()


# ---------------------------------------------------------------- platform plumbing (not tenant-filtered; explicit tenant_id)
class Outbox(Base):
    __tablename__ = "outbox"
    id: Mapped[str] = pk()
    type: Mapped[str] = mapped_column(String(60))
    tenant_id: Mapped[str] = mapped_column(String(36))
    firm_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    correlation_id: Mapped[str] = mapped_column(String(32), default="-")
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    idempotency_key: Mapped[str] = mapped_column(String(200))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = now_col()
    published_at: Mapped[datetime | None] = mapped_column(nullable=True, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    dead: Mapped[bool] = mapped_column(Boolean, default=False)  # dead-letter; replay by run id
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    __table_args__ = (Index("ix_outbox_pending", "published_at", "dead"),)


class ProcessedEvent(Base):
    __tablename__ = "processed_events"
    consumer: Mapped[str] = mapped_column(String(60), primary_key=True)
    event_key: Mapped[str] = mapped_column(String(200), primary_key=True)
    at: Mapped[datetime] = now_col()


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"
    key: Mapped[str] = mapped_column(String(200), primary_key=True)
    scope: Mapped[str] = mapped_column(String(100), primary_key=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    status_code: Mapped[int] = mapped_column(Integer)
    response: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = now_col()


class ActivityLog(InsertOnly, Base):
    """Append-only and hash-chained (tamper-evident). Also mirrored to locked object storage in prod."""
    __tablename__ = "activity_log"
    id: Mapped[str] = pk()
    seq: Mapped[int] = mapped_column(Integer, unique=True, nullable=True)
    tenant_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)
    actor: Mapped[str] = mapped_column(String(60))
    action: Mapped[str] = mapped_column(String(80))
    resource: Mapped[str] = mapped_column(String(120), default="")
    result: Mapped[str] = mapped_column(String(12), default="ok")  # ok | denied | error
    reason: Mapped[str] = mapped_column(String(200), default="")
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    prev_hash: Mapped[str] = mapped_column(String(64), default="")
    hash: Mapped[str] = mapped_column(String(64), default="")
    at: Mapped[datetime] = now_col()


class KillSwitch(Base):
    __tablename__ = "kill_switches"
    id: Mapped[str] = pk()
    scope: Mapped[str] = mapped_column(String(10))  # global | tenant | campaign
    scope_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    reason: Mapped[str] = mapped_column(String(300), default="")
    set_by: Mapped[str] = mapped_column(String(60), default="")
    at: Mapped[datetime] = now_col()


class UsageEvent(Base):
    """Spend is metered, not capped: per run, provider, group, tenant and campaign."""
    __tablename__ = "usage_events"
    id: Mapped[str] = pk()
    tenant_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)
    run_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)
    campaign_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)
    group_key: Mapped[str | None] = mapped_column(String(30), nullable=True)
    provider: Mapped[str] = mapped_column(String(40))
    kind: Mapped[str] = mapped_column(String(20))  # llm | search | sms | email
    units: Mapped[float] = mapped_column(Float, default=0)
    cost_micro_usd: Mapped[int] = mapped_column(BigInteger, default=0)
    at: Mapped[datetime] = now_col()


class Incident(Base):
    __tablename__ = "incidents"
    id: Mapped[str] = pk()
    kind: Mapped[str] = mapped_column(String(60))
    tenant_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)
    at: Mapped[datetime] = now_col()


TENANT_TABLES = [t.name for t in Base.metadata.sorted_tables if "tenant_id" in t.c]
