"""Postgres row-level security and DB-level immutability (defence in depth behind the ORM guards).

Revision ID: 0002
Revises: 0001

RLS: every tenant table is filtered by current_setting('app.tenant_id'), which the API sets per transaction from the
verified token. `app.system = 'on'` is for workers that cross tenants (outbox relay, schedulers); the app role is NOT a
superuser and does NOT own the tables, so FORCE ROW LEVEL SECURITY applies to it.
Immutability: approvals, versions, consents, alerts, lead events and the activity log reject UPDATE and DELETE.
No-op on non-Postgres databases (the ORM guards still apply)."""
from alembic import op

revision = "0002"
down_revision = "0001"

TENANT_TABLES = ['audit_requests', 'runs', 'group_results', 'findings', 'reports', 'shield_enrollments', 'alerts', 'directory_snapshot', 'review_items', 'campaigns', 'campaign_versions', 'precheck_reports', 'rendered_sets', 'approvals', 'knowledge_facts', 'consents', 'leads', 'lead_events', 'sequences', 'bookings', 'ad_links', 'ad_objects', 'metrics_daily', 'conversions_sent', 'optimiser_proposals', 'firms', 'memberships', 'delegations', 'api_keys']
INSERT_ONLY = ['alerts', 'campaign_versions', 'precheck_reports', 'rendered_sets', 'approvals', 'consents', 'lead_events', 'activity_log']


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for t in TENANT_TABLES:
        op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {t} USING ("
            f"current_setting('app.system', true) = 'on' OR tenant_id = current_setting('app.tenant_id', true)) "
            f"WITH CHECK (current_setting('app.system', true) = 'on' OR tenant_id = current_setting('app.tenant_id', true))")
    op.execute("""CREATE OR REPLACE FUNCTION ag_reject_change() RETURNS trigger AS $$
        BEGIN RAISE EXCEPTION '% is insert-only', TG_TABLE_NAME USING ERRCODE = 'integrity_constraint_violation'; END; $$ LANGUAGE plpgsql""")
    for t in INSERT_ONLY:
        op.execute(f"CREATE TRIGGER {t}_immutable BEFORE UPDATE OR DELETE ON {t} FOR EACH ROW EXECUTE FUNCTION ag_reject_change()")
        op.execute(f"CREATE TRIGGER {t}_no_truncate BEFORE TRUNCATE ON {t} FOR EACH STATEMENT EXECUTE FUNCTION ag_reject_change()")


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for t in INSERT_ONLY:
        op.execute(f"DROP TRIGGER IF EXISTS {t}_immutable ON {t}")
        op.execute(f"DROP TRIGGER IF EXISTS {t}_no_truncate ON {t}")
    for t in TENANT_TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {t}")
        op.execute(f"ALTER TABLE {t} DISABLE ROW LEVEL SECURITY")
