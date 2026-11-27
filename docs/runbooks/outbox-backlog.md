# Events not being delivered (`OutboxBacklog`)
The outbox relay delivers at least once; consumers are idempotent. Check the worker is running, then `ag_outbox_pending`.
A growing backlog with a healthy worker means one consumer is slow: find it in `outbox.last_error`. Restarting the worker is safe.
