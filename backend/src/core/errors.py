from __future__ import annotations


class AdvisorGroError(Exception):
    status = 400
    code = "error"

    def __init__(self, message: str = "", *, code: str | None = None):
        super().__init__(message or self.__class__.__name__)
        if code:
            self.code = code


class NotFound(AdvisorGroError):
    status, code = 404, "not_found"


class Forbidden(AdvisorGroError):
    status, code = 403, "forbidden"


class Unauthorized(AdvisorGroError):
    status, code = 401, "unauthorized"


class StepUpRequired(AdvisorGroError):
    status, code = 401, "step_up_required"


class Conflict(AdvisorGroError):
    status, code = 409, "conflict"


class ValidationFailed(AdvisorGroError):
    status, code = 422, "validation_failed"


class IllegalTransition(Conflict):
    code = "illegal_transition"


class ImmutableRecord(AdvisorGroError):
    status, code = 500, "immutable_record"


class TenantContextMissing(AdvisorGroError):
    status, code = 500, "tenant_context_missing"


class ProviderError(AdvisorGroError):
    status, code = 502, "provider_error"


class ProviderUnavailable(ProviderError):
    code = "provider_unavailable"


class SubscriptionRequired(AdvisorGroError):
    status, code = 402, "subscription_required"


class KillSwitchActive(Conflict):
    code = "kill_switch_active"


class ApprovalMismatch(Conflict):
    code = "approval_mismatch"
