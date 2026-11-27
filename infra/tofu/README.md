# Infrastructure as code (OpenTofu)

UNVALIDATED SKELETON: written to document the target shape from the architecture deck; it has not been `tofu validate`d or
applied. Treat module inputs, IAM and sizing as a starting point for the platform engineer, and review with security.

- `modules/data`: KMS key, Postgres (RDS, private, encrypted, PITR), object storage with versioning, Object Lock (approvals, activity-log mirror), public access blocked.
- `modules/runtime`: container cluster for `api` and `worker`, secrets injection from the secrets manager, least-privilege task roles.
- `envs/staging`: composes both. Production is a separate account with the same modules and no shared credentials.
