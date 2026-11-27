"""Campaign state machine (deck slide 9.3). Illegal moves raise; nothing goes Live except through Approved ->
Publishing -> read-back verification."""
from __future__ import annotations

from ...core.errors import IllegalTransition

TRANSITIONS: dict[str, set[str]] = {
    "Draft": {"Generating", "Retired"},
    "Generating": {"PreChecking", "Draft"},
    "PreChecking": {"PendingCCO", "Draft"},
    "PendingCCO": {"Approved", "ChangesRequested", "Rejected"},
    "ChangesRequested": {"Generating", "PreChecking", "Retired"},
    "Rejected": {"Retired"},
    "Approved": {"Publishing", "PendingCCO", "ChangesRequested"},  # PendingCCO: approval expired
    "Publishing": {"Live", "Paused", "Approved"},  # Approved: publish call failed, retry  # read-back mismatch -> Paused
    "Live": {"Paused", "Retired", "ChangesRequested"},  # an edit leaves Live running the approved version until the new one is approved
    "Paused": {"Publishing", "Live", "Retired", "ChangesRequested"},
    "Retired": set(),
}
EDITABLE = {"Draft", "ChangesRequested", "Live", "Paused", "Approved"}
RUNNING = {"Live"}


def can(frm: str, to: str) -> bool:
    return to in TRANSITIONS.get(frm, set())


def transition(campaign, to: str) -> None:
    if not can(campaign.state, to):
        raise IllegalTransition(f"{campaign.state} -> {to} is not allowed")
    campaign.state = to
