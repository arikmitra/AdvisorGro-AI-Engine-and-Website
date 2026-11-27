# Dead-lettered events (`DeadLetters`)
After 5 failed attempts an event is parked (`outbox.dead = true`, reason in `last_error`). Fix the cause, then replay by run id:
`EventBus.replay(run_id="...")` (or `event_id`). Replays are safe: `processed_events` stops duplicate side effects such as a second email.
