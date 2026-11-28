"""care: programmes, offerings, referrals, enrolment, doses, alert rules (CARE, PRG)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal, Protocol

from suwapath.contracts.common import Contract, Page
from suwapath.contracts.enums import DoseSlot, Language
from suwapath.contracts.fhir.base import GeoPoint
from suwapath.contracts.ids import (
    OfferingId,
    OrganizationId,
    PatientId,
    ReferralId,
    ResourceId,
    ScheduleId,
)
from suwapath.contracts.principal import Principal


class DoseItem(Contract):
    dose_id: str  # opaque: identifies (statement, scheduled time) for "mark taken"
    statement_id: ResourceId
    medication_display: str  # visible only to principals allowed `medications`
    slot: DoseSlot | None
    scheduled_at: datetime
    status: Literal["pending", "taken", "missed"]
    administration_id: ResourceId | None = None


class AdherenceDay(Contract):
    day: date
    taken: int
    missed: int
    pending: int
    missed_slots: tuple[DoseSlot, ...] = ()


class AdherenceSummary(Contract):
    patient_id: PatientId
    days: tuple[AdherenceDay, ...]
    missed_by_slot: dict[DoseSlot, int]
    current_streak_days: int


class ProgrammeInfo(Contract):
    key: str
    name: dict[Language, str]
    summary: dict[Language, str]
    enabled: bool
    content_version: str
    demonstration: bool = False  # P1 programmes (PRG-7)


class OfferingInfo(Contract):
    id: OfferingId
    programme_key: str
    organization_id: OrganizationId
    organization_name: str
    title: str
    location: GeoPoint
    distance_km: float | None = None
    next_session_id: ScheduleId | None = None
    next_session_at: datetime | None = None
    fee_lkr: int
    status: Literal["draft", "pending_approval", "approved", "rejected", "archived"]


class Enrollment(Contract):
    id: ResourceId
    patient_id: PatientId
    programme_key: str
    offering_id: OfferingId | None
    source: Literal["self", "referral"]
    status: Literal["active", "ended"]
    started_at: datetime


class Referral(Contract):
    id: ReferralId
    patient_id: PatientId
    programme_key: str
    offering_id: OfferingId | None
    status: Literal["pending", "accepted", "declined"]


class CareService(Protocol):
    async def doses_for_day(
        self, principal: Principal, patient_id: PatientId, day: date
    ) -> list[DoseItem]: ...

    async def mark_taken(
        self,
        principal: Principal,
        patient_id: PatientId,
        statement_id: ResourceId,
        scheduled_at: datetime,
        idempotency_key: str,
    ) -> DoseItem: ...

    async def adherence(
        self, principal: Principal, patient_id: PatientId, days: int = 7
    ) -> AdherenceSummary: ...

    async def programmes(self, principal: Principal) -> list[ProgrammeInfo]: ...

    async def offerings(
        self,
        principal: Principal,
        programme_key: str,
        near: GeoPoint | None,
        cursor: str | None,
        limit: int,
    ) -> Page[OfferingInfo]:
        """Approved only, by distance then next session; fees never boost (PRG-4)."""
        ...

    async def enroll(
        self,
        principal: Principal,
        patient_id: PatientId,
        programme_key: str,
        offering_id: OfferingId | None,
        referral_id: ReferralId | None,
    ) -> Enrollment: ...

    async def refer(
        self,
        principal: Principal,
        patient_id: PatientId,
        programme_key: str,
        offering_id: OfferingId | None,
    ) -> Referral:
        """Requires a treating relationship (PRG-5)."""
        ...

    async def decide_referral(
        self, principal: Principal, referral_id: ReferralId, accept: bool
    ) -> Referral: ...

    async def save_offering(self, principal: Principal, offering: OfferingInfo) -> OfferingInfo: ...

    async def decide_offering(
        self, principal: Principal, offering_id: OfferingId, approve: bool
    ) -> OfferingInfo: ...

    async def set_programme_enabled(
        self, principal: Principal, key: str, enabled: bool
    ) -> ProgrammeInfo: ...
