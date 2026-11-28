"""Critical tier: consent on Postgres with the real identity service (SEC-10 to SEC-13, CUS-3).
Grants drive the policy, revocation works on the next request, care relationships drive doctors,
agents get the intersection, and only the patient manages grants."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select

from suwapath.contracts.enums import AgentKind, DataCategory, Language, PrincipalKind, Purpose, Role
from suwapath.contracts.errors import NotFound, ValidationFailed
from suwapath.contracts.ids import AppointmentId, PatientId, TraceId
from suwapath.contracts.ports.consent import AccessRequest
from suwapath.contracts.ports.identity import UserAccount
from suwapath.contracts.principal import Principal
from suwapath.modules.consent.api.router import build_router
from suwapath.modules.consent.application.service import SqlAccessPolicy, SqlConsentService
from suwapath.modules.identity.application.service import SqlIdentityService
from suwapath.platform.clock import FixedClock
from suwapath.platform.config import Settings
from suwapath.platform.http.app import create_app
from suwapath.platform.models import OutboxRow
from suwapath.platform.uow import UnitOfWorkFactory

pytestmark = pytest.mark.integration
C = DataCategory
TRACE = TraceId("8" * 32)


class Family:
    async def setup(self, uow_factory: UnitOfWorkFactory) -> Family:
        self.identity = SqlIdentityService(uow_factory, "s" * 40)
        self.patient = PatientId(uuid4())
        self.amma = await self._user("amma@example.test", Role.PATIENT, patient_id=self.patient)
        self.son = await self._user("son@example.test", Role.GUARDIAN)
        self.doctor = await self._user("dr@example.test", Role.DOCTOR)
        self.scopes: dict[str, frozenset[DataCategory]] = {
            AgentKind.GUARDIAN_AGENT.value: frozenset({C.ADHERENCE, C.MEDICATIONS, C.APPOINTMENTS})
        }
        self.policy = SqlAccessPolicy(uow_factory, lambda: self.identity, lambda: self.scopes)
        self.consent = SqlConsentService(uow_factory, lambda: self.identity)
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

    async def may(self, principal: Principal, category: DataCategory, action: str = "read") -> bool:
        decision = await self.policy.decide(
            AccessRequest(
                principal=principal,
                action=action,  # type: ignore[arg-type]
                patient_id=self.patient,
                category=category,
                resource_type="Test",
                purpose=principal.purpose,
            )
        )
        return decision.allowed

    async def grant_son(self, *categories: DataCategory, emergency: bool = True) -> None:
        await self.consent.set_grant(
            self.amma_view,
            self.patient,
            self.son.id,
            frozenset(categories),
            "k",
            emergency_alert=emergency,
        )


@pytest.fixture
async def family(uow_factory: UnitOfWorkFactory) -> Family:
    return await Family().setup(uow_factory)


# ----- grants drive the policy, and revocation is immediate --------------------------------------


async def test_a_guardian_sees_exactly_what_was_granted(family: Family) -> None:
    son = family.as_user(family.son, Purpose.GUARDIAN_MONITORING)
    assert not await family.may(son, C.MEDICATIONS)  # deny by default
    await family.grant_son(C.MEDICATIONS, C.ADHERENCE)
    assert await family.may(son, C.MEDICATIONS)
    assert not await family.may(son, C.CONDITIONS)
    assert not await family.may(son, C.MEDICATIONS, "write")


async def test_revocation_takes_effect_on_the_next_request(family: Family) -> None:
    son = family.as_user(family.son, Purpose.GUARDIAN_MONITORING)
    await family.grant_son(C.ADHERENCE)
    assert await family.may(son, C.ADHERENCE)
    await family.consent.revoke_grant(family.amma_view, family.patient, family.son.id)
    assert not await family.may(son, C.ADHERENCE)
    await family.consent.revoke_grant(family.amma_view, family.patient, family.son.id)  # idempotent


async def test_narrowing_applies_at_once_and_is_announced(
    family: Family, uow_factory: UnitOfWorkFactory
) -> None:
    son = family.as_user(family.son, Purpose.GUARDIAN_MONITORING)
    await family.grant_son(C.ADHERENCE, C.MEDICATIONS)
    await family.grant_son(C.ADHERENCE, C.MEDICATIONS)  # the same again changes nothing
    await family.grant_son(C.ADHERENCE)
    assert not await family.may(son, C.MEDICATIONS)
    async with uow_factory() as uow:
        changes = [
            r["change"] for r in (await uow.session.execute(select(OutboxRow.payload))).scalars()
        ]
    assert changes == ["granted", "narrowed"]


async def test_sensitive_categories_cannot_be_granted_to_family(family: Family) -> None:
    with pytest.raises(ValidationFailed):
        await family.grant_son(C.SENSITIVE_MENTAL_HEALTH)


async def test_the_emergency_alert_follows_the_patients_switch(family: Family) -> None:
    son = family.as_user(family.son, Purpose.EMERGENCY)
    await family.grant_son(C.ADHERENCE)
    assert await family.may(son, C.DEMOGRAPHICS)  # on by default (rule 7)
    await family.grant_son(C.ADHERENCE, emergency=False)
    assert not await family.may(son, C.DEMOGRAPHICS)


async def test_only_the_patient_manages_grants(family: Family) -> None:
    son = family.as_user(family.son, Purpose.GUARDIAN_MONITORING)
    with pytest.raises(NotFound):
        await family.consent.set_grant(
            son, family.patient, family.son.id, frozenset({C.CONDITIONS}), "k"
        )
    with pytest.raises(NotFound):
        await family.consent.grants_for_patient(son, family.patient)
    stranger = family.as_user(
        await family._user("x@example.test", Role.PATIENT, patient_id=PatientId(uuid4())),
        Purpose.SELF_ACCESS,
    )
    with pytest.raises(NotFound):
        await family.consent.revoke_grant(stranger, family.patient, family.son.id)


# ----- doctors: care relationships (rule 4) ------------------------------------------------------


async def test_a_doctor_reads_from_booking_until_thirty_days_after(
    family: Family, clock: FixedClock
) -> None:
    doctor = family.as_user(family.doctor, Purpose.TREATMENT)
    assert not await family.may(doctor, C.CONDITIONS)
    appointment = AppointmentId(uuid4())
    await family.consent.record_treatment(
        patient_id=family.patient,
        doctor_id=family.doctor.id,
        appointment_id=appointment,
        booked_at=clock.now(),
        appointment_at=clock.now() + timedelta(days=2),
    )
    assert await family.may(doctor, C.CONDITIONS)
    assert not await family.may(doctor, C.SENSITIVE_SEXUAL_HEALTH)  # hidden by default
    clock.advance(timedelta(days=32, minutes=1))
    assert not await family.may(doctor, C.CONDITIONS)


async def test_a_cancelled_appointment_ends_access_now(family: Family, clock: FixedClock) -> None:
    doctor = family.as_user(family.doctor, Purpose.TREATMENT)
    appointment = AppointmentId(uuid4())
    await family.consent.record_treatment(
        patient_id=family.patient,
        doctor_id=family.doctor.id,
        appointment_id=appointment,
        booked_at=clock.now(),
        appointment_at=clock.now() + timedelta(days=1),
    )
    await family.consent.end_treatment(appointment)
    assert not await family.may(doctor, C.CONDITIONS)


async def test_the_patient_can_show_a_sensitive_category_to_one_doctor(
    family: Family, clock: FixedClock
) -> None:
    doctor = family.as_user(family.doctor, Purpose.TREATMENT)
    await family.consent.record_treatment(
        patient_id=family.patient,
        doctor_id=family.doctor.id,
        appointment_id=AppointmentId(uuid4()),
        booked_at=clock.now(),
        appointment_at=clock.now() + timedelta(days=1),
    )
    await family.consent.set_grant(
        family.amma_view,
        family.patient,
        family.doctor.id,
        frozenset({C.SENSITIVE_SEXUAL_HEALTH}),
        "k",
    )
    assert await family.may(doctor, C.SENSITIVE_SEXUAL_HEALTH)
    assert not await family.may(doctor, C.SENSITIVE_MENTAL_HEALTH)


# ----- agents: the intersection (rule 5) and the patient's own record (rule 2) -------------------


async def test_the_guardian_agent_gets_its_humans_grant_within_its_scope(family: Family) -> None:
    await family.grant_son(C.ADHERENCE, C.CONDITIONS)
    agent = Principal(
        kind=PrincipalKind.AGENT,
        agent_kind=AgentKind.GUARDIAN_AGENT,
        acting_for=family.son.id,
        patient_scope=family.patient,
        purpose=Purpose.GUARDIAN_MONITORING,
        trace_id=TRACE,
    )
    assert await family.may(agent, C.ADHERENCE)
    assert not await family.may(
        agent, C.CONDITIONS
    )  # granted to the son, outside the agent's scope
    assert not await family.may(agent, C.MEDICATIONS)  # in scope, not granted


async def test_a_patient_reads_their_record_and_a_forged_scope_does_not_help(
    family: Family,
) -> None:
    assert await family.may(family.amma_view, C.CONDITIONS)
    other = await family._user("y@example.test", Role.PATIENT, patient_id=PatientId(uuid4()))
    forged = family.as_user(other, Purpose.SELF_ACCESS).model_copy(
        update={"patient_scope": family.patient}
    )
    assert not await family.may(forged, C.CONDITIONS)  # the account, not the principal, decides


# ----- the routes --------------------------------------------------------------------------------


async def test_the_patient_manages_grants_through_the_api(family: Family) -> None:
    tokens = await family.identity.authenticate("amma@example.test", "long enough", "10.0.0.1")
    app = create_app(
        Settings(_env_file=None),
        token_verifier=family.identity.verify_access_token,
        routers=[build_router(family.consent)],
    )
    headers = {"Authorization": f"Bearer {tokens.access_token}"}
    base = f"/api/v1/patients/{family.patient}/consents"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        put = await client.put(
            f"{base}/{family.son.id}", json={"categories": ["adherence"]}, headers=headers
        )
        assert put.status_code == 200 and put.json()["categories"] == ["adherence"]
        assert [g["grantee_id"] for g in (await client.get(base, headers=headers)).json()] == [
            str(family.son.id)
        ]
        bad = await client.put(
            f"{base}/{family.son.id}",
            json={"categories": ["sensitive_sexual_health"]},
            headers=headers,
        )
        assert bad.status_code == 422
        assert (await client.delete(f"{base}/{family.son.id}", headers=headers)).status_code == 204
        assert (await client.get(base, headers=headers)).json() == []
        other = await client.get(f"/api/v1/patients/{uuid4()}/consents", headers=headers)
        assert other.status_code == 404
    assert await family.consent.guardians_of(family.patient) == []
