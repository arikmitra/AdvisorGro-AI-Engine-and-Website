"""Temporal definitions (production orchestration). Needs `pip install advisorgro[temporal]` and a Temporal cluster; the
in-process EventBus/`jobs.daily` give the same behaviour for dev and tests.

Workflows are deterministic and only call activities; every activity is idempotent so retries are safe.
Not exercised in this repository's unit tests (no Temporal server in CI): validate in the staging environment."""
from __future__ import annotations

from datetime import timedelta

try:
    from temporalio import activity, workflow
    from temporalio.common import RetryPolicy
except ImportError:  # pragma: no cover
    activity = workflow = RetryPolicy = None

TASK_QUEUE = "advisorgro"

if workflow is not None:
    RETRY = RetryPolicy(initial_interval=timedelta(seconds=2), backoff_coefficient=2.0, maximum_attempts=5)

    @workflow.defn
    class AuditWorkflow:
        """One audit run: execute the five groups under the 2-minute budget, then deliver or hold for review."""
        @workflow.run
        async def run(self, run_id: str) -> str:
            state = await workflow.execute_activity("execute_run", run_id, start_to_close_timeout=timedelta(minutes=4), retry_policy=RETRY)
            await workflow.execute_activity("drain_events", start_to_close_timeout=timedelta(minutes=2), retry_policy=RETRY)
            return state

    @workflow.defn
    class CampaignWorkflow:
        """Waits (durably, for days) for the CCO decision, then publishes and verifies. Approval arrives as a signal."""
        def __init__(self) -> None:
            self.decision: str | None = None

        @workflow.signal
        def decided(self, decision: str) -> None:
            self.decision = decision

        @workflow.run
        async def run(self, campaign_id: str) -> str:
            await workflow.wait_condition(lambda: self.decision is not None, timeout=timedelta(days=30))
            if self.decision != "approve":
                return self.decision or "expired"
            state = await workflow.execute_activity("publish_campaign", campaign_id, start_to_close_timeout=timedelta(minutes=10), retry_policy=RETRY)
            return state

    @workflow.defn
    class DailyJobsWorkflow:
        """Run on a Temporal Schedule (daily): Shield due runs, probes, read-back re-verification, metrics, check-ins."""
        @workflow.run
        async def run(self) -> dict:
            return await workflow.execute_activity("daily_jobs", start_to_close_timeout=timedelta(hours=1), retry_policy=RETRY)
