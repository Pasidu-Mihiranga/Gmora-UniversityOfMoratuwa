"""The test doubles pass the same contract suites the real modules must pass (QA-1)."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from suwapath.contracts.enums import AgentKind, DataCategory, Role
from suwapath.contracts.ids import PatientId, UserId
from suwapath.contracts.testing.doubles_clinical import InMemoryBookingService, InMemoryCaseService
from suwapath.contracts.testing.doubles_core import (
    InMemoryAccessPolicy,
    InMemoryAuditLog,
    InMemoryOpsService,
)
from suwapath.contracts.testing.suites import (
    AccessPolicyContract,
    AuditLogContract,
    BookingContract,
    CaseServiceContract,
    OpsQuotaContract,
    user,
)


class TestInMemoryAccessPolicy(AccessPolicyContract):
    @pytest.fixture
    def policy_world(self) -> tuple[InMemoryAccessPolicy, dict[str, Any]]:
        patient_user, patient, other = UserId(uuid4()), PatientId(uuid4()), PatientId(uuid4())
        guardian, doctor, admin = user(Role.GUARDIAN), user(Role.DOCTOR), user(Role.SYSTEM_ADMIN)
        policy = InMemoryAccessPolicy(
            patients_by_user={patient_user: patient},
            guardian_grants={
                (guardian.user_id, patient): frozenset(
                    {
                        DataCategory.MEDICATIONS,  # type: ignore[dict-item]
                        DataCategory.ADHERENCE,
                    }
                )
            },
            treating={(doctor.user_id, patient)},  # type: ignore[arg-type]
            agent_scopes={AgentKind.GUARDIAN_AGENT.value: frozenset({DataCategory.ADHERENCE})},
            user_roles={guardian.user_id: Role.GUARDIAN},  # type: ignore[dict-item]
        )
        return policy, {
            "patient_user": patient_user,
            "patient": patient,
            "other_patient": other,
            "guardian": guardian,
            "doctor": doctor,
            "admin": admin,
        }


class TestInMemoryAuditLog(AuditLogContract):
    @pytest.fixture
    def audit(self) -> InMemoryAuditLog:
        return InMemoryAuditLog()


class TestInMemoryCases(CaseServiceContract):
    @pytest.fixture
    def cases(self) -> InMemoryCaseService:
        return InMemoryCaseService()


class TestInMemoryBooking(BookingContract):
    @pytest.fixture
    def booking_world(self) -> tuple[InMemoryBookingService, Any]:
        service = InMemoryBookingService()
        return service, service.add_slot


class TestInMemoryOps(OpsQuotaContract):
    @pytest.fixture
    def ops(self) -> InMemoryOpsService:
        return InMemoryOpsService(turn_limit=2)
