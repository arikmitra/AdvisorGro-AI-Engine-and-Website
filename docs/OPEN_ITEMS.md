# Open items, assumptions and what is NOT validated

Read this before relying on anything. The code and tests below are real; these are the places where the design left a choice, or where this repository could not be checked in its environment.

## Assumptions I made (please confirm)
- **Six offer templates**: the deck says "six offer templates" without naming them. `engine/gro/offers.py` has placeholders (retirement readiness, tax-aware investing, business-owner exit, inheritance transition, retirement income, portfolio second opinion).
- **The free audit is one time only per firm, ever** (identified by domain, and by CRD when given). A run that failed on our side does not use it up. After that, ongoing checks need a Shield subscription (`ShieldService.enroll` / `schedule_due`); the deck's one-hour cache applies to Shield snapshots only. There is no renewal window.
- **Free audits are auto-released unless the scan found material compliance flags or could not run** ("a scan that did not run is not a clean scan", from the MVP). The MVP reviewed *every* audit by hand; set review-all by routing every run to `InReview` if you want that back.
- **Editing a Live campaign pauses its ads** until the new version is approved (unapproved content never runs). A "keep the old version running" mode would need a separate live-vs-draft state.
- **Consent wording and quiet hours** (21:00 to 08:00, server clock) are placeholders for counsel (`renderer.CONSENT`, `Settings.quiet_hours`). Retention periods (object lock five years) likewise.
- Staff roles can release *audit reports* from the review queue; they can never approve a firm's campaign.

## Not validated here (needs a real environment)
- **Google Ads and Meta connectors** (`connectors/ads.py`) follow the public APIs but were never run against an account. Run them in shadow mode against test accounts first. Offline conversions are intentionally unimplemented until a conversion action / pixel id is configured.
- **Postgres row-level security and immutability triggers** (`migrations/0002`) cannot run on SQLite; the CI `backend` job applies them on Postgres. The ORM-level guards are tested.
- **Temporal workflows** (`workflows/temporal_defs.py`, `worker.py`) compile-check only; there is no Temporal server in CI. The in-process bus runs the same handlers and is what the tests exercise.
- **OpenTofu** (`infra/tofu`) has not been validated or applied. Keycloak realm JSON has not been imported into a running Keycloak.
- **Real search/LLM/IAPD/directory providers**: the vendored MVP groups are tested against stubs (158 tests); live calls are covered by the MVP's own provider checks, not by CI. Directory scrapers are brittle by nature.
- The portal is type-checked and builds; it has no browser tests.
- Brand-new vendor model names in config (e.g. `claude-sonnet-5-5`) should be checked against each vendor's current docs.

## Security follow-ups
- **The MVP archive contained `engine/.env` with live-looking API keys** (Gemini, SerpAPI, Google Places). It was deliberately not copied. Rotate those keys and make sure the archive is not shared further.
- `app.system = 'on'` bypasses RLS for workers. Keep the set of code paths that open system sessions small and reviewed (CODEOWNERS covers `db/`).
- Webhook secrets, link secret, JWT/KEK/HMAC keys default to dev values; `Settings` refuses to start in staging/prod with dev keys, but add the secrets-manager wiring for your platform.
