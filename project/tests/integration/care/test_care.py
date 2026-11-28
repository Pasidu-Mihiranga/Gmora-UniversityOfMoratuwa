"""Care on Postgres with the real record, consent, identity, timers and bus (CARE-2 to CARE-8).
Rolling dose timers, taken and missed, adherence, and guardian rules that respect grants."""

from __future__ import annotations

import base64
import os
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from suwapath.contracts.enums import (
    AgentKind,
    DataCategory,
    DoseSlot,
    Language,
    PrincipalKind,
    Purpose,
    Role,
)
from suwapath.contracts.errors import NotFound
from suwapath.contracts.fhir.base import CodeableConcept, Meta
from suwapath.contracts.fhir.resources import Dosage, DoseTiming, MedicationStatement
from suwapath.contracts.ids import PatientId, ResourceId, TraceId
from suwapath.contracts.ports.identity import UserAccount
from suwapath.contracts.principal import Principal
from suwapath.modules.care.application import doses
from suwapath.modules.care.application.service import RuleRequest, SqlCareService, parse_dose_id
from suwapath.modules.consent.application.service import SqlAccessPolicy, SqlConsentService
from suwapath.modules.identity.application.service import SqlIdentityService
from suwapath.modules.record.application.keys import SqlPatientCipher
from suwapath.modules.record.application.service import SqlRecordService
from suwapath.platform.clock import FixedClock
from suwapath.platform.crypto import DekCache, LocalKeyProvider
from suwapath.platform.events.dispatcher import Dispatcher
from suwapath.platform.events.subscribers import SubscriberRegistry
from suwapath.platform.jobs.timers import fire_due_timers
from suwapath.platform.models import OutboxRow, TimerRow
from suwapath.platform.uow import UnitOfWorkFactory

pytestmark = pytest.mark.integration
TRACE = TraceId("a" * 32)
# The fixture clock is 2026-11-28 13:30 UTC = 19:00 in Colombo, exactly the evening dose.


class Home:
    async def setup(self, uow_factory: UnitOfWorkFactory, clock: FixedClock) -> Home:
        self.uow_factory, self.clock = uow_factory, clock
        self.identity = SqlIdentityService(uow_factory, "s" * 40)
        self.patient = PatientId(uuid4())
        self.amma = await self._user("amma@example.test", Role.PATIENT, patient_id=self.patient)
        self.son = await self._user("son@example.test", Role.GUARDIAN)
        policy = SqlAccessPolicy(
            uow_factory,
            lambda: self.identity,
            lambda: {AgentKind.GUARDIAN_AGENT.value: frozenset({DataCategory.ADHERENCE})},
        )
        self.consent = SqlConsentService(uow_factory, lambda: self.identity)
        provider = LocalKeyProvider(base64.b64encode(os.urandom(32)).decode())
        cipher = SqlPatientCipher(uow_factory, lambda: provider, DekCache())
        self.record = SqlRecordService(uow_factory, lambda: policy, lambda: cipher)
        self.care = SqlCareService(uow_factory, lambda: policy, lambda: self.record)
        subscribers = SubscriberRegistry()
        subscribers.add("platform.timer_fired", doses.on_timer, name="care.dose_check")
        subscribers.add("platform.timer_fired", self.care.on_timer, name="care.dose_window")
        subscribers.add(
            "record.resource_written", self.care.on_resource_written, name="care.schedule_doses"
        )
        subscribers.add("care.dose_missed", self.care.on_dose_missed, name="care.alert_rules")
        self.dispatcher = Dispatcher(uow_factory, subscribers)
        self.metformin = ResourceId(uuid4())
        await self.record.import_resources(
            [
                MedicationStatement(
                    id=self.metformin,
                    meta=Meta(last_updated=clock.now(), source="seed"),
                    patient_id=self.patient,
                    medication=CodeableConcept(text="Metformin"),
                    dosage=Dosage(
                        text="500 mg", timing=DoseTiming(slots=[DoseSlot.MORNING, DoseSlot.EVENING])
                    ),
                    status="active",
                )
            ],
            agent="seed",
        )
        await self.pump()
        return self

    async def _user(self, login: str, role: Role, **extra: Any) -> UserAccount:
        return await self.identity.create_user(
            login=login,
            password="long enough",
            role=role,
            display_name=login,
            language=Language.SI,
            **extra,
        )

    def as_user(self, account: UserAccount, purpose: Purpose) -> Principal:
        return Principal(
            kind=PrincipalKind.USER,
            user_id=account.id,
            role=account.role,
            acting_for=account.id,
            patient_scope=account.patient_id,
            purpose=purpose,
            trace_id=TRACE,
        )

    @property
    def amma_view(self) -> Principal:
        return self.as_user(self.amma, Purpose.SELF_ACCESS)

    @property
    def son_view(self) -> Principal:
        return self.as_user(self.son, Purpose.GUARDIAN_MONITORING)

    async def pump(self) -> None:
        for _ in range(20):
            if not (await fire_due_timers(self.uow_factory) + await self.dispatcher.drain()):
                return
        raise AssertionError("did not settle")

    async def later(self, delta: timedelta) -> None:
        self.clock.advance(delta)
        await self.pump()

    async def count(self, model: Any, *where: Any) -> int:
        async with self.uow_factory() as uow:
            return int(
                (
                    await uow.session.execute(select(func.count()).select_from(model).where(*where))
                ).scalar_one()
            )

    async def events(self, kind: str) -> list[dict[str, Any]]:
        async with self.uow_factory() as uow:
            return list(
                (
                    await uow.session.execute(
                        select(OutboxRow.payload).where(OutboxRow.type == kind)
                    )
                ).scalars()
            )

    async def share(self, *categories: DataCategory) -> None:
        await self.consent.set_grant(
            self.amma_view, self.patient, self.son.id, frozenset(categories), "k"
        )


@pytest.fixture
async def home(uow_factory: UnitOfWorkFactory, clock: FixedClock) -> Home:
    return await Home().setup(uow_factory, clock)


# ----- the rolling window (CARE-3) ---------------------------------------------------------------


async def test_a_new_medicine_gets_dose_checks_for_48_hours(home: Home) -> None:
    # From 19:00 Colombo: evening today, morning and evening tomorrow, morning the day after.
    assert await home.count(TimerRow, TimerRow.kind == "dose_check") == 4
    assert await home.count(TimerRow, TimerRow.kind == "dose_window") == 1


async def test_the_daily_timer_rolls_the_window_without_duplicates(home: Home) -> None:
    await home.later(timedelta(days=1, minutes=1))
    # The window now ends at 19:01 on day three: three new doses, and none scheduled twice.
    assert await home.count(TimerRow, TimerRow.kind == "dose_check") == 7
    assert await home.count(TimerRow, TimerRow.kind == "dose_window") == 2


# ----- taken and missed (CARE-4, CARE-5) ---------------------------------------------------------


async def test_a_dose_taken_in_time_is_never_missed(home: Home) -> None:
    today = await home.care.doses_for_day(home.amma_view, home.patient, home.care.today())
    evening = next(d for d in today if d.slot is DoseSlot.EVENING)
    assert (evening.status, evening.medication_display) == ("pending", "Metformin")
    statement, at = parse_dose_id(evening.dose_id)
    taken = await home.care.mark_taken(home.amma_view, home.patient, statement, at, "k1")
    await home.care.mark_taken(home.amma_view, home.patient, statement, at, "k1")  # a second tap
    assert taken.status == "taken"
    assert len(await home.events("care.dose_taken")) == 1
    await home.later(timedelta(hours=2))  # past the grace: the cancelled timer does nothing
    assert await home.events("care.dose_missed") == []


async def test_a_dose_not_taken_is_recorded_missed_and_can_be_taken_late(home: Home) -> None:
    await home.later(timedelta(minutes=61))
    missed = await home.events("care.dose_missed")
    assert len(missed) == 1
    evening = next(
        d
        for d in await home.care.doses_for_day(home.amma_view, home.patient, home.care.today())
        if d.slot is DoseSlot.EVENING
    )
    assert evening.status == "missed"
    statement, at = parse_dose_id(evening.dose_id)
    assert (
        await home.care.mark_taken(home.amma_view, home.patient, statement, at, "late")
    ).status == "taken"


async def test_a_made_up_dose_is_not_found(home: Home) -> None:
    with pytest.raises(NotFound):
        await home.care.mark_taken(
            home.amma_view,
            home.patient,
            home.metformin,
            datetime(2026, 11, 28, 4, 0, tzinfo=UTC),
            "x",
        )


# ----- adherence and who sees it (CARE-6) --------------------------------------------------------


async def test_the_week_counts_taken_missed_and_pending(home: Home) -> None:
    await home.later(timedelta(minutes=61))  # this evening missed
    await home.later(timedelta(hours=13))  # tomorrow's 08:00 is missed once its grace ends at 09:00
    summary = await home.care.adherence(home.amma_view, home.patient, 2)
    yesterday, today = summary.days
    assert (yesterday.missed, yesterday.missed_slots) == (1, (DoseSlot.EVENING,))
    assert (today.missed, today.pending) == (1, 0)
    assert summary.missed_by_slot == {DoseSlot.EVENING: 1, DoseSlot.MORNING: 1}


async def test_family_sees_doses_only_when_shared_and_names_only_with_medicines(home: Home) -> None:
    with pytest.raises(NotFound):
        await home.care.doses_for_day(home.son_view, home.patient, home.care.today())
    await home.share(DataCategory.ADHERENCE)
    without_names = await home.care.doses_for_day(home.son_view, home.patient, home.care.today())
    assert without_names and all(d.medication_display == "" for d in without_names)
    await home.share(DataCategory.ADHERENCE, DataCategory.MEDICATIONS)
    with_names = await home.care.doses_for_day(home.son_view, home.patient, home.care.today())
    assert {d.medication_display for d in with_names} == {"Metformin"}


# ----- guardian alert rules (CARE-7, CARE-8) -----------------------------------------------------


async def test_a_guardian_rule_needs_the_adherence_grant(home: Home) -> None:
    request = RuleRequest(
        patient_id=home.patient, rule_type="missed_doses", params={"count": 2, "window_hours": 48}
    )
    with pytest.raises(NotFound):
        await home.care.create_rule(home.son_view, request)
    await home.share(DataCategory.ADHERENCE)
    rule = await home.care.create_rule(home.son_view, request)
    again = await home.care.create_rule(
        home.son_view, request.model_copy(update={"params": {"count": 3, "window_hours": 48}})
    )
    assert (rule.version, again.version, again.params["count"]) == (1, 2, 3)  # versioned, one rule


async def test_the_rule_fires_once_per_window_and_triggers_the_guardian_agent(home: Home) -> None:
    await home.share(DataCategory.ADHERENCE)
    await home.care.create_rule(
        home.son_view,
        RuleRequest(
            patient_id=home.patient,
            rule_type="missed_doses",
            params={"count": 2, "window_hours": 48},
        ),
    )
    await home.later(timedelta(minutes=61))  # one missed: not yet
    assert await home.events("care.alert_rule_fired") == []
    await home.later(timedelta(hours=13))  # two missed (after the morning grace): fires
    fired = await home.events("care.alert_rule_fired")
    assert [(f["rule_type"], f["guardian_id"]) for f in fired] == [
        ("missed_doses", str(home.son.id))
    ]
    await home.later(timedelta(hours=12))  # a third miss within the window: no second alert
    assert len(await home.events("care.alert_rule_fired")) == 1


async def test_a_revoked_grant_silences_the_rule(home: Home) -> None:
    await home.share(DataCategory.ADHERENCE)
    await home.care.create_rule(
        home.son_view,
        RuleRequest(
            patient_id=home.patient,
            rule_type="missed_doses",
            params={"count": 1, "window_hours": 24},
        ),
    )
    await home.consent.revoke_grant(home.amma_view, home.patient, home.son.id)
    await home.later(timedelta(minutes=61))
    assert await home.events("care.alert_rule_fired") == []
