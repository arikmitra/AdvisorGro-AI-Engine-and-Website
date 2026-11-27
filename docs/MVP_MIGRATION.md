# What happened to the MVP (`advisorgro_mvp.zip`)

Following guide section 7 ("keep g01-g05, move the fan-out to workers"):

**Kept, vendored as-is** into `backend/src/advisorgro/engine/shield/mvp/` (CRLF→LF, imports made relative, nothing else changed):
the five check groups and their providers (`g01..g05`, `web`, `search`, `scrape`, `fallback`, `iapd`, `nap`, `llm`, `config`, `engine`), `kernel`, `scoring`, `fixes`, `report`, `reportjson`, `guard`, `intake`, the pure Shield rules from `shield.py` (probes, the eight RAG rows, `build_alerts`), `recommend` from `emailer.py`, and the legacy single-node `orchestrator`/`storage`/`log`.
**158 of the MVP's tests ship in `backend/tests/mvp/` and pass** (2 skipped, see below). The MVP's `g05` scanner is also the rule floor for Gro's pre-check, as the guide recommends.

**Replaced by the platform:** `server.py` → FastAPI (`/api/*` contract preserved, plus `/v1/*`); SQLite `storage.py`/`leads.py` → Postgres tables with tenant isolation; `emailer.py`/`serverconfig`/`readiness` → handlers + `connectors/messaging.py`; `orchestrator.py` thread pool → `engine/shield/orchestrator.py` (async, per-group slices, retries, no USD cap); `shield.py` store/monitor/CLI → enrolments, daily jobs, alert rules table.

**Behaviour deliberately preserved:** score semantics (lower bound when partial), "what passed" lists, `compliance_unverified`, held compliance detail never shown to prospects, the audit form contract and its honeypot, the one-free-audit-per-firm rule, `advisorgro-api.js` (patched only to send consent and an `Idempotency-Key`).

**Changed from the MVP (decide if you agree):** audits are auto-released unless flagged (the MVP held every audit for a human); the MVP's `/admin` approval-by-token is replaced by the authenticated review queue.

**Test adjustments:** 2 Gemini tests asserted a model name/thinking setting that the current config no longer uses (they now follow the config constant); 2 tests covering the MVP server/audit/email layers are skipped because the platform tests cover those paths.

**Security note:** the archive contained `engine/.env` with live-looking keys. It was not copied. Rotate them.
