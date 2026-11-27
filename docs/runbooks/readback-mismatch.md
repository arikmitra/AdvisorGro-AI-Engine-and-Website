# Live ad differs from the approved version (`ReadBackMismatch`)
The Publisher (or the daily verifier) recomputed the content hash from what is live and it did not equal the approved hash, so the campaign was **paused automatically** and an incident and an internal alert were written.
1. Identify the campaign from the incident (`incidents.detail`): it carries the expected and live hashes.
2. Compare what the platform holds with the approved version (portal, campaign detail). Typical causes: an edit made in the ad platform UI, platform policy rewriting copy, a CDN serving a stale or altered page.
3. Do NOT resume until the cause is understood. Resuming re-verifies and requires the approval to still be valid.
4. Tell the firm's CCO. If the change was in the platform UI, revert it there; if the creative must change, make a new version (it needs a new approval).
