# AdvisorGro

Monorepo for the AdvisorGro platform, built from the *Production System Architecture* deck and the *Tooling and Implementation Guide*, with the MVP engine and your website design folded in.

- **Shield** (free audit and monthly monitoring): five check groups scored out of 100. A check that is late or broken becomes *unverified*; it is never a failed run and never counted as zero.
- **Gro** (campaigns): brief → generate → **fact guard** → **pre-check** → render + content hash → **CCO approval** → publish → **read-back verification** → leads → follow-up → measurement. AI drafts; code decides; a human approves.

```
advisorgro/
├── backend/                     Python 3.11+: API, engines, workers
│   ├── src/advisorgro/
│   │   ├── core/                config, ids, hashing (content hash), SSRF guard, redaction, events (outbox), idempotency
│   │   ├── db/                  models, tenant-scoped sessions, insert-only guards
│   │   ├── security/            policy (single authorise fn), crypto, tokens + step-up, webhooks, hash-chained activity log
│   │   ├── llm/                 gateway (fallback, schema repair, metering), prompt registry + versioned prompts, fakes
│   │   ├── engine/
│   │   │   ├── shield/          async orchestrator, record, alerts, service; mvp/ = vendored MVP check groups g01-g05
│   │   │   └── gro/             states, schemas, offers, knowledge, factguard, precheck, renderer, approvals, policy,
│   │   │                        campaigns, publisher, pages, measure (metrics, optimiser, weekly report)
│   │   ├── connectors/          Google Ads, Meta, email/SMS, fake ad platform
│   │   ├── leads/               capture, consent, dedupe, 90 s follow-up, opt-out, bookings, conversions
│   │   ├── workflows/           event handlers, daily jobs, Temporal definitions, worker
│   │   ├── api/                 FastAPI app, middleware, public + portal routers
│   │   ├── observability/       metrics, OpenTelemetry
│   │   └── storage/             object store (local / S3 with Object Lock)
│   ├── migrations/              Alembic (0002 = Postgres RLS + immutability triggers)
│   └── tests/                   237 tests: 235 pass, 2 skipped (incl. 158 vendored MVP engine tests)
├── portal/                      React + Vite + TypeScript: approval console, campaigns, Shield, leads, activity
├── website/                     Static site from your design bundle, wired to the API via assets/advisorgro-api.js
├── infra/                       docker, compose, Keycloak realm, OTel, Prometheus alerts, OpenTofu skeleton
├── data/dbt/                    PII-free analytics marts
├── evals/                       golden evals (run in CI) + promptfoo config
├── docs/                        ARCHITECTURE, SECURITY, OPEN_ITEMS, MVP_MIGRATION, runbooks
├── scripts/seed_dev.py          one firm, one user per role, dev tokens
└── .github/                     CI, CODEOWNERS
```

## Run it locally (no API keys needed)
```bash
make install           # backend deps
make test              # 235 passed, 2 skipped
make run               # API on :8000 with fake providers (AG_USE_FAKES=true)
make website           # site on :8081 (site.config.js points at localhost:8000)
make seed && make portal   # paste a printed token into the portal sign-in
# or everything: docker compose up --build
```
Try the free audit at http://localhost:8081, or `curl -X POST localhost:8000/api/audit -H 'content-type: application/json' -d '{"website":"yourfirm.com","email":"you@yourfirm.com","area":"Austin, Texas","consent":true}'`.
With fakes the five groups return canned results (domain keywords `held`, `nog5`, `slow`, `boom`, `lowtls` force each path); scoring, the review gate, reports, tokens, events and email all run for real.

**Testing locally in VS Code:** see [`TEST.md`](TEST.md).

**Testing in VS Code:** see **[TEST.md](TEST.md)**: setup, the 237 automated tests, a 31-check smoke test, step-by-step manual scenarios with expected results, browser walkthroughs, and troubleshooting. `.vscode/` ships tasks, debug configurations and a REST Client file. Helper scripts in `scripts/`: `seed_dev.py`, `mint_token.py` (fresh dev tokens per role), `smoke_test.py`, `run_daily.py`, `sign_webhook.py`.

For real providers set `AG_USE_FAKES=false` plus keys (`.env.example`). **Read `docs/OPEN_ITEMS.md` first**: it lists my assumptions and everything that could not be validated in this environment.
