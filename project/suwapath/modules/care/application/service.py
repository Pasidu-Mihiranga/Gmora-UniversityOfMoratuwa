"""`CareService` on Postgres (M09, CARE-2 to CARE-8).

- **Doses.** Each active, scheduled medicine has a `dose_check` timer per dose for the next 48 h;
  a daily `dose_window` timer per patient rolls the window forward (CARE-3). A dose marked taken
  records the administration, announces `care.dose_taken` and cancels its timer (CARE-4); a timer
  that fires with nothing recorded records a miss (CARE-5, `doses.on_timer`).
- **Adherence** per Sri Lanka day: taken, missed, pending, missed times of day, and the run of full
  days (CARE-6). Medicine names appear only for principals allowed `medications`.
- **Alert rules** (CARE-7, CARE-8): a registry of three types. A guardian's rule may only use
  categories the patient granted, is versioned, fires at most once per window, re-checks the grant
  when it fires, and triggers the guardian agent (never messages the guardian directly).
"""

from __future__ import annotations

import base64
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from suwapath.contracts.common import Contract, Page
from suwapath.contracts.enums import DataCategory, DoseSlot, PrincipalKind, Purpose, Role
from suwapath.contracts.errors import AccessDenied, CapabilityMissing, NotFound, ValidationFailed
from suwapath.contracts.events import EventEnvelope
from suwapath.contracts.events.catalog import AlertRuleFired, DoseMissed, DoseTaken, TimerFired
from suwapath.contracts.ids import PatientId, ResourceId, TraceId, UserId
from suwapath.contracts.ports.care import AdherenceDay, AdherenceSummary, DoseItem
from suwapath.contracts.ports.consent import AccessPolicy, AccessRequest
from suwapath.contracts.ports.record import RecordService
from suwapath.contracts.principal import Principal
from suwapath.modules.care.adapters.models import AlertRuleRow, MedicationAdministrationRow
from suwapath.modules.care.application import doses
from suwapath.modules.care.domain.schedule import day_bounds, doses_between
from suwapath.platform.clock import COLOMBO
from suwapath.platform.ids import new_id
from suwapath.platform.uow import UnitOfWork, UnitOfWorkFactory

WINDOW = timedelta(hours=48)  # CARE-3
WINDOW_TIMER = "dose_window"
SYSTEM_TRACE = TraceId("0" * 31 + "c")

# ----- alert rule registry (CARE-7) --------------------------------------------------------------


class MissedDosesParams(Contract):
    count: int = Field(ge=1, le=20)
    window_hours: int = Field(ge=1, le=24 * 14)
    slot: DoseSlot | None = None


class NoResponseParams(Contract):
    hours: int = Field(ge=1, le=72)


class RedFlagParams(Contract):
    pass


@dataclass(frozen=True)
class RuleType:
    params: type[BaseModel]
    needs: frozenset[DataCategory]  # what the guardian must be granted to own this rule
    guardian_may_create: bool


RULE_TYPES: dict[str, RuleType] = {
    "missed_doses": RuleType(MissedDosesParams, frozenset({DataCategory.ADHERENCE}), True),
    "no_checkin_response": RuleType(NoResponseParams, frozenset({DataCategory.ADHERENCE}), True),
    "red_flag": RuleType(RedFlagParams, frozenset(), False),  # always on, for every guardian
}


class AlertRule(Contract):
    id: ResourceId
    patient_id: PatientId
    rule_type: str
    params: dict[str, Any]
    version: int
    active: bool


class RuleRequest(Contract):
    patient_id: PatientId
    rule_type: Literal["missed_doses", "no_checkin_response"]
    params: dict[str, Any]


class SqlCareService:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        policy: Callable[[], AccessPolicy | None],
        record: Callable[[], RecordService | None],
    ) -> None:
        self._uow = uow_factory
        self._policy = policy
        self._record = record

    # ----- doses ------------------------------------------------------------------------------

    def today(self) -> date:
        """Today in Sri Lanka, from the Clock port (EVT-16)."""
        return self._uow.clock.now().astimezone(COLOMBO).date()

    async def doses_for_day(
        self, principal: Principal, patient_id: PatientId, day: date
    ) -> list[DoseItem]:
        await self._require(principal, patient_id, DataCategory.ADHERENCE, "read")
        begin, end = day_bounds(day)
        names = await self._names(principal, patient_id)
        schedules = await self._schedules(patient_id)
        async with self._uow.join() as uow:
            recorded = await _administrations(uow, patient_id, begin, end)
        items = []
        for schedule in schedules:
            for at, slot in doses_between(schedule.timing, begin, end):
                done = recorded.get((schedule.statement_id, at))
                status = (
                    "pending" if done is None else ("taken" if done[0] == "completed" else "missed")
                )
                items.append(
                    DoseItem(
                        dose_id=dose_id(schedule.statement_id, at),
                        statement_id=schedule.statement_id,
                        medication_display=names.get(schedule.statement_id, ""),
                        slot=slot,
                        scheduled_at=at,
                        status=status,
                        administration_id=ResourceId(done[1]) if done else None,
                    )
                )
        return sorted(items, key=lambda d: (d.scheduled_at, d.medication_display))

    async def mark_taken(
        self,
        principal: Principal,
        patient_id: PatientId,
        statement_id: ResourceId,
        scheduled_at: datetime,
        idempotency_key: str,
    ) -> DoseItem:
        """Idempotent; a dose recorded missed and then taken late becomes taken (CARE-4)."""
        await self._require(principal, patient_id, DataCategory.ADHERENCE, "write")
        schedule = next(
            (s for s in await self._schedules(patient_id) if s.statement_id == statement_id), None
        )
        if schedule is None:
            raise NotFound()
        slot = next(
            (
                sl
                for at, sl in doses_between(
                    schedule.timing, scheduled_at, scheduled_at + timedelta(seconds=1)
                )
                if at == scheduled_at
            ),
            None,
        )
        if slot is None:
            raise NotFound()  # not a dose of this medicine
        async with self._uow.join() as uow:
            now = uow.clock.now()
            administration = ResourceId(new_id())
            row = (
                await uow.session.execute(
                    insert(MedicationAdministrationRow)
                    .values(
                        id=administration,
                        patient_id=patient_id,
                        medication_statement_id=statement_id,
                        scheduled_at=scheduled_at,
                        slot=slot.value,
                        status="completed",
                        recorded_at=now,
                        recorded_by="patient" if principal.role is Role.PATIENT else "companion",
                    )
                    .on_conflict_do_update(
                        index_elements=["medication_statement_id", "scheduled_at"],
                        set_={"status": "completed", "status_reason": None, "recorded_at": now},
                        where=MedicationAdministrationRow.status != "completed",
                    )
                    .returning(MedicationAdministrationRow.id)
                )
            ).scalar_one_or_none()
            if row is not None:  # a change: announce it (a repeat tap changes nothing)
                uow.publish(
                    DoseTaken(
                        administration_id=ResourceId(row),
                        statement_id=statement_id,
                        patient_id=patient_id,
                    ),
                    aggregate_id=str(row),
                    patient_id=patient_id,
                )
                await uow.cancel_timer(f"dose:{statement_id}:{scheduled_at.isoformat()}")
            current = (
                await _administrations(
                    uow, patient_id, scheduled_at, scheduled_at + timedelta(seconds=1)
                )
            )[(statement_id, scheduled_at)]
        names = await self._names(principal, patient_id)
        return DoseItem(
            dose_id=dose_id(statement_id, scheduled_at),
            statement_id=statement_id,
            medication_display=names.get(statement_id, ""),
            slot=slot,
            scheduled_at=scheduled_at,
            status="taken",
            administration_id=ResourceId(current[1]),
        )

    async def adherence(
        self, principal: Principal, patient_id: PatientId, days: int = 7
    ) -> AdherenceSummary:
        await self._require(principal, patient_id, DataCategory.ADHERENCE, "read")
        days = max(1, min(days, 31))
        schedules = await self._schedules(patient_id)
        async with self._uow.join() as uow:
            now = uow.clock.now()
            today = now.astimezone(COLOMBO).date()
            first = today - timedelta(days=days - 1)
            recorded = await _administrations(
                uow, patient_id, day_bounds(first)[0], day_bounds(today)[1]
            )
        summary_days, by_slot = [], {s: 0 for s in DoseSlot}
        for n in range(days):
            day = first + timedelta(days=n)
            begin, end = day_bounds(day)
            taken = missed = pending = 0
            missed_slots: list[DoseSlot] = []
            for schedule in schedules:
                for at, slot in doses_between(
                    schedule.timing, begin, min(end, now + timedelta(seconds=1))
                ):
                    done = recorded.get((schedule.statement_id, at))
                    if done is None:
                        pending += 1
                    elif done[0] == "completed":
                        taken += 1
                    else:
                        missed += 1
                        by_slot[slot] += 1
                        if slot not in missed_slots:
                            missed_slots.append(slot)
            summary_days.append(
                AdherenceDay(
                    day=day,
                    taken=taken,
                    missed=missed,
                    pending=pending,
                    missed_slots=tuple(missed_slots),
                )
            )
        streak = 0
        for item in reversed(summary_days):
            if item.missed or item.pending:
                if item.day == today and not item.missed:
                    continue  # today still in progress
                break
            if item.taken:
                streak += 1
        return AdherenceSummary(
            patient_id=patient_id,
            days=tuple(summary_days),
            missed_by_slot={s: n for s, n in by_slot.items() if n},
            current_streak_days=streak,
        )

    # ----- the rolling 48 hour window (CARE-3) -------------------------------------------------

    async def extend_window(self, uow: UnitOfWork, patient_id: PatientId) -> int:
        """Schedule every dose check for the next 48 hours, and tomorrow's extension."""
        now = uow.clock.now()
        scheduled = 0
        for schedule in await self._schedules(patient_id):
            for at, slot in doses_between(schedule.timing, now, now + WINDOW):
                if await doses.schedule_dose_check(
                    uow,
                    patient_id=patient_id,
                    statement_id=schedule.statement_id,
                    scheduled_at=at,
                    slot=slot,
                ):
                    scheduled += 1
        tomorrow = now + timedelta(days=1)
        await uow.schedule_timer(
            kind=WINDOW_TIMER,
            due_at=tomorrow,
            ref_type="patient",
            ref_id=str(patient_id),
            dedupe_key=f"window:{patient_id}:{tomorrow.astimezone(COLOMBO).date()}",
            payload={},
        )
        return scheduled

    async def on_resource_written(self, envelope: EventEnvelope, uow: UnitOfWork) -> None:
        payload = envelope.payload
        if getattr(payload, "resource_type", None) == "MedicationStatement":
            await self.extend_window(uow, PatientId(payload.patient_id))  # type: ignore[attr-defined]

    async def on_timer(self, envelope: EventEnvelope, uow: UnitOfWork) -> None:
        payload = envelope.payload
        assert isinstance(payload, TimerFired)
        if payload.kind == WINDOW_TIMER:
            await self.extend_window(uow, PatientId(UUID(payload.ref_id)))

    # ----- alert rules (CARE-7, CARE-8) --------------------------------------------------------

    async def create_rule(self, principal: Principal, request: RuleRequest) -> AlertRule:
        kind = RULE_TYPES[request.rule_type]
        if (
            principal.kind is not PrincipalKind.USER
            or principal.role is not Role.GUARDIAN
            or not kind.guardian_may_create
        ):
            raise AccessDenied()
        try:
            params = kind.params.model_validate(request.params).model_dump(mode="json")
        except ValidationError:
            raise ValidationFailed("invalid rule settings", fields=["params"]) from None
        for category in kind.needs:  # CARE-8: only what the patient shared with this guardian
            await self._require(principal, request.patient_id, category, "read")
        async with self._uow.join() as uow:
            existing = (
                await uow.session.execute(
                    select(AlertRuleRow).where(
                        AlertRuleRow.owner_id == principal.user_id,
                        AlertRuleRow.patient_id == request.patient_id,
                        AlertRuleRow.rule_type == request.rule_type,
                        AlertRuleRow.active.is_(True),
                    )
                )
            ).scalar()
            if existing is None:
                existing = AlertRuleRow(
                    id=new_id(),
                    owner_id=principal.user_id,
                    patient_id=request.patient_id,
                    rule_type=request.rule_type,
                    params=params,
                    version=1,
                )
                uow.session.add(existing)
            else:
                existing.params, existing.version = params, existing.version + 1
                existing.updated_at = uow.clock.now()
            await uow.session.flush()
            return _rule(existing)

    async def rules_for(self, principal: Principal) -> list[AlertRule]:
        if principal.kind is not PrincipalKind.USER or principal.role is not Role.GUARDIAN:
            raise AccessDenied()
        async with self._uow.join() as uow:
            rows = (
                await uow.session.execute(
                    select(AlertRuleRow).where(
                        AlertRuleRow.owner_id == principal.user_id, AlertRuleRow.active.is_(True)
                    )
                )
            ).scalars()
            return [_rule(r) for r in rows]

    async def on_dose_missed(self, envelope: EventEnvelope, uow: UnitOfWork) -> None:
        """Evaluate `missed_doses` rules; each fires at most once per its window."""
        payload = envelope.payload
        assert isinstance(payload, DoseMissed)
        now = uow.clock.now()
        rules = (
            await uow.session.execute(
                select(AlertRuleRow).where(
                    AlertRuleRow.patient_id == payload.patient_id,
                    AlertRuleRow.rule_type == "missed_doses",
                    AlertRuleRow.active.is_(True),
                )
            )
        ).scalars()
        for rule in rules:
            params = MissedDosesParams.model_validate(rule.params)
            window = timedelta(hours=params.window_hours)
            if rule.last_fired_at is not None and now - rule.last_fired_at < window:
                continue
            query = select(func.count()).where(
                MedicationAdministrationRow.patient_id == payload.patient_id,
                MedicationAdministrationRow.status == "not-done",
                MedicationAdministrationRow.scheduled_at > now - window,
            )
            if params.slot is not None:
                query = query.where(MedicationAdministrationRow.slot == params.slot.value)
            if (await uow.session.execute(query)).scalar_one() < params.count:
                continue
            if not await self._guardian_still_granted(UserId(rule.owner_id), payload.patient_id):
                continue  # the patient withdrew the grant since the rule was made
            rule.last_fired_at = now
            uow.publish(
                AlertRuleFired(
                    rule_id=ResourceId(rule.id),
                    rule_type=rule.rule_type,
                    patient_id=payload.patient_id,
                    guardian_id=UserId(rule.owner_id),
                ),
                aggregate_id=str(rule.id),
                patient_id=payload.patient_id,
            )

    # ----- programmes, offerings and referrals are P01 to P03 ----------------------------------

    async def programmes(self, principal: Principal) -> list[Any]:
        raise CapabilityMissing("programmes arrive with P01")

    async def offerings(self, *args: Any, **kwargs: Any) -> Page[Any]:
        raise CapabilityMissing("programmes arrive with P01")

    async def enroll(self, *args: Any, **kwargs: Any) -> Any:
        raise CapabilityMissing("programmes arrive with P01")

    async def refer(self, *args: Any, **kwargs: Any) -> Any:
        raise CapabilityMissing("programmes arrive with P01")

    async def decide_referral(self, *args: Any, **kwargs: Any) -> Any:
        raise CapabilityMissing("programmes arrive with P01")

    async def save_offering(self, *args: Any, **kwargs: Any) -> Any:
        raise CapabilityMissing("programmes arrive with P01")

    async def decide_offering(self, *args: Any, **kwargs: Any) -> Any:
        raise CapabilityMissing("programmes arrive with P01")

    async def set_programme_enabled(self, *args: Any, **kwargs: Any) -> Any:
        raise CapabilityMissing("programmes arrive with P01")

    # ----- internals ----------------------------------------------------------------------------

    async def _require(
        self, principal: Principal, patient_id: PatientId, category: DataCategory, action: str
    ) -> None:
        policy = self._policy()
        decision = (
            await policy.decide(
                AccessRequest(
                    principal=principal,
                    action=action,
                    patient_id=patient_id,
                    category=category,
                    resource_type="Care",
                    purpose=principal.purpose,
                )
            )
            if policy is not None
            else None
        )
        if decision is None or not decision.allowed:
            raise NotFound()  # a patient the principal may not see looks like no patient (CON-11)

    async def _guardian_still_granted(self, guardian: UserId, patient_id: PatientId) -> bool:
        principal = Principal(
            kind=PrincipalKind.USER,
            user_id=guardian,
            role=Role.GUARDIAN,
            acting_for=guardian,
            purpose=Purpose.GUARDIAN_MONITORING,
            trace_id=SYSTEM_TRACE,
        )
        try:
            await self._require(principal, patient_id, DataCategory.ADHERENCE, "read")
        except NotFound:
            return False
        return True

    async def _schedules(self, patient_id: PatientId) -> list[Any]:
        record = self._record()
        return [] if record is None else await record.medication_schedules(patient_id)

    async def _names(self, principal: Principal, patient_id: PatientId) -> dict[ResourceId, str]:
        """Medicine names only for principals allowed `medications` (CARE-6)."""
        record = self._record()
        if record is None:
            return {}
        try:
            page = await record.list_resources(
                principal, patient_id, "MedicationStatement", None, 100
            )
        except NotFound:
            return {}
        return {r.id: (r.medication.text or "") for r in page.items}  # type: ignore[attr-defined]


async def _administrations(
    uow: UnitOfWork, patient_id: PatientId, begin: datetime, end: datetime
) -> dict[tuple[UUID, datetime], tuple[str, UUID]]:
    rows = await uow.session.execute(
        select(
            MedicationAdministrationRow.medication_statement_id,
            MedicationAdministrationRow.scheduled_at,
            MedicationAdministrationRow.status,
            MedicationAdministrationRow.id,
        ).where(
            MedicationAdministrationRow.patient_id == patient_id,
            MedicationAdministrationRow.scheduled_at >= begin,
            MedicationAdministrationRow.scheduled_at < end,
        )
    )
    return {(s, at): (status, i) for s, at, status, i in rows.all()}


def dose_id(statement_id: UUID, scheduled_at: datetime) -> str:
    raw = f"{statement_id}|{scheduled_at.isoformat()}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def parse_dose_id(value: str) -> tuple[ResourceId, datetime]:
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)).decode()
        statement, at = raw.split("|", 1)
        return ResourceId(UUID(statement)), datetime.fromisoformat(at)
    except (ValueError, UnicodeDecodeError):
        raise NotFound() from None


def _rule(row: AlertRuleRow) -> AlertRule:
    return AlertRule(
        id=ResourceId(row.id),
        patient_id=PatientId(row.patient_id),
        rule_type=row.rule_type,
        params=row.params,
        version=row.version,
        active=row.active,
    )
