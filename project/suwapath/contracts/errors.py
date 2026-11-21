"""Error hierarchy (docs/specs/02-contracts-and-domain.md section 6).

Each error carries a stable `code` (used in problem+json responses, API-4) and the HTTP status the
API layer maps it to. Domain code raises these; it never raises HTTP exceptions.
"""

from __future__ import annotations

from typing import ClassVar


class SuwaPathError(Exception):
    code: ClassVar[str] = "internal_error"
    http_status: ClassVar[int] = 500
    retryable: ClassVar[bool] = False

    def __init__(self, message: str = "", **details: object) -> None:
        super().__init__(message or self.code)
        self.message = message or self.code
        self.details = details


class NotFound(SuwaPathError):
    """The resource does not exist, or the principal may not learn that it exists (CON-11)."""

    code = "not_found"
    http_status = 404


class AccessDenied(SuwaPathError):
    code = "access_denied"
    http_status = 403


class ConsentDenied(SuwaPathError):
    code = "consent_denied"
    http_status = 403


class VersionConflict(SuwaPathError):
    code = "version_conflict"
    http_status = 409
    retryable = True


class InvalidTransition(SuwaPathError):
    code = "invalid_transition"
    http_status = 409


class ValidationFailed(SuwaPathError):
    code = "validation_failed"
    http_status = 422


class BudgetExceeded(SuwaPathError):
    code = "budget_exceeded"
    http_status = 429


class QuotaExceeded(SuwaPathError):
    """A person's daily quota is used up (CUS-8). Never raised on care-access paths (CUS-9a)."""

    code = "quota_exceeded"
    http_status = 429


class ProviderUnavailable(SuwaPathError):
    code = "provider_unavailable"
    http_status = 503
    retryable = True


class CapabilityMissing(SuwaPathError):
    code = "capability_missing"
    http_status = 501


class SafetyStop(SuwaPathError):
    code = "safety_stop"
    http_status = 409


class PhiEgressBlocked(SuwaPathError):
    code = "phi_egress_blocked"
    http_status = 500


class SlotUnavailable(SuwaPathError):
    code = "slot_unavailable"
    http_status = 409
