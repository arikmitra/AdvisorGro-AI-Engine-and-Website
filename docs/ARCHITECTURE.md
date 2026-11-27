# Architecture and design-to-code traceability

## Flows
**Free audit.** `POST /v1/audits` (or the MVP-compatible `/api/audit`) → validation, honeypot, consent, the once-per-firm free-audit rule (later runs need Shield), rate limit, `Idempotency-Key` → `Run` + `AuditRequest` + `AuditRequested` event written in one transaction (outbox) → **202 immediately**. A worker (or the in-process relay in dev) runs the five groups concurrently under a 120 s budget (`engine/shield/orchestrator.py`) → record (scorecard, delta, review gate, fixes) → reports → if releasable: unguessable, expiring, revocable report link emailed; otherwise `InReview` for a person.

**Campaign.** `submit_for_review` → brief (LLM angles, deterministic fallback) → creative (Google/Meta/page/emails in parallel, schema-validated, one repair retry) → **fact guard** (every number/award/return must match an approved fact) → version row → **pre-check** (MVP rule scanner is the floor; model can only add) → renderer (one content hash over normalised text, links, disclosures) → `PendingCCO`. CCO decision (fresh MFA, hash must match) → `Approved` → Publisher: policy → create everything **paused** → **read back** → recompute hash → equal: activate, `Live`; different: stay paused + incident. Re-verified daily.

**Lead.** Public form on an *approved, live* page → consent stored first → encrypted lead, keyed-hash dedupe → `LeadCreated` → first reply ≤ 90 s from approved templates (code fills personal data) → 30/60/90 check-ins → STOP/booking stop everything → booking webhook → consent-gated conversion to the ad platform.

## Where each invariant is enforced (and tested)
| Invariant | Enforced in | Tested in |
|---|---|---|
| Late/broken group is *unverified*, run still completes | `engine/shield/orchestrator.py` | `test_shield_service::test_a_group_that_explodes_or_is_late...` |
| Unscanned compliance is not a clean scan | MVP `_review_gate` via `record.assemble` | `test_unverified_compliance_is_not_a_clean_scan` |
| Held compliance detail never reaches the prospect | MVP `safe_payload`, release gate | `test_held_compliance_goes_to_review...` |
| Report link is the credential (hashed, expiring, revocable) | `ShieldService.open_report` | `test_report_link_is_the_credential` |
| Events at-least-once, consumers idempotent, dead-letter + replay | `core/events.py` | `test_outbox_is_idempotent...`, `test_dead_letter_and_replay` |
| Tenant from the signed token only; other tenant = 404 | `db/session.py`, `api/deps.py` | `test_orm_tenant_scoping`, `test_auth_required_and_tenant_isolation` |
| One policy function, deny by default, matrix of roles | `security/policy.py` | `test_permission_matrix` (16 cases), staff/break-glass tests |
| Drafter ≠ approver; staff never approve; deputy only in window | `policy.py`, `approvals.py` | `test_approval_refusals`, `test_drafter_cannot_approve_own_work`, `test_deputy...` |
| Approvals/versions insert-only (ORM + DB trigger) | `db/session.py`, migration 0002 | `test_approvals_are_insert_only` |
| Approval is per exact version + content hash; edit invalidates | `approvals.py`, `policy.valid_approval` | `test_edit_makes_new_version...` |
| Fact guard blocks invented claims | `engine/gro/factguard.py` | `test_fact_guard_blocks_invented_claims`, golden evals |
| Nothing live without read-back match; drift auto-pauses | `engine/gro/publisher.py` | `test_read_back_mismatch_pauses...` |
| Kill switch, budget ceiling | `engine/gro/policy.py` | `test_publish_blocked_by_kill_switch...`, `test_optimiser_cannot_exceed_ceiling` |
| Publish retries never create a second campaign | request keys in `publisher.py` / connector | `test_publish_retry_is_idempotent` |
| Optimiser: rules decide, model explains, cannot launch | `engine/gro/measure.py` | `test_metrics_gap_is_not_zero_and_optimiser_guardrails` |
| Weekly report numbers come from code | `measure.numbers_ok` | `test_weekly_report_numbers_come_from_code` |
| Consent first, masked leads, 90 s SLA, STOP stops all | `leads/service.py` | `test_lead_capture_followup_sla_optout...`, `test_sms_stop_ends_sequences` |
| Webhooks signed + timestamped; stale refused | `security/webhooks.py` | `test_webhook_signature...`, API webhook test |
| Field encryption bound to tenant; keyed-hash dedupe | `security/crypto.py` | `test_crypto_round_trip_tenant_binding...` |
| Tamper-evident activity log | `security/activity.py` | `test_activity_log_is_hash_chained...` |
| SSRF guard, body cap, CORS allow-list, rate limit, security headers | `core/ssrf.py`, `api/main.py`, `api/deps.py` | `test_ssrf_guard`, `test_edge_protections`, `test_rate_limit` |
| Dev secrets refused in staging/prod | `core/config.py` | `test_prod_refuses_every_dev_secret` |

## Event catalogue (outbox → handlers in `workflows/handlers.py`)
`AuditRequested`, `ReportReady`, `ShieldRunDue`, `AlertRaised`, `CampaignSubmitted`, `CampaignApproved`, `AdPublished`, `CampaignPaused`, `LeadCreated`, `BookingConfirmed`. Each carries tenant, firm, correlation id, schema version and an idempotency key.

## Decisions worth knowing
- Spend is **metered, not capped** (`usage_events`, per run/group/campaign); the MVP's USD cap was removed per the guide. The *client's* budget ceiling for ads is a hard cap.
- The API is synchronous (`def` endpoints in a thread pool), engines use `asyncio.run` at the edges; swap to a queue worker without changing handlers.
- Timestamps are stored as naive UTC and returned timezone-aware (`db/base.py::UTCDateTime`).
