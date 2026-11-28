"""Consent on Postgres: the access policy's facts, grants, and care relationships (M03).

`SqlAccessPolicy.decide` looks every fact up fresh (SEC-12), so a revoked grant or an ended care
relationship takes effect on the very next request. The decision itself is the pure
`domain.policy.decide`.

Customisation (settings, plain-language rules, suggestions) is issue C01 and answers
`CapabilityMissing` until then.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select, update

from suwapath.contracts.enums import (
    SENSITIVE_CATEGORIES,
    DataCategory,
    Language,
    PrincipalKind,
    Purpose,
    Role,
)
from suwapath.contracts.errors import CapabilityMissing, NotFound, ValidationFailed
from suwapath.contracts.events.catalog import GrantChanged
from suwapath.contracts.fhir.base import Meta, Period
from suwapath.contracts.fhir.resources import Consent
from suwapath.contracts.ids import AppointmentId, PatientId, ResourceId, TraceId, UserId
from suwapath.contracts.ports.audit import AuditEntryDraft
from suwapath.contracts.ports.consent import AccessDecision, AccessRequest
from suwapath.contracts.ports.identity import UserAccount
from suwapath.contracts.principal import Principal
from suwapath.modules.consent.adapters.models import CareRelationshipRow, GrantRow
from suwapath.modules.consent.domain.policy import Facts, Grant, decide
from suwapath.platform.ids import new_id
from suwapath.platform.uow import UnitOfWork, UnitOfWorkFactory

TREATING_AFTER = timedelta(days=30)  # SEC-10 rule 4
EMERGENCY = Purpose.EMERGENCY.value
UserLookup = Callable[[UserId], Any]  # async: identity.get_user
ScopeSource = Callable[[], Mapping[str, frozenset[DataCategory]]]


class SqlAccessPolicy:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        users: Callable[[], Any | None],
        agent_scopes: ScopeSource,
    ) -> None:
        self._uow = uow_factory
        self._users = users  # the identity port, looked up per call (bound by another module)
        self._scopes = agent_scopes

    async def decide(self, request: AccessRequest) -> AccessDecision:
        return decide(request, await self._facts(request))

    async def _facts(self, request: AccessRequest) -> Facts:
        principal = request.principal
        human = principal.effective_user
        if principal.kind is PrincipalKind.SYSTEM or human is None:
            return Facts()
        account = None
        if principal.kind is PrincipalKind.AGENT or principal.role is Role.PATIENT:
            identity = self._users()
            if identity is not None:
                try:
                    account = await identity.get_user(human)
                except NotFound:
                    return Facts()
        role = (
            principal.role
            if principal.kind is PrincipalKind.USER
            else (account.role if account else None)
        )
        own = principal.patient_scope if principal.kind is PrincipalKind.USER else None
        if account is not None and account.role is Role.PATIENT:
            own = account.patient_id  # the account is the truth about which record is theirs
        scope = None
        if principal.kind is PrincipalKind.AGENT and principal.agent_kind is not None:
            scope = self._scopes().get(principal.agent_kind.value)
        async with self._uow.join() as uow:
            now = uow.clock.now()
            grant = await _active_grant(uow, request.patient_id, human, now)
            treating, sensitive = False, frozenset[DataCategory]()
            if role is Role.DOCTOR:
                treating = await _treating(uow, request.patient_id, human, now)
                if grant is not None and grant.grantee_role == Role.DOCTOR.value:
                    sensitive = (
                        frozenset(DataCategory(c) for c in grant.categories) & SENSITIVE_CATEGORIES
                    )
        guardian_grant = None
        if grant is not None and grant.grantee_role == Role.GUARDIAN.value:
            guardian_grant = Grant(
                categories=frozenset(DataCategory(c) for c in grant.categories),
                emergency=EMERGENCY in grant.purposes,
            )
        return Facts(
            human_role=role,
            own_patient=own,
            guardian_grant=guardian_grant,
            treating=treating,
            doctor_sensitive=sensitive,
            agent_scope=scope,
        )


class SqlConsentService:
    def __init__(self, uow_factory: UnitOfWorkFactory, users: Callable[[], Any | None]) -> None:
        self._uow = uow_factory
        self._users = users

    # ----- grants (the patient decides, CUS-3) --------------------------------------------------

    async def grants_for_patient(
        self, principal: Principal, patient_id: PatientId
    ) -> list[Consent]:
        _own(principal, patient_id)
        async with self._uow.join() as uow:
            rows = (
                await uow.session.execute(
                    select(GrantRow)
                    .where(GrantRow.patient_id == patient_id, GrantRow.status == "active")
                    .order_by(GrantRow.created_at)
                )
            ).scalars()
            return [_consent(r) for r in rows]

    async def set_grant(
        self,
        principal: Principal,
        patient_id: PatientId,
        grantee_id: UserId,
        categories: frozenset[DataCategory],
        idempotency_key: str,
        *,
        emergency_alert: bool = True,
    ) -> Consent:
        """Idempotent: the same categories again change nothing. Sensitive categories are never
        granted to a guardian (agreed 28 Nov 2026); for a doctor they are the only thing a grant
        adds, because a doctor's access comes from treating (SEC-10 rule 4)."""
        _own(principal, patient_id)
        grantee = await self._grantee(grantee_id)
        if grantee.role is Role.GUARDIAN and categories & SENSITIVE_CATEGORIES:
            raise ValidationFailed(
                "sensitive categories are never shared with family", fields=["categories"]
            )
        purposes = sorted(
            {Purpose.GUARDIAN_MONITORING.value} | ({EMERGENCY} if emergency_alert else set())
        )
        if grantee.role is Role.DOCTOR:
            purposes = [Purpose.TREATMENT.value]
        async with self._uow.join() as uow:
            now = uow.clock.now()
            row = await _active_grant(uow, patient_id, grantee_id, now)
            before = frozenset(DataCategory(c) for c in row.categories) if row else None
            values = sorted(c.value for c in categories)
            if row is None:
                row = GrantRow(
                    id=new_id(),
                    patient_id=patient_id,
                    grantee_id=grantee_id,
                    grantee_role=grantee.role.value,
                    categories=values,
                    purposes=purposes,
                    status="active",
                    period_start=now,
                )
                uow.session.add(row)
                change = "granted"
            elif before == categories and sorted(row.purposes) == purposes:
                return _consent(row)
            else:
                row.categories, row.purposes = values, purposes
                row.version += 1
                row.updated_at = now
                change = "narrowed" if before is not None and categories < before else "widened"
            await uow.session.flush()
            self._announce(uow, principal, patient_id, grantee_id, categories, change)
            await self._audit(uow, principal, patient_id, f"consent.{change}", str(row.id))
            return _consent(row)

    async def revoke_grant(
        self, principal: Principal, patient_id: PatientId, grantee_id: UserId
    ) -> None:
        _own(principal, patient_id)
        async with self._uow.join() as uow:
            now = uow.clock.now()
            ended = (
                await uow.session.execute(
                    update(GrantRow)
                    .where(
                        GrantRow.patient_id == patient_id,
                        GrantRow.grantee_id == grantee_id,
                        GrantRow.status == "active",
                    )
                    .values(status="inactive", period_end=now, updated_at=now)
                    .returning(GrantRow.id)
                )
            ).scalar_one_or_none()
            if ended is None:
                return  # nothing to revoke: idempotent
            self._announce(uow, principal, patient_id, grantee_id, frozenset(), "revoked")
            await self._audit(uow, principal, patient_id, "consent.revoke", str(ended))

    # ----- who is linked to whom ----------------------------------------------------------------

    async def guardians_of(self, patient_id: PatientId) -> list[UserId]:
        async with self._uow.join() as uow:
            rows = await uow.session.execute(
                select(GrantRow.grantee_id).where(
                    GrantRow.patient_id == patient_id,
                    GrantRow.grantee_role == Role.GUARDIAN.value,
                    GrantRow.status == "active",
                )
            )
            return [UserId(u) for u in rows.scalars()]

    async def patients_of(self, guardian_id: UserId) -> list[PatientId]:
        async with self._uow.join() as uow:
            rows = await uow.session.execute(
                select(GrantRow.patient_id).where(
                    GrantRow.grantee_id == guardian_id,
                    GrantRow.grantee_role == Role.GUARDIAN.value,
                    GrantRow.status == "active",
                )
            )
            return [PatientId(p) for p in rows.scalars()]

    # ----- care relationships (booking writes these, SEC-10 rule 4) ----------------------------

    async def record_treatment(
        self,
        *,
        patient_id: PatientId,
        doctor_id: UserId,
        appointment_id: AppointmentId,
        booked_at: datetime,
        appointment_at: datetime,
    ) -> None:
        """Booked or completed: treating from booking until 30 days after the appointment.
        Idempotent by appointment."""
        async with self._uow.join() as uow:
            row = (
                await uow.session.execute(
                    select(CareRelationshipRow).where(
                        CareRelationshipRow.appointment_id == appointment_id
                    )
                )
            ).scalar()
            ends = appointment_at + TREATING_AFTER
            if row is None:
                uow.session.add(
                    CareRelationshipRow(
                        id=new_id(),
                        patient_id=patient_id,
                        doctor_id=doctor_id,
                        appointment_id=appointment_id,
                        starts_at=booked_at,
                        ends_at=ends,
                    )
                )
            else:
                row.ends_at = ends
            await uow.session.flush()

    async def end_treatment(self, appointment_id: AppointmentId) -> None:
        """A cancelled appointment ends the relationship it created, now."""
        async with self._uow.join() as uow:
            await uow.session.execute(
                update(CareRelationshipRow)
                .where(
                    CareRelationshipRow.appointment_id == appointment_id,
                    CareRelationshipRow.ends_at > uow.clock.now(),
                )
                .values(ends_at=uow.clock.now())
            )

    # ----- customisation is C01 ----------------------------------------------------------------

    async def settings(self, principal: Principal, owner_id: UserId) -> list[Any]:
        raise CapabilityMissing("customisation arrives with C01")

    async def change_settings(
        self, principal: Principal, owner_id: UserId, changes: dict[str, Any]
    ) -> list[Any]:
        raise CapabilityMissing("customisation arrives with C01")

    async def parse_rule(self, principal: Principal, text: str, language: Language) -> Any:
        raise CapabilityMissing("customisation arrives with C01")

    async def revert_setting(
        self, principal: Principal, owner_id: UserId, key: str, to_version: int
    ) -> Any:
        raise CapabilityMissing("customisation arrives with C01")

    async def suggest(
        self, principal: Principal, patient_id: PatientId, key: str, value: Any
    ) -> Any:
        raise CapabilityMissing("customisation arrives with C01")

    async def decide_suggestion(
        self, principal: Principal, suggestion_id: ResourceId, accept: bool
    ) -> Any:
        raise CapabilityMissing("customisation arrives with C01")

    # ----- internals ----------------------------------------------------------------------------

    async def _grantee(self, grantee_id: UserId) -> UserAccount:
        identity = self._users()
        if identity is None:
            raise NotFound()
        account: UserAccount = await identity.get_user(grantee_id)
        if account.role not in (Role.GUARDIAN, Role.DOCTOR):
            raise ValidationFailed(
                "access can be shared with a family member or a doctor", fields=["grantee_id"]
            )
        return account

    def _announce(
        self,
        uow: UnitOfWork,
        principal: Principal,
        patient_id: PatientId,
        grantee_id: UserId,
        categories: frozenset[DataCategory],
        change: str,
    ) -> None:
        uow.publish(
            GrantChanged(
                patient_id=patient_id,
                grantee_id=grantee_id,
                categories=categories,
                change=change,
            ),
            aggregate_id=str(patient_id),
            patient_id=patient_id,
        )

    async def _audit(
        self, uow: UnitOfWork, principal: Principal, patient_id: PatientId, action: str, ref: str
    ) -> None:
        await uow.audit(
            AuditEntryDraft(
                actor_kind=principal.kind,
                actor_id=str(principal.user_id) if principal.user_id else None,
                patient_id=patient_id,
                action=action,
                resource_type="Consent",
                resource_id=ref,
                purpose=principal.purpose,
                outcome="allow",
                trace_id=TraceId(principal.trace_id),
            )
        )


async def _active_grant(
    uow: UnitOfWork, patient_id: PatientId, grantee_id: UserId, now: datetime
) -> GrantRow | None:
    row: GrantRow | None = (
        await uow.session.execute(
            select(GrantRow).where(
                GrantRow.patient_id == patient_id,
                GrantRow.grantee_id == grantee_id,
                GrantRow.status == "active",
            )
        )
    ).scalar()
    if row is not None and row.period_end is not None and row.period_end <= now:
        return None
    return row


async def _treating(
    uow: UnitOfWork, patient_id: PatientId, doctor_id: UserId, now: datetime
) -> bool:
    found = await uow.session.execute(
        select(CareRelationshipRow.id)
        .where(
            CareRelationshipRow.patient_id == patient_id,
            CareRelationshipRow.doctor_id == doctor_id,
            CareRelationshipRow.starts_at <= now,
            CareRelationshipRow.ends_at > now,
        )
        .limit(1)
    )
    return found.first() is not None


def _own(principal: Principal, patient_id: PatientId) -> None:
    """Only the patient changes or lists their grants; others learn nothing (CON-11)."""
    if (
        principal.kind is not PrincipalKind.USER
        or principal.role is not Role.PATIENT
        or principal.patient_scope != patient_id
    ):
        raise NotFound()


def _consent(row: GrantRow) -> Consent:
    return Consent(
        id=ResourceId(row.id),
        meta=Meta(version_id=row.version, last_updated=row.updated_at, source="consent"),
        patient_id=PatientId(row.patient_id),
        grantee_id=UserId(row.grantee_id),
        grantee_role=row.grantee_role,
        categories=frozenset(DataCategory(c) for c in row.categories),
        purposes=frozenset(row.purposes),
        period=Period(start=row.period_start, end=row.period_end),
        status=row.status,
        version=row.version,
    )
