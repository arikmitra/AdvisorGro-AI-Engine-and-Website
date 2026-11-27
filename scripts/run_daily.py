"""Run the daily jobs once, in-process (what the Temporal schedule or the worker's daily tick does):
Shield monthly runs that are due, daily probes (certificate and domain expiry), read-back re-verification of live
campaigns, yesterday's metrics, due 30/60/90-day check-ins, then deliver all pending events.

    cd backend && python ../scripts/run_daily.py
"""
import json

from advisorgro.container import build_container
from advisorgro.core.config import get_settings
from advisorgro.workflows import jobs

print(json.dumps(jobs.daily(build_container(get_settings())), indent=2))
