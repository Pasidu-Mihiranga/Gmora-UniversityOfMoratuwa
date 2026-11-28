"""`RecordService` on Postgres (M05, SEC-11, CON-7 to CON-9).

- Every resource is stored whole, sealed with the patient's key (`PatientCipher`), with only the
  columns queries need left plain (status, timing, dates, codes). That is stricter than the spec's
  per-field list: names, values and notes are all sealed.
- Every read asks the `AccessPolicy` for that patient and category. A resource the principal may
  not see is `NotFound`, the same as one that does not exist, so IDs cannot be probed (CON-11).
  When the policy narrows to some fields (the emergency alert), record resources are left out:
  those fields travel in messages, never as partial records.
- Every written clinical fact gets a `Provenance` (CON-9): the caller's, or one recording who
  entered it.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import select

from suwapath.contracts.common import Page
from suwapath.contracts.enums import DataCategory, PrincipalKind, Purpose, Role
from suwapath.contracts.errors import AccessDenied, NotFound, ValidationFailed, VersionConflict
from suwapath.contracts.events.catalog import ResourceWritten
from suwapath.contracts.fhir.base import Meta, PatientResource, Reference
from suwapath.contracts.fhir.resources import (
    AllergyIntolerance,
    Condition,
    DoseTiming,
    MedicationStatement,
    Observation,
    Patient,
    Provenance,
    ProvenanceEntity,
)
from suwapath.contracts.ids import PatientId, ResourceId, TraceId, UserId
from suwapath.contracts.ports.audit import AuditEntryDraft
from suwapath.contracts.ports.consent import AccessDecision, AccessPolicy, AccessRequest
from suwapath.contracts.ports.crypto import PatientCipher
from suwapath.contracts.ports.record import MedicationSchedule, RecordSummary
from suwapath.contracts.principal import Principal
from suwapath.modules.record.adapters.models import (
    AllergyRow,
    ConditionRow,
    MedicationStatementRow,
    ObservationRow,
    PatientRow,
    ProvenanceRow,
)
from suwapath.platform.ids import new_id
from suwapath.platform.uow import UnitOfWork, UnitOfWorkFactory


@dataclass(frozen=True)
class Kind:
    model: type[PatientResource]
    row: type[Any]
    category: DataCategory
    plain: Callable[[Any], dict[str, Any]]  # the columns queries need, from the resource


def _timing(m: Any) -> dict[str, Any]:
    return {"status": m.status, "timing": m.dosage.timing.model_dump(mode="json")}


def _observation(o: Any) -> dict[str, Any]:
    return {
        "effective_at": o.effective_at,
        "interpretation": o.interpretation,
        "derived_from": o.derived_from.id if o.derived_from else None,
    }


def _patient(p: Any) -> dict[str, Any]:
    return {
        "user_id": p.user_id,
        "age_band": p.age_band,
        "gender": p.gender,
        "language": p.language.value,
        "district": p.district,
        "lat": p.location.lat if p.location else None,
        "lng": p.location.lng if p.location else None,
    }


KINDS: dict[str, Kind] = {
    "Patient": Kind(Patient, PatientRow, DataCategory.DEMOGRAPHICS, _patient),
    "Condition": Kind(
        Condition,
        ConditionRow,
        DataCategory.CONDITIONS,
        lambda c: {"clinical_status": c.clinical_status},
    ),
    "AllergyIntolerance": Kind(
        AllergyIntolerance,
        AllergyRow,
        DataCategory.ALLERGIES,
        lambda a: {"criticality": a.criticality},
    ),
    "MedicationStatement": Kind(
        MedicationStatement, MedicationStatementRow, DataCategory.MEDICATIONS, _timing
    ),
    "Observation": Kind(Observation, ObservationRow, DataCategory.OBSERVATIONS, _observation),
}
FIELD = "data_enc"


class SqlRecordService:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        policy: Callable[[], AccessPolicy | None],
        cipher: Callable[[], PatientCipher | None],
    ) -> None:
        self._uow = uow_factory
        self._policy = policy
        self._cipher = cipher

    # ----- reads ----------------------------------------------------------------------------------

    async def patient_for_user(self, user_id: UserId) -> PatientId | None:
        async with self._uow.join() as uow:
            found = (
                await uow.session.execute(
                    select(PatientRow.id).where(PatientRow.user_id == user_id)
                )
            ).scalar()
        return PatientId(found) if found else None

    async def medication_schedules(self, patient_id: PatientId) -> list[MedicationSchedule]:
        async with self._uow.join() as uow:
            rows = (
                await uow.session.execute(
                    select(MedicationStatementRow.id, MedicationStatementRow.timing).where(
                        MedicationStatementRow.patient_id == patient_id,
                        MedicationStatementRow.status == "active",
                    )
                )
            ).all()
        return [
            MedicationSchedule(statement_id=ResourceId(i), timing=DoseTiming.model_validate(t))
            for i, t in rows
        ]

    async def summary(
        self, principal: Principal, patient_id: PatientId, purpose: Purpose
    ) -> RecordSummary:
        found: dict[str, list[Any]] = {}
        hidden = 0
        async with self._uow.join() as uow:
            for name, kind in KINDS.items():
                rows = await self._rows(uow, kind, patient_id)
                if not rows:
                    continue
                if not await self._whole(principal, patient_id, kind.category, "read"):
                    hidden += 1
                    continue
                found[name] = [await self._open(kind, row) for row in rows]
        if not found:
            raise NotFound()  # nothing visible: do not even confirm the patient exists (CON-11)
        patients = found.get("Patient", [])
        return RecordSummary(
            patient=patients[0] if patients else None,
            conditions=tuple(found.get("Condition", [])),
            allergies=tuple(found.get("AllergyIntolerance", [])),
            medications=tuple(found.get("MedicationStatement", [])),
            recent_observations=tuple(
                sorted(found.get("Observation", []), key=lambda o: o.effective_at, reverse=True)
            ),
            hidden_categories_count=hidden,
        )

    async def list_resources(
        self,
        principal: Principal,
        patient_id: PatientId,
        resource_type: str,
        cursor: str | None,
        limit: int,
    ) -> Page[PatientResource]:
        kind = _kind(resource_type)
        if not await self._whole(principal, patient_id, kind.category, "read"):
            raise NotFound()
        limit = max(1, min(limit, 100))
        async with self._uow.join() as uow:
            query = (
                select(kind.row)
                .where(
                    kind.row.patient_id == patient_id
                    if kind.row is not PatientRow
                    else kind.row.id == patient_id
                )
                .order_by(kind.row.id)
                .limit(limit + 1)
            )
            if cursor:
                query = query.where(kind.row.id > _cursor(cursor))
            rows = list((await uow.session.execute(query)).scalars())
            items = [await self._open(kind, r) for r in rows[:limit]]
        more = len(rows) > limit
        return Page(items=items, next_cursor=str(rows[limit - 1].id) if more else None)

    async def get_resource(
        self, principal: Principal, resource_type: str, resource_id: ResourceId
    ) -> PatientResource:
        kind = _kind(resource_type)
        async with self._uow.join() as uow:
            row = await uow.session.get(kind.row, resource_id)
            if row is None:
                raise NotFound()
            patient = PatientId(row.id if kind.row is PatientRow else row.patient_id)
            if not await self._whole(principal, patient, kind.category, "read"):
                raise NotFound()  # IDOR: someone else's resource looks like no resource (CON-11)
            return await self._open(kind, row)

    async def get_provenance(self, principal: Principal, provenance_id: ResourceId) -> Provenance:
        async with self._uow.join() as uow:
            row = await uow.session.get(ProvenanceRow, provenance_id)
            if row is None:
                raise NotFound()
            patient = PatientId(row.patient_id)
            targets = [Reference.model_validate(t) for t in row.targets]
            kinds = {t.type for t in targets if t.type in KINDS}
            allowed = [
                await self._whole(principal, patient, KINDS[k].category, "read") for k in kinds
            ]
            if not allowed or not any(allowed):
                raise NotFound()  # the source of something the principal may not see
            entity = await self._decrypt(
                patient, "record_provenance", "entity_enc", row.id, row.entity_enc
            )
            return Provenance(
                id=ResourceId(row.id),
                meta=Meta(last_updated=row.recorded_at, source="record"),
                patient_id=patient,
                targets=targets,
                recorded_at=row.recorded_at,
                agent=row.agent,
                activity=row.activity,
                entity=ProvenanceEntity.model_validate_json(entity),
            )

    async def export_fhir(self, principal: Principal, patient_id: PatientId) -> dict[str, Any]:
        """FHIR R4 Bundle (type collection): only the patient exports their own record."""
        if principal.kind is not PrincipalKind.USER or principal.role is not Role.PATIENT:
            raise NotFound()
        record = await self.summary(principal, patient_id, Purpose.SELF_ACCESS)
        resources: list[BaseModel] = [
            *([record.patient] if record.patient else []),
            *record.conditions,
            *record.allergies,
            *record.medications,
            *record.recent_observations,
        ]
        if not resources:
            raise NotFound()
        return {
            "resourceType": "Bundle",
            "type": "collection",
            "entry": [
                {"fullUrl": f"urn:uuid:{r.id}", "resource": _fhir(r, patient_id)}  # type: ignore[attr-defined]
                for r in resources
            ],
        }

    # ----- writes ---------------------------------------------------------------------------------

    async def write_resource(
        self, principal: Principal, resource: PatientResource
    ) -> PatientResource:
        """Creates, or updates at the expected version (`meta.version_id`); emits
        `record.resource_written`. The policy decides the write (patients: adherence, uploads)."""
        kind = _kind(type(resource).resource_type)
        patient = PatientId(resource.id if isinstance(resource, Patient) else resource.patient_id)
        if not await self._whole(principal, patient, kind.category, "write"):
            raise AccessDenied()
        async with self._uow.join() as uow:
            if not resource.provenance_ids:
                provenance = await self._add_provenance(
                    uow,
                    patient,
                    [Reference(type=kind.model.resource_type, id=resource.id)],
                    agent=_actor(principal),
                    activity="entered",
                    entity=ProvenanceEntity(),
                )
                resource = resource.model_copy(update={"provenance_ids": [provenance]})
            stored = await self._store(uow, kind, resource, patient)
            uow.publish(
                ResourceWritten(
                    resource_type=kind.model.resource_type,
                    resource_id=resource.id,
                    patient_id=patient,
                ),
                aggregate_id=str(resource.id),
                patient_id=patient,
            )
            await uow.audit(
                AuditEntryDraft(
                    actor_kind=principal.kind,
                    actor_id=str(principal.user_id) if principal.user_id else None,
                    agent_kind=principal.agent_kind,
                    acting_for=principal.acting_for,
                    patient_id=patient,
                    action="record.write",
                    resource_type=kind.model.resource_type,
                    resource_id=str(resource.id),
                    purpose=principal.purpose,
                    outcome="allow",
                    trace_id=TraceId(principal.trace_id),
                )
            )
            return stored

    async def import_resources(
        self,
        resources: Sequence[PatientResource],
        *,
        agent: str,
        document_id: Any = None,
    ) -> int:
        """For the seed loader and report extraction, running as this module (no person): each
        resource gets an `imported` provenance (CON-9). Idempotent by resource id."""
        written = 0
        async with self._uow.join() as uow:
            for resource in resources:
                kind = _kind(type(resource).resource_type)
                patient = PatientId(
                    resource.id if isinstance(resource, Patient) else resource.patient_id
                )
                if await uow.session.get(kind.row, resource.id) is not None:
                    continue
                if not resource.provenance_ids and kind.model is not Patient:
                    provenance = await self._add_provenance(
                        uow,
                        patient,
                        [Reference(type=kind.model.resource_type, id=resource.id)],
                        agent=agent,
                        activity="imported",
                        entity=ProvenanceEntity(document_id=document_id),
                    )
                    resource = resource.model_copy(update={"provenance_ids": [provenance]})
                await self._store(uow, kind, resource, patient)
                uow.publish(  # care schedules doses from imported medicines too
                    ResourceWritten(
                        resource_type=kind.model.resource_type,
                        resource_id=resource.id,
                        patient_id=patient,
                    ),
                    aggregate_id=str(resource.id),
                    patient_id=patient,
                )
                written += 1
        return written

    async def destroy_patient_key(self, principal: Principal, patient_id: PatientId) -> None:
        """Crypto-shredding on the patient's own request (SEC-25)."""
        if principal.kind is not PrincipalKind.USER or principal.patient_scope != patient_id:
            raise NotFound()
        cipher = self._need_cipher()
        async with self._uow.join() as uow:
            await cipher.destroy_key(patient_id)
            await uow.audit(
                AuditEntryDraft(
                    actor_kind=principal.kind,
                    actor_id=str(principal.user_id),
                    patient_id=patient_id,
                    action="record.erase",
                    resource_type="Patient",
                    resource_id=str(patient_id),
                    purpose=principal.purpose,
                    outcome="allow",
                    trace_id=TraceId(principal.trace_id),
                )
            )

    # ----- internals ----------------------------------------------------------------------------

    async def _whole(
        self, principal: Principal, patient_id: PatientId, category: DataCategory, action: str
    ) -> bool:
        """Allowed to see or write whole resources of this category (no field narrowing)."""
        policy = self._policy()
        if policy is None:
            return False  # deny by default until consent is bound
        decision: AccessDecision = await policy.decide(
            AccessRequest(
                principal=principal,
                action=action,
                patient_id=patient_id,
                category=category,
                resource_type="Record",
                purpose=principal.purpose,
            )
        )
        return decision.allowed and decision.allowed_fields is None

    async def _rows(self, uow: UnitOfWork, kind: Kind, patient_id: PatientId) -> list[Any]:
        column = kind.row.id if kind.row is PatientRow else kind.row.patient_id
        return list(
            (await uow.session.execute(select(kind.row).where(column == patient_id))).scalars()
        )

    async def _store(
        self, uow: UnitOfWork, kind: Kind, resource: PatientResource, patient: PatientId
    ) -> PatientResource:
        row = await uow.session.get(kind.row, resource.id)
        version = 1 if row is None else row.version + 1
        if row is not None and resource.meta.version_id != row.version:
            raise VersionConflict()
        stored = resource.model_copy(
            update={"meta": Meta(version_id=version, last_updated=uow.clock.now(), source="record")}
        )
        sealed = await self._encrypt(
            patient, kind.row.__tablename__, FIELD, resource.id, stored.model_dump_json().encode()
        )
        values = {**kind.plain(stored), "data_enc": sealed, "version": version}
        if kind.row is not PatientRow:
            values["patient_id"] = patient
            values["provenance_ids"] = list(stored.provenance_ids)
        if row is None:
            uow.session.add(kind.row(id=resource.id, **values))
        else:
            for key, value in values.items():
                setattr(row, key, value)
        await uow.session.flush()
        return stored

    async def _open(self, kind: Kind, row: Any) -> PatientResource:
        patient = PatientId(row.id if kind.row is PatientRow else row.patient_id)
        data = await self._decrypt(patient, kind.row.__tablename__, FIELD, row.id, row.data_enc)
        return kind.model.model_validate_json(data)

    async def _add_provenance(
        self,
        uow: UnitOfWork,
        patient: PatientId,
        targets: list[Reference],
        *,
        agent: str,
        activity: str,
        entity: ProvenanceEntity,
    ) -> ResourceId:
        provenance_id = ResourceId(new_id())
        uow.session.add(
            ProvenanceRow(
                id=provenance_id,
                patient_id=patient,
                targets=[t.model_dump(mode="json") for t in targets],
                activity=activity,
                agent=agent[:80],
                recorded_at=uow.clock.now(),
                entity_enc=await self._encrypt(
                    patient,
                    "record_provenance",
                    "entity_enc",
                    provenance_id,
                    entity.model_dump_json().encode(),
                ),
            )
        )
        return provenance_id

    async def _encrypt(
        self, patient: PatientId, table: str, column: str, row_id: UUID, data: bytes
    ) -> bytes:
        return await self._need_cipher().encrypt(patient, table, column, row_id, data)

    async def _decrypt(
        self, patient: PatientId, table: str, column: str, row_id: UUID, data: bytes
    ) -> bytes:
        return await self._need_cipher().decrypt(patient, table, column, row_id, data)

    def _need_cipher(self) -> PatientCipher:
        cipher = self._cipher()
        if cipher is None:
            raise RuntimeError("the record is never stored unencrypted: bind patient_cipher (M04)")
        return cipher


def _kind(resource_type: str) -> Kind:
    kind = KINDS.get(resource_type)
    if kind is None:
        raise ValidationFailed("unknown resource type", fields=["type"])
    return kind


def _cursor(cursor: str) -> UUID:
    try:
        return UUID(cursor)
    except ValueError:
        raise NotFound() from None


def _actor(principal: Principal) -> str:
    if principal.kind is PrincipalKind.AGENT and principal.agent_kind is not None:
        return f"agent:{principal.agent_kind.value}"
    return f"user:{principal.user_id}" if principal.user_id else "system"


def _fhir(resource: BaseModel, patient_id: PatientId) -> dict[str, Any]:
    """A FHIR-shaped JSON object: resourceType, and the patient as a reference (CON-7)."""
    data = resource.model_dump(mode="json", exclude_none=True)
    data.pop("patient_id", None)
    data.pop("provenance_ids", None)
    data["resourceType"] = type(resource).resource_type  # type: ignore[attr-defined]
    if not isinstance(resource, Patient):
        data["subject"] = {"reference": f"Patient/{patient_id}"}
    return data
