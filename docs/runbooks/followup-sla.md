# First reply missing the 90-second SLA (`FollowUpSlaBreach`)
Check `lead_events` (`type = first_contact`, `meta.latency_s`, `meta.channels`). Usual causes: email/SMS provider outage (`incidents` kinds `followup_email_failed` / `followup_sms_failed`), quiet-hours deferral of SMS (expected), worker backlog (see outbox-backlog). A lead with no consent for a channel is never contacted on it.
