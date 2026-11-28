"""Who may see what of a case (per-item categories, SEC-10, CON-11).

A case's events and communications each carry a data category. A viewer sees the case when the
access policy allows at least one of the case's categories, and then only the items and state fields
of the allowed categories. Decisions are made fresh on every call (SEC-12).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

from suwapath.contracts.cases import CaseState
from suwapath.contracts.enums import DataCategory, PrincipalKind, Role
from suwapath.contracts.errors import AccessDenied, NotFound
from suwapath.contracts.ports.consent import AccessPolicy, AccessRequest
from suwapath.contracts.principal import Principal

PolicySource = Callable[[], AccessPolicy | None]


async def allowed_categories(
    policy_source: PolicySource,
    principal: Principal,
    case: CaseState,
    categories: Iterable[DataCategory],
) -> frozenset[DataCategory]:
    policy = policy_source()
    if policy is None:  # deny by default until the consent module is bound
        return frozenset()
    allowed: set[DataCategory] = set()
    for category in sorted(set(categories)):
        decision = await policy.decide(
            AccessRequest(
                principal=principal,
                action="read",
                patient_id=case.patient_id,
                category=category,
                resource_type="Case",
                purpose=principal.purpose,
            )
        )
        if decision.allowed:
            allowed.add(category)
    return frozenset(allowed)


async def visible(
    policy_source: PolicySource,
    principal: Principal,
    case: CaseState,
    categories: Iterable[DataCategory],
) -> frozenset[DataCategory]:
    """The allowed categories, or NotFound when there are none (the case's existence is hidden)."""
    allowed = await allowed_categories(policy_source, principal, case, categories)
    if not allowed:
        raise NotFound()
    return allowed


def check_writer(principal: Principal) -> None:
    """Guardians read only (SEC-10 rule 3); admins never touch clinical data (rule 1)."""
    if principal.kind is PrincipalKind.USER and principal.role not in (Role.PATIENT, Role.DOCTOR):
        raise AccessDenied()


def filter_state(state: CaseState, allowed: frozenset[DataCategory]) -> CaseState:
    """Blank the state fields that come from categories the viewer may not see."""
    update: dict[str, object] = {}
    if DataCategory.CONVERSATIONS not in allowed:
        update |= {
            "urgency": None,
            "urgency_floor": None,
            "red_flags": (),
            "facts": (),
            "open_questions": (),
            "suggested_route": None,
            "brief_id": None,
            "pending_approval": None,
        }
    if DataCategory.APPOINTMENTS not in allowed:
        update["appointments"] = ()
    if DataCategory.ADHERENCE not in allowed:
        update["guardian_status"] = None
    return state.model_copy(update=update) if update else state
