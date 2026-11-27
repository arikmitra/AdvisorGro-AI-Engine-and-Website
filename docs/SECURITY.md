# Security notes

Controls implemented and tested are mapped in `ARCHITECTURE.md`. Summary of the model:
- **Identity**: OIDC (Keycloak) in prod, HS256 only in dev/test (refused in staging/prod). Step-up: approvals, ad-account linking and review releases need MFA within 5 minutes.
- **Authorisation**: one `authorize()`; deny by default; tenant first; roles per the guide's matrix; staff cannot approve campaigns; support needs ticket + firm consent and sees masked data; engineers need break-glass.
- **Tenant isolation, three layers**: ORM loader criteria + write guard, Postgres RLS (migration 0002), policy.
- **Data**: contact fields AES-GCM encrypted with per-tenant keys (HKDF from the KEK; swap `KeyProvider` for the cloud KMS); keyed HMAC for dedupe; PII redaction in logs; object storage per-tenant prefixes, signed short-lived links, Object Lock for immutable records.
- **Edge**: body size cap, CORS allow-list, per-client rate limit, security headers, Turnstile hook, idempotency keys, honeypot, signed + timestamped webhooks, SSRF guard on every outbound fetch the engine makes.
- **AI**: prompts are versioned files; untrusted text is passed only as delimited data with an injection preamble; model output is schema-validated (one repair retry); the model never publishes, spends or approves.
- **Supply chain / CI**: gitleaks, semgrep, pip-audit, npm audit, trivy, checkov, CODEOWNERS on the controls (`.github/`).

Known gaps are listed in `OPEN_ITEMS.md` (RLS/Temporal/IaC unvalidated, ad connectors unvalidated against live accounts, rotate the MVP keys).
