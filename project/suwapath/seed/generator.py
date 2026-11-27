"""Deterministic synthetic world (SEED-1, SEED-2): the same seed always produces the same bytes.

Everything is built from contract types, so the output is valid against the contracts by
construction. IDs come from the seeded random generator (not UUIDv7) to keep runs identical.
Dose history is stored as an adherence pattern per patient and expanded by `expand_doses`.
"""

from __future__ import annotations

import random
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal
from uuid import UUID

from pydantic import Field

from suwapath.contracts.common import Contract
from suwapath.contracts.enums import DataCategory, DoseSlot, Language, Role
from suwapath.contracts.fhir.base import (
    Address,
    CodeableConcept,
    Coding,
    ContactPoint,
    GeoPoint,
    HumanName,
    Meta,
    Period,
    Quantity,
    Reference,
    age_band,
)
from suwapath.contracts.fhir.resources import (
    AllergyIntolerance,
    Condition,
    Consent,
    Dosage,
    DoseTiming,
    MedicationStatement,
    Observation,
    Organization,
    Patient,
    Practitioner,
    PractitionerRole,
    Provenance,
    ProvenanceEntity,
    ReferenceRange,
    RelatedPerson,
    Schedule,
)
from suwapath.contracts.ids import (
    DocumentId,
    OrganizationId,
    PatientId,
    PractitionerId,
    PractitionerRoleId,
    ResourceId,
    UserId,
)
from suwapath.seed import catalog

ANCHOR = date(2026, 11, 30)  # the demo's "today"; schedules start here
SEED_SOURCE = "seed"


class SeedUser(Contract):
    id: UserId
    role: Role
    login: str  # demo password comes from DEMO_PASSWORD at load time, never stored here
    display_name: str
    language: Language
    patient_id: PatientId | None = None
    practitioner_id: PractitionerId | None = None
    organization_id: OrganizationId | None = None


class AdherencePlan(Contract):
    patient_id: PatientId
    pattern: Literal["good", "slipping", "evening_misses"]


class LabRow(Contract):
    observation_id: ResourceId
    test: str
    value: float
    unit: str
    low: float
    high: float


class LabReport(Contract):
    document_id: DocumentId
    patient_id: PatientId
    issued_at: datetime
    lab_name: str
    rows: tuple[LabRow, ...]


class BriefLabel(Contract):
    """Ground truth for the briefs suite (SEED-2)."""

    patient_id: PatientId
    required_fact_ids: tuple[ResourceId, ...]
    planted_clashes: tuple[str, ...] = ()


class World(Contract):
    seed: int
    anchor_date: date
    users: tuple[SeedUser, ...]
    organizations: tuple[Organization, ...]
    practitioners: tuple[Practitioner, ...]
    practitioner_roles: tuple[PractitionerRole, ...]
    schedules: tuple[Schedule, ...]
    patients: tuple[Patient, ...]
    related_persons: tuple[RelatedPerson, ...]
    consents: tuple[Consent, ...]
    conditions: tuple[Condition, ...]
    allergies: tuple[AllergyIntolerance, ...]
    medications: tuple[MedicationStatement, ...]
    observations: tuple[Observation, ...]
    provenance: tuple[Provenance, ...]
    lab_reports: tuple[LabReport, ...]
    adherence: tuple[AdherencePlan, ...]
    brief_labels: tuple[BriefLabel, ...]
    xray_studies: tuple[str, ...] = Field(
        default=(), description="public-domain images are added at load time"
    )


class _Gen:
    def __init__(self, seed: int) -> None:
        self.rng = random.Random(seed)  # noqa: S311 (reproducible synthetic data, not security)
        self.now = datetime.combine(ANCHOR, time(8, 0), tzinfo=UTC)

    def uuid(self) -> UUID:
        return UUID(int=self.rng.getrandbits(128), version=4)

    def meta(self) -> Meta:
        return Meta(last_updated=self.now, source=SEED_SOURCE)

    def phone(self) -> str:
        return "+9477" + "".join(str(self.rng.randint(0, 9)) for _ in range(7))


def generate(seed: int = 42) -> World:
    g = _Gen(seed)
    users: list[SeedUser] = []

    organizations = [
        Organization(
            id=ResourceId(g.uuid()),
            meta=g.meta(),
            name=name,
            district=district,
            location=GeoPoint(lat=lat, lng=lng),
            phone="+94" + str(g.rng.randint(110000000, 819999999)),
        )
        for name, district, lat, lng in catalog.HOSPITALS
    ]

    practitioners: list[Practitioner] = []
    roles: list[PractitionerRole] = []
    for index, specialty in enumerate(catalog.SPECIALTY_MIX):
        org = organizations[index % len(organizations)]
        if index == 0:  # Dr. Ruwan Silva, general medicine, Lotus Care Kurunegala (spec 16)
            given, family, org = "Ruwan", "Silva", organizations[0]
        else:
            given = g.rng.choice(catalog.GIVEN_MALE + catalog.GIVEN_FEMALE)
            family = g.rng.choice(catalog.FAMILY)
        langs = [Language.SI, Language.EN] if g.rng.random() < 0.85 else [Language.EN]
        practitioner = Practitioner(
            id=ResourceId(g.uuid()),
            meta=g.meta(),
            name=HumanName(given=[given], family=family, text=f"Dr. {given} {family}"),
            qualifications=["MBBS"],
            languages=langs,
        )
        practitioners.append(practitioner)
        roles.append(
            PractitionerRole(
                id=ResourceId(g.uuid()),
                meta=g.meta(),
                practitioner_id=PractitionerId(practitioner.id),
                organization_id=OrganizationId(org.id),
                specialty=specialty,
                fee_lkr=g.rng.choice([1500, 2000, 2500, 3000, 3500]),
            )
        )

    schedules: list[Schedule] = []
    for role in roles:
        for day in range(14):
            when = ANCHOR + timedelta(days=day)
            if when.weekday() == 6 or g.rng.random() < 0.35:
                continue
            start_hour = g.rng.choice([8, 14, 17])
            start = datetime.combine(when, time(start_hour, 0), tzinfo=UTC) - timedelta(
                hours=5, minutes=30
            )
            schedules.append(
                Schedule(
                    id=ResourceId(g.uuid()),
                    meta=g.meta(),
                    practitioner_role_id=PractitionerRoleId(role.id),
                    organization_id=role.organization_id,
                    planning_horizon=Period(start=start, end=start + timedelta(hours=3)),
                    capacity=g.rng.randint(15, 25),
                )
            )

    for index, practitioner in enumerate(practitioners):
        uid = UserId(g.uuid())
        login = "dr.silva@demo.suwapath.lk" if index == 0 else f"doctor{index:02d}@demo.suwapath.lk"
        users.append(
            SeedUser(
                id=uid,
                role=Role.DOCTOR,
                login=login,
                display_name=practitioner.name.text or "",
                language=Language.EN,
                practitioner_id=PractitionerId(practitioner.id),
            )
        )
        practitioners[index] = practitioner.model_copy(update={"user_id": uid})

    users.append(
        SeedUser(
            id=UserId(g.uuid()),
            role=Role.HOSPITAL_ADMIN,
            login="admin.lotus@demo.suwapath.lk",
            display_name="Lotus Care admin",
            language=Language.EN,
            organization_id=OrganizationId(organizations[0].id),
        )
    )
    users.append(
        SeedUser(
            id=UserId(g.uuid()),
            role=Role.SYSTEM_ADMIN,
            login="ops@demo.suwapath.lk",
            display_name="Operations",
            language=Language.EN,
        )
    )

    patients: list[Patient] = []
    related: list[RelatedPerson] = []
    consents: list[Consent] = []
    conditions: list[Condition] = []
    allergies: list[AllergyIntolerance] = []
    medications: list[MedicationStatement] = []
    observations: list[Observation] = []
    provenance: list[Provenance] = []
    reports: list[LabReport] = []
    adherence: list[AdherencePlan] = []
    labels: list[BriefLabel] = []

    ages = (
        [72]
        + [g.rng.randint(60, 88) for _ in range(19)]
        + [g.rng.randint(25, 59) for _ in range(5)]
        + [g.rng.randint(1, 12) for _ in range(3)]
        + [g.rng.randint(24, 34) for _ in range(2)]
    )
    for index, age in enumerate(ages):
        amma = index == 0
        female = amma or index >= 28 or g.rng.random() < 0.55
        given = (
            "Kamala"
            if amma
            else g.rng.choice(catalog.GIVEN_FEMALE if female else catalog.GIVEN_MALE)
        )
        family = "Perera" if amma else g.rng.choice(catalog.FAMILY)
        org = organizations[0] if amma else g.rng.choice(organizations)
        pid = PatientId(g.uuid())
        user_id = UserId(g.uuid())
        born = ANCHOR.replace(year=ANCHOR.year - age) - timedelta(days=g.rng.randint(0, 300))
        language = Language.SI if amma or g.rng.random() < 0.75 else Language.EN
        patients.append(
            Patient(
                id=ResourceId(pid),
                meta=g.meta(),
                patient_id=pid,
                user_id=user_id,
                name=HumanName(given=[given], family=family, text=f"{given} {family}"),
                birth_date=born,
                age_band=age_band(born, ANCHOR),
                gender="female" if female else "male",
                language=language,
                telecom=[ContactPoint(system="phone", value=g.phone())],
                address=Address(district=org.district),
                nic=f"{g.rng.randint(400000000, 999999999)}V" if age >= 18 else None,
                district=org.district,
                location=GeoPoint(
                    lat=org.location.lat + g.rng.uniform(-0.05, 0.05),
                    lng=org.location.lng + g.rng.uniform(-0.05, 0.05),
                ),
            )
        )
        users.append(
            SeedUser(
                id=user_id,
                role=Role.PATIENT,
                login="amma@demo.suwapath.lk" if amma else f"patient{index:02d}@demo.suwapath.lk",
                display_name=f"{given} {family}",
                language=language,
                patient_id=pid,
            )
        )
        source_doc = DocumentId(g.uuid())

        def cite(
            target: ResourceId, kind: str, pid: PatientId = pid, source_doc: DocumentId = source_doc
        ) -> ResourceId:
            prov = Provenance(
                id=ResourceId(g.uuid()),
                meta=g.meta(),
                patient_id=pid,
                targets=[Reference(type=kind, id=target)],
                recorded_at=g.now,
                agent=SEED_SOURCE,
                activity="imported",
                entity=ProvenanceEntity(document_id=source_doc),
            )
            provenance.append(prov)
            return prov.id

        if amma:
            codes = ["diabetes_type2", "hypertension", "osteoarthritis"]
            meds = ["metformin", "amlodipine", "atorvastatin", "paracetamol"]
            allergy_codes = ["penicillin"]
        elif age >= 60:
            codes = g.rng.sample([c for c, _ in catalog.CONDITIONS], g.rng.randint(2, 4))
            meds = sorted(
                {m for m, (_, treats, _) in catalog.FORMULARY.items() if set(treats) & set(codes)}
            )
            meds = meds[: g.rng.randint(2, min(7, max(2, len(meds))))] if meds else ["paracetamol"]
            allergy_codes = g.rng.sample([a for a, _ in catalog.ALLERGIES], g.rng.randint(0, 1))
        elif age < 18:
            codes, meds, allergy_codes = [], [], []
        else:
            codes = g.rng.sample(
                ["hypertension", "dyslipidaemia", "hypothyroidism"], g.rng.randint(0, 1)
            )
            meds = [
                m for m, (_, treats, _) in catalog.FORMULARY.items() if set(treats) & set(codes)
            ][:1]
            allergy_codes = []

        planted: list[str] = []
        if (
            not amma and age >= 60 and index % 3 == 0
        ):  # SEED-2: planted clashes for the briefs suite
            plant = ("allergy", "interaction", "duplicate", "nsaid_ckd")[(index // 3) % 4]
            if plant == "allergy":
                allergy_codes = ["penicillin"]
                meds = [*meds, "amoxicillin"]
            elif plant == "interaction":
                meds = [*meds, "warfarin", "aspirin"]
            elif plant == "duplicate":
                meds = [*meds, "atorvastatin", "rosuvastatin"]
            else:
                codes = [*codes, "ckd_stage3"]
                meds = [*meds, "diclofenac"]
            planted.append(plant)
            meds = list(dict.fromkeys(meds))
            codes = list(dict.fromkeys(codes))

        facts: list[ResourceId] = []
        display = dict(catalog.CONDITIONS)
        for code in codes:
            cid = ResourceId(g.uuid())
            conditions.append(
                Condition(
                    id=cid,
                    meta=g.meta(),
                    patient_id=pid,
                    clinical_status="active",
                    code=CodeableConcept(
                        coding=[Coding(system="local", code=code)], text=display.get(code, code)
                    ),
                    provenance_ids=[cite(cid, "Condition")],
                )
            )
            facts.append(cid)
        for code in allergy_codes:
            aid = ResourceId(g.uuid())
            klass = dict(catalog.ALLERGIES)[code]
            allergies.append(
                AllergyIntolerance(
                    id=aid,
                    meta=g.meta(),
                    patient_id=pid,
                    criticality="high",
                    code=CodeableConcept(
                        coding=[
                            Coding(system="local", code=code),
                            Coding(system="class", code=klass),
                        ],
                        text=code.replace("_", " ").capitalize(),
                    ),
                    provenance_ids=[cite(aid, "AllergyIntolerance")],
                )
            )
            facts.append(aid)
        for med in meds:
            mid = ResourceId(g.uuid())
            klass, _, slots = catalog.FORMULARY[med]
            medications.append(
                MedicationStatement(
                    id=mid,
                    meta=g.meta(),
                    patient_id=pid,
                    status="active",
                    medication=CodeableConcept(
                        coding=[
                            Coding(system="local", code=med),
                            Coding(system="class", code=klass),
                        ],
                        text=med.replace("_", " ").capitalize(),
                    ),
                    dosage=Dosage(
                        text=None,
                        timing=DoseTiming(slots=[DoseSlot(s) for s in slots], as_needed=not slots),
                    ),
                    provenance_ids=[cite(mid, "MedicationStatement")],
                )
            )
            facts.append(mid)

        if age >= 18 and (codes or amma):
            for _ in range(1 if not amma else 2):
                issued = g.now - timedelta(days=g.rng.randint(7, 90))
                rows: list[LabRow] = []
                doc = DocumentId(g.uuid())
                for loinc, name, unit, low, high in (
                    g.rng.sample(catalog.LAB_TESTS, 3) if not amma else catalog.LAB_TESTS[:3]
                ):
                    value = round(
                        g.rng.uniform(
                            low * 0.9, high * (1.6 if "diabetes_type2" in codes else 1.05)
                        ),
                        1,
                    )
                    if amma and loinc == "4548-4":
                        value = 8.4  # Amma's HbA1c is above range (spec 16 scenario)
                    oid = ResourceId(g.uuid())
                    flag = "high" if value > high else ("low" if value < low else "normal")
                    prov = Provenance(
                        id=ResourceId(g.uuid()),
                        meta=g.meta(),
                        patient_id=pid,
                        targets=[Reference(type="Observation", id=oid)],
                        recorded_at=issued,
                        agent=SEED_SOURCE,
                        activity="extracted",
                        entity=ProvenanceEntity(document_id=doc, page=1, line=len(rows) + 3),
                    )
                    provenance.append(prov)
                    observations.append(
                        Observation(
                            id=oid,
                            meta=g.meta(),
                            patient_id=pid,
                            effective_at=issued,
                            interpretation=flag,
                            code=CodeableConcept(
                                coding=[Coding(system="http://loinc.org", code=loinc)], text=name
                            ),
                            value=Quantity(value=value, unit=unit),
                            reference_range=ReferenceRange(low=low, high=high),
                            provenance_ids=[prov.id],
                        )
                    )
                    rows.append(
                        LabRow(
                            observation_id=oid,
                            test=name,
                            value=value,
                            unit=unit,
                            low=low,
                            high=high,
                        )
                    )
                    if loinc == "4548-4":
                        facts.append(oid)
                reports.append(
                    LabReport(
                        document_id=doc,
                        patient_id=pid,
                        issued_at=issued,
                        lab_name=f"{org.name} laboratory",
                        rows=tuple(rows),
                    )
                )

        pattern: Literal["good", "slipping", "evening_misses"] = (
            "evening_misses" if amma else g.rng.choice(catalog.ADHERENCE_PATTERNS)
        )
        if meds:
            adherence.append(AdherencePlan(patient_id=pid, pattern=pattern))
        labels.append(
            BriefLabel(
                patient_id=pid, required_fact_ids=tuple(facts), planted_clashes=tuple(planted)
            )
        )

        guardian_count = 1 if amma else (1 if age >= 60 and g.rng.random() < 0.8 else 0)
        for _ in range(guardian_count):
            guardian_id = UserId(g.uuid())
            g_given = "Nimal" if amma else g.rng.choice(catalog.GIVEN_MALE + catalog.GIVEN_FEMALE)
            abroad = amma or g.rng.random() < 0.4
            users.append(
                SeedUser(
                    id=guardian_id,
                    role=Role.GUARDIAN,
                    login="nimal@demo.suwapath.lk"
                    if amma
                    else f"guardian{index:02d}@demo.suwapath.lk",
                    display_name=f"{g_given} {family}",
                    language=Language.EN if abroad else language,
                )
            )
            related.append(
                RelatedPerson(
                    id=ResourceId(g.uuid()),
                    meta=g.meta(),
                    patient_id=pid,
                    user_id=guardian_id,
                    relationship="son" if amma else g.rng.choice(["son", "daughter", "spouse"]),
                    name=HumanName(given=[g_given], family=family, text=f"{g_given} {family}"),
                    telecom=[ContactPoint(system="phone", value=g.phone())],
                    lives_abroad=abroad,
                )
            )
            consents.append(
                Consent(
                    id=ResourceId(g.uuid()),
                    meta=g.meta(),
                    patient_id=pid,
                    grantee_id=guardian_id,
                    grantee_role="guardian",
                    categories=frozenset(
                        {
                            DataCategory.MEDICATIONS,
                            DataCategory.ADHERENCE,
                            DataCategory.APPOINTMENTS,
                        }
                    ),
                    purposes=frozenset({"guardian_monitoring", "emergency"}),
                    period=Period(start=g.now),
                    status="active",
                )
            )

    return World(
        seed=seed,
        anchor_date=ANCHOR,
        users=tuple(users),
        organizations=tuple(organizations),
        practitioners=tuple(practitioners),
        practitioner_roles=tuple(roles),
        schedules=tuple(schedules),
        patients=tuple(patients),
        related_persons=tuple(related),
        consents=tuple(consents),
        conditions=tuple(conditions),
        allergies=tuple(allergies),
        medications=tuple(medications),
        observations=tuple(observations),
        provenance=tuple(provenance),
        lab_reports=tuple(reports),
        adherence=tuple(adherence),
        brief_labels=tuple(labels),
    )


def expand_doses(
    world: World, plan: AdherencePlan, days: int = 30
) -> list[tuple[ResourceId, datetime, DoseSlot, bool]]:
    """Deterministic dose history: (statement, scheduled time, slot, taken) for the last days."""
    slot_time = {
        DoseSlot.MORNING: time(8),
        DoseSlot.NOON: time(13),
        DoseSlot.EVENING: time(19),
        DoseSlot.NIGHT: time(22),
    }
    rng = random.Random(f"{world.seed}:{plan.patient_id}")  # noqa: S311 (synthetic data)
    history = []
    for statement in (m for m in world.medications if m.patient_id == plan.patient_id):
        for offset in range(days, 0, -1):
            day = world.anchor_date - timedelta(days=offset)
            for slot in statement.dosage.timing.slots:
                when = datetime.combine(day, slot_time[slot], tzinfo=UTC) - timedelta(
                    hours=5, minutes=30
                )
                if plan.pattern == "good":
                    taken = rng.random() > 0.03
                elif plan.pattern == "slipping":
                    taken = rng.random() > (0.05 + 0.25 * (days - offset) / days)
                else:  # evening_misses: the last two evenings are missed (spec 16 step 0)
                    taken = not (slot is DoseSlot.EVENING and offset <= 2) and rng.random() > 0.05
                history.append((statement.id, when, slot, taken))
    return history
