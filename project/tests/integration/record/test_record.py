"""Critical tier: the record on Postgres with the real identity, consent policy and cipher
(SEC-11, CON-7 to CON-11, SEC-22). Reads go through the policy, IDs cannot be probed, everything
is sealed, every fact has a source, and the patient can export and erase."""

from __future__ import annotations

import base64
import os
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import select

from suwapath.contracts.enums import (
    AgentKind,
    DataCategory,
    DoseSlot,
    Language,
    PrincipalKind,
    Purpose,
    Role,
)
from suwapath.contracts.errors import AccessDenied, NotFound, VersionConflict
from suwapath.contracts.fhir.base import CodeableConcept, HumanName, Meta, Quantity
from suwapath.contracts.fhir.resources import (
    AllergyIntolerance,
    Condition,
    Dosage,
    DoseTiming,
    MedicationStatement,
    Observation,
    Patient,
)
from suwapath.contracts.ids import AppointmentId, PatientId, ResourceId, TraceId
from suwapath.contracts.ports.identity import UserAccount
from suwapath.contracts.principal import Principal
from suwapath.modules.consent.application.service import SqlAccessPolicy, SqlConsentService
from suwapath.modules.identity.application.service import SqlIdentityService
from suwapath.modules.record.adapters.models import ConditionRow, ProvenanceRow
from suwapath.modules.record.application.keys import SqlPatientCipher
from suwapath.modules.record.application.service import SqlRecordService
from suwapath.platform.clock import FixedClock
from suwapath.platform.crypto import DekCache, LocalKeyProvider
from suwapath.platform.models import OutboxRow
from suwapath.platform.uow import UnitOfWorkFactory

pytestmark = pytest.mark.integration
TRACE = TraceId("9" * 32)
NOW = datetime(2026, 11, 28, 9, 0, tzinfo=UTC)


def meta() -> Meta:
    return Meta(last_updated=NOW, source="seed")


class Clinic:
    async def setup(self, uow_factory: UnitOfWorkFactory, clock: FixedClock) -> Clinic:
        self.clock = clock
        self.identity = SqlIdentityService(uow_factory, "s" * 40)
        self.patient = PatientId(uuid4())
        self.amma = await self._user("amma@example.test", Role.PATIENT, patient_id=self.patient)
        self.son = await self._user("son@example.test", Role.GUARDIAN)
        self.doctor = await self._user("dr@example.test", Role.DOCTOR)
        self.stranger = await self._user(
            "x@example.test", Role.PATIENT, patient_id=PatientId(uuid4())
        )
        policy = SqlAccessPolicy(
            uow_factory,
            lambda: self.identity,
            lambda: {
                AgentKind.DOCTOR_ASSISTANT.value: frozenset(DataCategory)
                - {DataCategory.SENSITIVE_SEXUAL_HEALTH}
            },
        )
        self.consent = SqlConsentService(uow_factory, lambda: self.identity)
        provider = LocalKeyProvider(base64.b64encode(os.urandom(32)).decode())
        self.cipher = SqlPatientCipher(uow_factory, lambda: provider, DekCache())
        self.record = SqlRecordService(uow_factory, lambda: policy, lambda: self.cipher)
        self.uow_factory = uow_factory
        await self._seed()
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

    async def _seed(self) -> None:
        p = self.patient
        self.condition = Condition(
            id=ResourceId(uuid4()),
            meta=meta(),
            patient_id=p,
            code=CodeableConcept(text="Type 2 diabetes"),
            clinical_status="active",
        )
        self.medication = MedicationStatement(
            id=ResourceId(uuid4()),
            meta=meta(),
            patient_id=p,
            medication=CodeableConcept(text="Metformin"),
            dosage=Dosage(
                text="500 mg", timing=DoseTiming(slots=[DoseSlot.MORNING, DoseSlot.EVENING])
            ),
            status="active",
        )
        self.allergy = AllergyIntolerance(
            id=ResourceId(uuid4()),
            meta=meta(),
            patient_id=p,
            code=CodeableConcept(text="Penicillin"),
            criticality="high",
        )
        self.hba1c = Observation(
            id=ResourceId(uuid4()),
            meta=meta(),
            patient_id=p,
            code=CodeableConcept(text="HbA1c"),
            value=Quantity(value=8.1, unit="%"),
            effective_at=NOW - timedelta(days=20),
        )
        demographics = Patient(
            id=ResourceId(p),
            meta=meta(),
            patient_id=p,
            user_id=self.amma.id,
            name=HumanName(given=["Kamala"], family="Perera"),
            birth_date=date(1954, 3, 2),
            age_band="60-74",
            gender="female",
            language=Language.SI,
            nic="545620123V",
        )
        written = await self.record.import_resources(
            [demographics, self.condition, self.medication, self.allergy, self.hba1c], agent="seed"
        )
        assert written == 5

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

    async def treat(self) -> None:
        await self.consent.record_treatment(
            patient_id=self.patient,
            doctor_id=self.doctor.id,
            appointment_id=AppointmentId(uuid4()),
            booked_at=self.clock.now(),
            appointment_at=self.clock.now() + timedelta(days=1),
        )


@pytest.fixture
async def clinic(uow_factory: UnitOfWorkFactory, clock: FixedClock) -> Clinic:
    return await Clinic().setup(uow_factory, clock)


# ----- reads through the policy ------------------------------------------------------------------


async def test_the_patient_sees_their_whole_record(clinic: Clinic) -> None:
    record = await clinic.record.summary(
        clinic.as_user(clinic.amma, Purpose.SELF_ACCESS), clinic.patient, Purpose.SELF_ACCESS
    )
    assert record.patient is not None and record.patient.name.given == ["Kamala"]
    assert [c.code.text for c in record.conditions] == ["Type 2 diabetes"]
    assert record.allergies[0].criticality == "high"
    assert record.hidden_categories_count == 0


async def test_a_guardian_sees_only_the_shared_categories(clinic: Clinic) -> None:
    amma = clinic.as_user(clinic.amma, Purpose.SELF_ACCESS)
    await clinic.consent.set_grant(
        amma, clinic.patient, clinic.son.id, frozenset({DataCategory.MEDICATIONS}), "k"
    )
    record = await clinic.record.summary(
        clinic.as_user(clinic.son, Purpose.GUARDIAN_MONITORING),
        clinic.patient,
        Purpose.GUARDIAN_MONITORING,
    )
    assert [m.medication.text for m in record.medications] == ["Metformin"]
    assert record.conditions == () and record.allergies == () and record.patient is None
    assert record.hidden_categories_count == 4  # demographics, conditions, allergies, observations


async def test_a_doctor_sees_the_record_only_while_treating(clinic: Clinic) -> None:
    doctor = clinic.as_user(clinic.doctor, Purpose.TREATMENT)
    with pytest.raises(NotFound):
        await clinic.record.summary(doctor, clinic.patient, Purpose.TREATMENT)
    await clinic.treat()
    record = await clinic.record.summary(doctor, clinic.patient, Purpose.TREATMENT)
    assert [o.code.text for o in record.recent_observations] == ["HbA1c"]


async def test_strangers_learn_nothing_not_even_that_the_patient_exists(clinic: Clinic) -> None:
    stranger = clinic.as_user(clinic.stranger, Purpose.SELF_ACCESS)
    with pytest.raises(NotFound):
        await clinic.record.summary(stranger, clinic.patient, Purpose.SELF_ACCESS)
    with pytest.raises(NotFound):  # IDOR: a guessed resource id looks like nothing
        await clinic.record.get_resource(stranger, "Condition", clinic.condition.id)
    with pytest.raises(NotFound):
        await clinic.record.get_resource(stranger, "Condition", ResourceId(uuid4()))
    with pytest.raises(NotFound):
        await clinic.record.list_resources(stranger, clinic.patient, "Condition", None, 10)


async def test_listing_pages_through_one_type(clinic: Clinic) -> None:
    amma = clinic.as_user(clinic.amma, Purpose.SELF_ACCESS)
    page = await clinic.record.list_resources(amma, clinic.patient, "Condition", None, 10)
    assert [r.id for r in page.items] == [clinic.condition.id] and page.next_cursor is None


# ----- storage, sources and writes ---------------------------------------------------------------


async def test_everything_is_sealed_at_rest(clinic: Clinic) -> None:
    async with clinic.uow_factory() as uow:
        sealed = (await uow.session.execute(select(ConditionRow.data_enc))).scalar_one()
        status = (await uow.session.execute(select(ConditionRow.clinical_status))).scalar_one()
    assert b"diabetes" not in sealed.lower() and status == "active"  # only query columns are plain


async def test_every_imported_fact_has_a_source_the_doctor_can_open(clinic: Clinic) -> None:
    await clinic.treat()
    doctor = clinic.as_user(clinic.doctor, Purpose.TREATMENT)
    condition = await clinic.record.get_resource(doctor, "Condition", clinic.condition.id)
    assert condition.provenance_ids
    source = await clinic.record.get_provenance(doctor, condition.provenance_ids[0])
    assert source.activity == "imported" and source.agent == "seed"
    assert source.targets[0].id == clinic.condition.id
    son = clinic.as_user(clinic.son, Purpose.GUARDIAN_MONITORING)
    with pytest.raises(NotFound):  # the source of something he may not see
        await clinic.record.get_provenance(son, condition.provenance_ids[0])


async def test_a_doctors_write_gets_a_source_a_version_and_an_event(clinic: Clinic) -> None:
    await clinic.treat()
    doctor = clinic.as_user(clinic.doctor, Purpose.TREATMENT)
    new = Condition(
        id=ResourceId(uuid4()),
        meta=meta(),
        patient_id=clinic.patient,
        code=CodeableConcept(text="Hypertension"),
        clinical_status="active",
    )
    stored = await clinic.record.write_resource(doctor, new)
    assert stored.meta.version_id == 1 and stored.provenance_ids
    entered = await clinic.record.get_provenance(doctor, stored.provenance_ids[0])
    assert entered.activity == "entered" and entered.agent == f"user:{clinic.doctor.id}"
    updated = await clinic.record.write_resource(
        doctor, stored.model_copy(update={"clinical_status": "remission"})
    )
    assert updated.meta.version_id == 2
    with pytest.raises(VersionConflict):  # a stale copy cannot overwrite the newer one
        await clinic.record.write_resource(doctor, stored)
    async with clinic.uow_factory() as uow:
        events = (
            await uow.session.execute(
                select(OutboxRow.type).where(OutboxRow.aggregate_id == str(new.id))
            )
        ).scalars()
        assert list(events) == ["record.resource_written"] * 2  # one per write of this resource


async def test_a_patient_cannot_write_clinical_facts(clinic: Clinic) -> None:
    amma = clinic.as_user(clinic.amma, Purpose.SELF_ACCESS)
    with pytest.raises(AccessDenied):
        await clinic.record.write_resource(
            amma, clinic.condition.model_copy(update={"clinical_status": "resolved"})
        )


# ----- export and erasure ------------------------------------------------------------------------


async def test_the_patient_exports_a_fhir_bundle(clinic: Clinic) -> None:
    bundle = await clinic.record.export_fhir(
        clinic.as_user(clinic.amma, Purpose.SELF_ACCESS), clinic.patient
    )
    assert bundle["resourceType"] == "Bundle" and bundle["type"] == "collection"
    types = sorted(e["resource"]["resourceType"] for e in bundle["entry"])
    assert types == [
        "AllergyIntolerance",
        "Condition",
        "MedicationStatement",
        "Observation",
        "Patient",
    ]
    condition = next(
        e["resource"] for e in bundle["entry"] if e["resource"]["resourceType"] == "Condition"
    )
    assert condition["subject"] == {"reference": f"Patient/{clinic.patient}"}
    await clinic.treat()
    with pytest.raises(NotFound):  # only the patient exports
        await clinic.record.export_fhir(
            clinic.as_user(clinic.doctor, Purpose.TREATMENT), clinic.patient
        )


async def test_erasure_makes_the_record_unreadable(clinic: Clinic) -> None:
    amma = clinic.as_user(clinic.amma, Purpose.SELF_ACCESS)
    with pytest.raises(NotFound):  # only for oneself
        await clinic.record.destroy_patient_key(
            clinic.as_user(clinic.stranger, Purpose.SELF_ACCESS), clinic.patient
        )
    await clinic.record.destroy_patient_key(amma, clinic.patient)
    with pytest.raises(NotFound):
        await clinic.record.summary(amma, clinic.patient, Purpose.SELF_ACCESS)
    async with clinic.uow_factory() as uow:
        assert (
            await uow.session.execute(select(ProvenanceRow.id))
        ).first() is not None  # rows stay; unreadable
