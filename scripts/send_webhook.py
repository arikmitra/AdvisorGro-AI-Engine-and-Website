"""Send a correctly SIGNED webhook to a local API (development only), so you do not have to compute HMACs by hand.

    cd backend
    python ../scripts/send_webhook.py calendar '{"payload":{"uid":"bk-1","attendees":[{"email":"pat@example.com"}]}}'
    python ../scripts/send_webhook.py sms '{"from":"+1 512 555 0100","body":"STOP"}'
    python ../scripts/send_webhook.py calendar '{}' --bad-signature      # expect 401
    python ../scripts/send_webhook.py calendar '{}' --stale              # signed an hour ago: expect 401
"""
import argparse
import json
import sys
import time

import httpx

from advisorgro.core.config import get_settings
from advisorgro.security import webhooks

ap = argparse.ArgumentParser()
ap.add_argument("kind", choices=["calendar", "sms"])
ap.add_argument("body")
ap.add_argument("--base", default="http://localhost:8000")
ap.add_argument("--bad-signature", action="store_true")
ap.add_argument("--stale", action="store_true")
a = ap.parse_args()
s = get_settings()
assert s.env in ("dev", "test"), "development only"
raw = a.body.encode()
ts, sig = webhooks.sign(s.webhook_secret_calendar if a.kind == "calendar" else s.webhook_secret_sms, raw, ts=int(time.time()) - 3600 if a.stale else None)
r = httpx.post(f"{a.base}/v1/webhooks/{a.kind}", content=raw, headers={"x-ag-timestamp": ts, "x-ag-signature": "bad" if a.bad_signature else sig})
print(r.status_code, json.dumps(r.json()))
sys.exit(0 if r.status_code < 500 else 1)
