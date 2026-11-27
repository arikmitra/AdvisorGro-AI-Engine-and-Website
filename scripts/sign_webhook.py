"""Sign a webhook body so you can POST it by hand (development only).

    python ../scripts/sign_webhook.py calendar '{"payload":{"uid":"bk-1","attendees":[{"email":"pat@example.com"}]}}'
    python ../scripts/sign_webhook.py sms '{"from":"+15125550100","body":"STOP"}'
Prints the two headers and a ready-to-paste curl command (use the PowerShell form in TEST.md on Windows).
"""
import sys

from advisorgro.core.config import get_settings
from advisorgro.security import webhooks

kind, body = sys.argv[1], sys.argv[2].encode()
s = get_settings()
secret = {"calendar": s.webhook_secret_calendar, "sms": s.webhook_secret_sms}[kind]
ts, sig = webhooks.sign(secret, body)
print(f"x-ag-timestamp: {ts}\nx-ag-signature: {sig}\n")
print(f"curl -s -X POST http://localhost:8000/v1/webhooks/{kind} -H 'content-type: application/json' -H 'x-ag-timestamp: {ts}' -H 'x-ag-signature: {sig}' -d '{sys.argv[2]}'")
