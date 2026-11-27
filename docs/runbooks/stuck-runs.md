# Audit runs not finishing (`AuditRunsStuck`)
1. `ag_runs_stuck` counts runs `Queued`/`Running` for more than 10 minutes. Check worker health first (`docker compose logs worker` / the worker service).
2. A group that is late or broken becomes *unverified* and the run still finishes, so a stuck run means orchestration itself failed: look for `run ... failed` in logs by `run_id`.
3. Safe to retry: `ShieldService.execute_run(run_id)` does nothing for a run that is already past `Queued`. For a run left `Running` after a crash, set it back to `Queued` and replay its `AuditRequested` event (`EventBus.replay(run_id=...)`).
4. If a provider is the cause, disable it (`LLMGateway.disabled`) or unset its key: groups depending on it become unverified, never failed.
