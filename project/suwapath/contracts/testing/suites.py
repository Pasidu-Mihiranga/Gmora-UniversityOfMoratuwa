"""Shared contract suites (QA-1). Each real implementation and its test double must pass the same
suite, so code built against a double behaves the same against the real module.

Usage in a module's tests:

    class TestRealBooking(BookingContract):
        @pytest.fixture
        def service(self, ...): return RealBookingService(...)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import uuid4

import pytest

from suwapath.contracts.cases import CaseEventDraft, CaseEventKind
from suwapath.contracts.common import UntrustedText
from suwapath.contracts.enums import (
    AgentKind,
    DataCategory,
    Language,
    PrincipalKind,
    Purpose,
    Role,
)
from suwapath.contracts.errors import (
    AccessDenied,
    InvalidTransition,
    NotFound,
    QuotaExceeded,
    SafetyStop,
    SlotUnavailable,
    VersionConflict,
)
from suwapath.contracts.ids import CaseId, PatientId, PractitionerRoleId, TraceId, UserId
from suwapath.contracts.ports.audit import AuditEntryDraft, AuditQuery
from suwapath.contracts.ports.consent import AccessRequest
from suwapath.contracts.principal import Principal

TRACE = TraceId("e" * 32)


def patient_principal(user_id: UserId, patient_id: PatientId) -> Principal:
    return Principal(
        kind=PrincipalKind.USER,
        user_id=user_id,
        role=Role.PATIENT,
        acting_for=user_id,
        patient_scope=patient_id,
        purpose=Purpose.SELF_ACCESS,
        trace_id=TRACE,
    )


def user(role: Role, purpose: Purpose = Purpose.SELF_ACCESS) -> Principal:
    uid = UserId(uuid4())
    return Principal(
        kind=PrincipalKind.USER,
        user_id=uid,
        role=role,
        acting_for=uid,
        purpose=purpose,
        trace_id=TRACE,
    )


class AccessPolicyContract:
    """Fixture `policy_world` returns (policy, ids) where ids has: patient_user, patient,
    other_patient, guardian, doctor, admin. The guardian is granted {medications, adherence} for
    `patient`; the doctor treats `patient`; agent scope for guardian_agent is {adherence}."""

    async def _decide(
        self,
        policy: Any,
        principal: Principal,
        patient: PatientId,
        category: DataCategory,
        action: Literal["read", "write"] = "read",
    ) -> bool:
        decision = await policy.decide(
            AccessRequest(
                principal=principal,
                action=action,
                patient_id=patient,
                category=category,
                resource_type="X",
                purpose=principal.purpose,
            )
        )
        return bool(decision.allowed)

    async def test_patient_reads_own_record_only(self, policy_world: Any) -> None:
        policy, ids = policy_world
        me = patient_principal(ids["patient_user"], ids["patient"])
        assert await self._decide(policy, me, ids["patient"], DataCategory.CONDITIONS)
        assert not await self._decide(policy, me, ids["other_patient"], DataCategory.CONDITIONS)

    async def test_guardian_sees_only_granted_categories_and_never_writes(
        self, policy_world: Any
    ) -> None:
        policy, ids = policy_world
        g = ids["guardian"]
        assert await self._decide(policy, g, ids["patient"], DataCategory.MEDICATIONS)
        assert not await self._decide(policy, g, ids["patient"], DataCategory.CONVERSATIONS)
        assert not await self._decide(policy, g, ids["patient"], DataCategory.MEDICATIONS, "write")

    async def test_doctor_needs_treating_relationship_and_sensitive_is_hidden(
        self, policy_world: Any
    ) -> None:
        policy, ids = policy_world
        d = ids["doctor"]
        assert await self._decide(policy, d, ids["patient"], DataCategory.ALLERGIES)
        assert not await self._decide(policy, d, ids["other_patient"], DataCategory.ALLERGIES)
        assert not await self._decide(
            policy, d, ids["patient"], DataCategory.SENSITIVE_SEXUAL_HEALTH
        )

    async def test_admins_never_read_clinical_data(self, policy_world: Any) -> None:
        policy, ids = policy_world
        assert not await self._decide(policy, ids["admin"], ids["patient"], DataCategory.CONDITIONS)

    async def test_agent_gets_intersection_of_human_and_agent_scope(
        self, policy_world: Any
    ) -> None:
        policy, ids = policy_world
        agent = Principal(
            kind=PrincipalKind.AGENT,
            agent_kind=AgentKind.GUARDIAN_AGENT,
            role=Role.GUARDIAN,
            acting_for=ids["guardian"].user_id,
            purpose=Purpose.GUARDIAN_MONITORING,
            trace_id=TRACE,
        )
        assert await self._decide(policy, agent, ids["patient"], DataCategory.ADHERENCE)
        # the guardian may read medications, but the agent's own scope excludes them
        assert not await self._decide(policy, agent, ids["patient"], DataCategory.MEDICATIONS)

    async def test_system_principal_has_no_clinical_access(self, policy_world: Any) -> None:
        policy, ids = policy_world
        system = Principal(
            kind=PrincipalKind.SYSTEM, purpose=Purpose.ADMINISTRATION, trace_id=TRACE
        )
        assert not await self._decide(policy, system, ids["patient"], DataCategory.CONDITIONS)


class AuditLogContract:
    """Fixture `audit` returns an empty AuditLog."""

    def _entry(self, action: str, patient: PatientId | None = None) -> AuditEntryDraft:
        return AuditEntryDraft(
            actor_kind=PrincipalKind.USER,
            actor_id="u1",
            patient_id=patient,
            action=action,
            outcome="allow",
            trace_id=TRACE,
        )

    async def test_entries_are_chained_and_verifiable(self, audit: Any) -> None:
        for action in ("record.read", "consent.revoke", "tool.call"):
            await audit.record(self._entry(action))
        assert await audit.verify_chain()

    async def test_search_filters_by_patient_newest_first(self, audit: Any) -> None:
        patient = PatientId(uuid4())
        await audit.record(self._entry("record.read", patient))
        await audit.record(self._entry("record.read"))
        await audit.record(self._entry("record.write", patient))
        page = await audit.search(AuditQuery(patient_id=patient), None, 10)
        assert [e.action for e in page.items] == ["record.write", "record.read"]


class CaseServiceContract:
    """Fixture `cases` returns an empty CaseService."""

    async def test_open_and_append_with_versions(self, cases: Any) -> None:
        me = patient_principal(UserId(uuid4()), PatientId(uuid4()))
        state = await cases.open(
            me,
            me.patient_scope,
            UntrustedText(value="dizzy", source="patient_message"),
            Language.SI,
        )
        assert state.version == 1
        state = await cases.append(
            me,
            state.id,
            [CaseEventDraft(kind=CaseEventKind.INTAKE_STARTED, actor="patient_companion")],
            1,
        )
        assert state.version == 2

    async def test_stale_version_conflicts(self, cases: Any) -> None:
        me = patient_principal(UserId(uuid4()), PatientId(uuid4()))
        state = await cases.open(
            me, me.patient_scope, UntrustedText(value="x", source="patient_message"), Language.EN
        )
        with pytest.raises(VersionConflict):
            await cases.append(
                me,
                state.id,
                [CaseEventDraft(kind=CaseEventKind.INTAKE_STARTED, actor="patient_companion")],
                0,
            )

    async def test_red_flag_moves_case_to_emergency(self, cases: Any) -> None:
        me = patient_principal(UserId(uuid4()), PatientId(uuid4()))
        state = await cases.open(
            me, me.patient_scope, UntrustedText(value="x", source="patient_message"), Language.EN
        )
        state = await cases.append(
            me,
            state.id,
            [
                CaseEventDraft(
                    kind=CaseEventKind.RED_FLAG_RAISED,
                    data={"rule_ids": ["RF-NEURO-001"]},
                    actor="safety",
                )
            ],
            state.version,
        )
        assert state.status.value == "emergency"
        assert state.urgency is not None and state.urgency.value == "emergency"

    async def test_other_patients_cannot_see_the_case(self, cases: Any) -> None:
        me = patient_principal(UserId(uuid4()), PatientId(uuid4()))
        state = await cases.open(
            me, me.patient_scope, UntrustedText(value="x", source="patient_message"), Language.EN
        )
        stranger = patient_principal(UserId(uuid4()), PatientId(uuid4()))
        with pytest.raises(NotFound):
            await cases.get(stranger, state.id)

    async def test_closed_case_rejects_events(self, cases: Any) -> None:
        me = patient_principal(UserId(uuid4()), PatientId(uuid4()))
        state = await cases.open(
            me, me.patient_scope, UntrustedText(value="x", source="patient_message"), Language.EN
        )
        # Spec 02 section 5: a case closes from replied, emergency or handed_to_human.
        state = await cases.append(
            me,
            state.id,
            [
                CaseEventDraft(
                    kind=CaseEventKind.RED_FLAG_RAISED,
                    data={"rule_ids": ["RF-NEURO-001"]},
                    actor="safety",
                ),
                CaseEventDraft(kind=CaseEventKind.CASE_CLOSED, actor="patient"),
            ],
            state.version,
        )
        assert state.status.value == "closed"
        with pytest.raises(InvalidTransition):
            await cases.append(
                me,
                state.id,
                [CaseEventDraft(kind=CaseEventKind.INTAKE_STARTED, actor="patient_companion")],
                state.version,
            )


class BookingContract:
    """Fixture `booking_world` returns (service, add_slot); add_slot(role_id, start) -> SlotId."""

    async def test_slot_cannot_be_held_twice(self, booking_world: Any) -> None:
        service, add_slot = booking_world
        slot = add_slot(PractitionerRoleId(uuid4()), datetime.now(UTC) + timedelta(days=1))
        a = patient_principal(UserId(uuid4()), PatientId(uuid4()))
        b = patient_principal(UserId(uuid4()), PatientId(uuid4()))
        await service.hold(a, slot, None)
        with pytest.raises(SlotUnavailable):
            await service.hold(b, slot, None)

    async def test_confirm_is_idempotent_and_person_only(self, booking_world: Any) -> None:
        service, add_slot = booking_world
        slot = add_slot(PractitionerRoleId(uuid4()), datetime.now(UTC) + timedelta(days=1))
        me = patient_principal(UserId(uuid4()), PatientId(uuid4()))
        hold = await service.hold(me, slot, None)
        agent = Principal(
            kind=PrincipalKind.AGENT,
            agent_kind=AgentKind.PATIENT_COMPANION,
            role=Role.PATIENT,
            acting_for=me.user_id,
            patient_scope=me.patient_scope,
            purpose=Purpose.BOOKING,
            trace_id=TRACE,
        )
        with pytest.raises(AccessDenied):
            await service.confirm(agent, hold.id, "k1")
        first = await service.confirm(me, hold.id, "k1")
        again = await service.confirm(me, hold.id, "k1")
        assert first.id == again.id

    async def test_emergency_case_stops_booking(self, booking_world: Any) -> None:
        service, add_slot = booking_world
        slot = add_slot(PractitionerRoleId(uuid4()), datetime.now(UTC) + timedelta(days=1))
        me = patient_principal(UserId(uuid4()), PatientId(uuid4()))
        case = CaseId(uuid4())
        await service.release_holds_for_case(case)
        with pytest.raises(SafetyStop):
            await service.hold(me, slot, case)


class OpsQuotaContract:
    """Fixture `ops` returns an OpsService whose patient turn limit is 2."""

    async def test_quota_blocks_model_use_but_never_care_access(self, ops: Any) -> None:
        who = UserId(uuid4())
        await ops.reserve(who, None, 0.001, care_access=False)
        await ops.reserve(who, None, 0.001, care_access=False)
        with pytest.raises(QuotaExceeded):
            await ops.reserve(who, None, 0.001, care_access=False)
        await ops.reserve(who, None, 0.001, care_access=True)  # CUS-9a: never blocked
