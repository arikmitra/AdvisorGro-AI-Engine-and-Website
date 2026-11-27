"""Simulate someone editing a LIVE ad inside the (fake) ad platform, to watch the read-back verifier catch it.
Dev only; works because the fake platform keeps its state in .objects/fake_google.json.

    cd backend
    python ../scripts/tamper_fake_ad.py            # changes the first live ad's first headline
    python ../scripts/run_daily.py                 # live_verified drops, the campaign becomes Paused, an incident is written
"""
import json
import os
import sys

from advisorgro.connectors.ads import FakeAdPlatform
from advisorgro.core.config import get_settings

s = get_settings()
assert s.env == "dev" and s.use_fakes, "fake platform only"
path = os.path.join(s.object_store_dir, "fake_google.json")
if not os.path.exists(path):
    sys.exit(f"{path} not found: publish a campaign first (run scripts/smoke_test.py)")
p = FakeAdPlatform("google", path)
p._load()
pid = next(iter(p.objects))
p.tamper(pid, "Guaranteed 12% returns")
print(json.dumps({"tampered": pid, "headline": p.objects[pid]["creative"]["google"]["headlines"][0]}))
