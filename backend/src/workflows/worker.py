"""Worker process: `python -m advisorgro.workflows.worker`. With Temporal installed it serves the workflows above;
otherwise it runs the in-process relay loop (single-node deployments, dev)."""
from __future__ import annotations

import asyncio
import logging
import time

from ..container import build_container
from ..core.logging import configure_logging
from . import jobs

log = logging.getLogger(__name__)


async def _temporal(c, address: str) -> None:
    from temporalio import activity
    from temporalio.client import Client
    from temporalio.worker import Worker

    from .temporal_defs import TASK_QUEUE, AuditWorkflow, CampaignWorkflow, DailyJobsWorkflow

    @activity.defn(name="execute_run")
    async def execute_run(run_id: str) -> str:
        return await asyncio.to_thread(c.shield.execute_run, run_id)

    @activity.defn(name="drain_events")
    async def drain_events() -> int:
        return await asyncio.to_thread(c.bus.drain)

    @activity.defn(name="publish_campaign")
    async def publish_campaign(campaign_id: str) -> str:
        return await asyncio.to_thread(c.publisher.publish, campaign_id)

    @activity.defn(name="daily_jobs")
    async def daily_jobs() -> dict:
        return await asyncio.to_thread(jobs.daily, c)

    client = await Client.connect(address)
    await Worker(client, task_queue=TASK_QUEUE, workflows=[AuditWorkflow, CampaignWorkflow, DailyJobsWorkflow],
                 activities=[execute_run, drain_events, publish_campaign, daily_jobs]).run()


def main() -> None:
    import os
    configure_logging()
    c = build_container()
    addr = os.environ.get("AG_TEMPORAL_ADDRESS", "")
    if addr:
        asyncio.run(_temporal(c, addr))
        return
    log.info("no AG_TEMPORAL_ADDRESS: running the in-process relay loop")
    last_daily = 0.0
    while True:
        c.bus.drain()
        if time.time() - last_daily > 86400:
            jobs.daily(c)
            last_daily = time.time()
        time.sleep(1.0)


if __name__ == "__main__":
    main()
