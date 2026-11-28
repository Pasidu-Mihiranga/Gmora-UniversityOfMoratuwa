"""Test doubles for the clinical-side ports (QA-1). Behaviour that consumers rely on is real:
versions and conflicts on cases, no double booking, safety stops, consent filtering on reads."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal
from uuid import uuid4

from suwapath.contracts.agents import AgentMessage
from suwapath.contracts.cases import CaseEvent, CaseEventDraft, CaseEventKind, CaseState
from suwapath.contracts.common import Page, UntrustedText
from suwapath.contracts.enums import (
    AppointmentStatus,
    CaseStatus,
    DataCategory,
    DecidedBy,
    Intent,
    Language,
    PrincipalKind,
    Purpose,
    Urgency,
    VerdictKind,
    max_urgency,
)
from suwapath.contracts.errors import (
    AccessDenied,
    InvalidTransition,
    NotFound,
    SafetyStop,
    SlotUnavailable,
    VersionConflict,
)
from suwapath.contracts.fhir.base import Meta, PatientResource
from suwapath.contracts.fhir.resources import (
    AllergyIntolerance,
    Appointment,
    Condition,
    MedicationStatement,
    Observation,
    Patient,
    Provenance,
)
from suwapath.contracts.ids import (
    AppointmentId,
    CaseId,
    EventId,
    HoldId,
    OrganizationId,
    PatientId,
    PractitionerRoleId,
    ResourceId,
    ScheduleId,
    SlotId,
    UserId,
)
from suwapath.contracts.ports.booking import Hold, HospitalAppointmentRow, SessionSummary, SlotView
from suwapath.contracts.ports.cases import TimelineItem
from suwapath.contracts.ports.consent import AccessPolicy, AccessRequest
from suwapath.contracts.ports.knowledge import (
    CachedAnswer,
    IntentResult,
    IntentSlots,
    RetrievedChunk,
)
from suwapath.contracts.ports.record import RecordSummary
from suwapath.contracts.ports.safety import (
    Clash,
    PatientContext,
    RedFlagMatch,
    SafetyAssessment,
    WordingHit,
)
from suwapath.contracts.ports.verifier import (
    CheckResult,
    ReviewItem,
    Verdict,
    VerificationContext,
)
from suwapath.contracts.principal import Principal

Now = Callable[[], datetime]


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _owns(principal: Principal, patient_id: PatientId) -> bool:
    return principal.kind is not PrincipalKind.SYSTEM and principal.patient_scope in (
        None,
        patient_id,
    )


# ----- Cases ------------------------------------------------------------------------------------


@dataclass
class InMemoryCaseService:
    """Event-sourced in memory: append checks the expected version and closed cases (CON-10)."""

    now: Now = _utc_now
    states: dict[CaseId, CaseState] = field(default_factory=dict)
    events: dict[CaseId, list[CaseEvent]] = field(default_factory=dict)
    communications: list[tuple[CaseId, str, str]] = field(default_factory=list)

    async def open(
        self,
        principal: Principal,
        patient_id: PatientId,
        first_message: UntrustedText,
        language: Language,
    ) -> CaseState:
        if not _owns(principal, patient_id):
            raise NotFound()
        case_id = CaseId(uuid4())
        self.states[case_id] = CaseState(
            id=case_id, patient_id=patient_id, status=CaseStatus.OPEN, language=language, version=0
        )
        self.events[case_id] = []
        return await self.append(
            principal, case_id, [CaseEventDraft(kind=CaseEventKind.CASE_OPENED, actor="patient")], 0
        )

    async def add_patient_message(
        self, principal: Principal, case_id: CaseId, message: UntrustedText
    ) -> ResourceId:
        await self.get(principal, case_id)
        self.communications.append((case_id, "patient", message.value))
        return ResourceId(uuid4())

    async def get(self, principal: Principal, case_id: CaseId) -> CaseState:
        state = self.states.get(case_id)
        if state is None or not _owns(principal, state.patient_id):
            raise NotFound()
        return state

    async def append(
        self,
        principal: Principal,
        case_id: CaseId,
        drafts: list[CaseEventDraft],
        expected_version: int,
    ) -> CaseState:
        state = await self.get(principal, case_id)
        if state.version != expected_version:
            raise VersionConflict()
        for draft in drafts:
            if state.status is CaseStatus.CLOSED:
                raise InvalidTransition("case is closed")
            state = _apply(state, draft)
            self.events[case_id].append(
                CaseEvent(
                    event_id=EventId(uuid4()),
                    case_id=case_id,
                    version=state.version,
                    kind=draft.kind,
                    data=draft.data,
                    actor=draft.actor,
                    at=self.now(),
                    simulated=draft.simulated,
                )
            )
        self.states[case_id] = state
        return state

    async def timeline(
        self, principal: Principal, case_id: CaseId, cursor: str | None, limit: int
    ) -> Page[TimelineItem]:
        await self.get(principal, case_id)
        items = [
            TimelineItem(
                id=str(e.event_id),
                case_id=case_id,
                at=e.at,
                kind="event",
                actor=e.actor,
                summary_key=f"case.{e.kind.value}",
                params={},
                simulated=e.simulated,
            )
            for e in self.events[case_id]
        ]
        return Page(items=items[:limit], next_cursor=None)

    async def deliver_communication(
        self, case_id: CaseId, recipient: str, payload: str, simulated: bool
    ) -> ResourceId:
        self.communications.append((case_id, recipient, payload))
        return ResourceId(uuid4())


_STATUS_BY_EVENT: dict[CaseEventKind, CaseStatus] = {
    CaseEventKind.INTAKE_STARTED: CaseStatus.INTAKE,
    CaseEventKind.URGENCY_ASSESSED: CaseStatus.ASSESSED,
    CaseEventKind.RED_FLAG_RAISED: CaseStatus.EMERGENCY,
    CaseEventKind.APPOINTMENT_BOOKED: CaseStatus.IN_CARE,
    CaseEventKind.DOCTOR_APPROVAL_REQUESTED: CaseStatus.AWAITING_DOCTOR,
    CaseEventKind.APPROVAL_TIMED_OUT: CaseStatus.IN_CARE,
    CaseEventKind.REPLY_DELIVERED: CaseStatus.REPLIED,
    CaseEventKind.HANDED_TO_HUMAN: CaseStatus.HANDED_TO_HUMAN,
    CaseEventKind.CASE_CLOSED: CaseStatus.CLOSED,
}


def _apply(state: CaseState, draft: CaseEventDraft) -> CaseState:
    update: dict[str, Any] = {"version": state.version + 1}
    leaves_emergency = draft.kind in (CaseEventKind.CASE_CLOSED, CaseEventKind.HANDED_TO_HUMAN)
    if (status := _STATUS_BY_EVENT.get(draft.kind)) is not None and (
        state.status is not CaseStatus.EMERGENCY or leaves_emergency
    ):
        update["status"] = status
    if draft.kind is CaseEventKind.RED_FLAG_RAISED:
        update["status"] = CaseStatus.EMERGENCY
        update["urgency"] = Urgency.EMERGENCY
        update["urgency_floor"] = Urgency.EMERGENCY
    if draft.kind is CaseEventKind.URGENCY_ASSESSED and "urgency" in draft.data:
        assessed = Urgency(draft.data["urgency"])
        floor = Urgency(draft.data["floor"]) if draft.data.get("floor") else None
        update["urgency"] = max_urgency(assessed, floor, state.urgency_floor)
        update["urgency_floor"] = max_urgency(floor, state.urgency_floor)
    return state.model_copy(update=update)


# ----- Booking ----------------------------------------------------------------------------------


@dataclass
class InMemoryBookingService:
    """BOOK-2 to BOOK-5: holds, person-only confirmation, idempotency, no double booking."""

    now: Now = _utc_now
    slot_store: dict[SlotId, SlotView] = field(default_factory=dict)
    holds: dict[HoldId, Hold] = field(default_factory=dict)
    appointment_store: dict[AppointmentId, Appointment] = field(default_factory=dict)
    idempotency: dict[str, AppointmentId] = field(default_factory=dict)
    emergency_cases: set[CaseId] = field(default_factory=set)
    hold_minutes: int = 5

    def add_slot(
        self, practitioner_role_id: PractitionerRoleId, start: datetime, number: int = 1
    ) -> SlotId:
        slot_id = SlotId(uuid4())
        self.slot_store[slot_id] = SlotView(
            slot_id=slot_id,
            schedule_id=ScheduleId(uuid4()),
            practitioner_role_id=practitioner_role_id,
            number=number,
            start=start,
            free=True,
        )
        return slot_id

    def _taken(self, slot_id: SlotId) -> bool:
        now = self.now()
        held = any(h.slot_id == slot_id and h.expires_at > now for h in self.holds.values())
        booked = any(
            a.slot_id == slot_id and a.status is AppointmentStatus.BOOKED
            for a in self.appointment_store.values()
        )
        return held or booked

    async def slots(
        self, practitioner_role_id: PractitionerRoleId, date_from: date, date_to: date
    ) -> list[SlotView]:
        return [
            s.model_copy(update={"free": not self._taken(s.slot_id)})
            for s in self.slot_store.values()
            if s.practitioner_role_id == practitioner_role_id
            and date_from <= s.start.date() <= date_to
        ]

    async def hold(
        self, principal: Principal, slot_id: SlotId, case_id: CaseId | None, sensitive: bool = False
    ) -> Hold:
        if case_id is not None and case_id in self.emergency_cases:
            raise SafetyStop("Booking is stopped during an emergency")
        if principal.patient_scope is None:
            raise AccessDenied()
        if slot_id not in self.slot_store or self._taken(slot_id):
            raise SlotUnavailable("That slot was just taken")
        hold = Hold(
            id=HoldId(uuid4()),
            slot_id=slot_id,
            patient_id=principal.patient_scope,
            case_id=case_id,
            expires_at=self.now() + timedelta(minutes=self.hold_minutes),
        )
        self.holds[hold.id] = hold
        return hold

    async def confirm(
        self, principal: Principal, hold_id: HoldId, idempotency_key: str
    ) -> Appointment:
        if principal.kind is not PrincipalKind.USER:
            raise AccessDenied("Only the patient can confirm a booking")
        if idempotency_key in self.idempotency:
            return self.appointment_store[self.idempotency[idempotency_key]]
        hold = self.holds.get(hold_id)
        if (
            hold is None
            or hold.patient_id != principal.patient_scope
            or hold.expires_at <= self.now()
        ):
            raise NotFound()
        if hold.case_id is not None and hold.case_id in self.emergency_cases:
            raise SafetyStop("Booking is stopped during an emergency")
        slot = self.slot_store[hold.slot_id]
        appointment = Appointment(
            id=ResourceId(uuid4()),
            meta=Meta(last_updated=self.now(), source="booking"),
            patient_id=hold.patient_id,
            slot_id=slot.slot_id,
            practitioner_role_id=slot.practitioner_role_id,
            organization_id=OrganizationId(uuid4()),
            start=slot.start,
            status=AppointmentStatus.BOOKED,
            case_id=hold.case_id,
        )
        del self.holds[hold_id]
        key = AppointmentId(appointment.id)
        self.appointment_store[key] = appointment
        self.idempotency[idempotency_key] = key
        return appointment

    async def cancel(self, principal: Principal, appointment_id: AppointmentId) -> Appointment:
        appointment = self.appointment_store.get(appointment_id)
        if appointment is None or appointment.patient_id != principal.patient_scope:
            raise NotFound()
        cancelled = appointment.model_copy(update={"status": AppointmentStatus.CANCELLED})
        self.appointment_store[appointment_id] = cancelled
        return cancelled

    async def appointments(
        self, principal: Principal, patient_id: PatientId, cursor: str | None, limit: int
    ) -> Page[Appointment]:
        if not _owns(principal, patient_id):
            raise NotFound()
        return Page(
            items=[a for a in self.appointment_store.values() if a.patient_id == patient_id][:limit]
        )

    async def practitioner_for_role(self, practitioner_role_id: object) -> None:
        return None

    async def booked_case(self, patient_id: object) -> None:
        return None

    async def release_holds_for_case(self, case_id: CaseId) -> int:
        released = [h for h, hold in self.holds.items() if hold.case_id == case_id]
        for h in released:
            del self.holds[h]
        self.emergency_cases.add(case_id)
        return len(released)

    async def hospital_sessions(
        self, principal: Principal, organization_id: OrganizationId, day: date
    ) -> list[SessionSummary]:
        return []

    async def hospital_session_appointments(
        self, principal: Principal, schedule_id: ScheduleId
    ) -> list[HospitalAppointmentRow]:
        return []


# ----- Safety -----------------------------------------------------------------------------------


@dataclass
class KeywordSafetyService:
    """Tiny keyword rules so consumers can test both paths; the real lexicon lives in `safety`."""

    rules: dict[str, tuple[str, Urgency, frozenset[str]]] = field(
        default_factory=lambda: {
            "chest pain": ("RF-CARD-001", Urgency.EMERGENCY, frozenset({"chest_pain"})),
            "face weak": ("RF-NEURO-001", Urgency.EMERGENCY, frozenset({"facial_weakness"})),
            "dizzy": ("RF-GER-003", Urgency.URGENT_24H, frozenset({"dizziness"})),
        }
    )

    def assess(
        self, text: UntrustedText, context: PatientContext, language: Language | None = None
    ) -> SafetyAssessment:
        lowered = text.value.lower()
        matches = []
        concepts: set[str] = set()
        for phrase, (rule_id, urgency, found) in self.rules.items():
            if phrase in lowered:
                if rule_id == "RF-GER-003" and (context.age_years or 0) < 65:
                    continue
                matches.append(
                    RedFlagMatch(
                        rule_id=rule_id,
                        label_key=rule_id,
                        urgency=urgency,
                        matched_concepts=tuple(sorted(found)),
                    )
                )
                concepts |= found
        floor = max_urgency(*(m.urgency for m in matches))
        emergency = floor is Urgency.EMERGENCY
        return SafetyAssessment(
            concepts=frozenset(concepts),
            red_flags=tuple(matches),
            urgency_floor=floor,
            emergency=emergency,
            help_lines=("1990",) if emergency else (),
            rules_version="test-1",
        )

    def check_wording(self, text: str, language: Language) -> list[WordingHit]:
        banned = {
            "you have": "diagnosis",
            "you are fine": "reassurance",
            "take two tablets": "dosing",
        }
        return [
            WordingHit(pattern_id=p, category=c, language=language)
            for p, c in banned.items()
            if p in text.lower()
        ]

    def check_clashes(
        self,
        medications: list[MedicationStatement],
        allergies: list[AllergyIntolerance],
        conditions: list[Condition],
    ) -> list[Clash]:
        return []


# ----- Verifier ---------------------------------------------------------------------------------


@dataclass
class ScriptedVerifier:
    """Returns a configured verdict; `rules_only=True` mimics VER-10 (escalate what passes)."""

    now: Now = _utc_now
    verdict: VerdictKind = VerdictKind.ALLOW
    rules_only: bool = False
    checked: list[AgentMessage] = field(default_factory=list)

    async def check(self, message: AgentMessage, context: VerificationContext) -> Verdict:
        self.checked.append(message)
        kind = (
            VerdictKind.ESCALATE
            if self.rules_only and self.verdict is VerdictKind.ALLOW
            else self.verdict
        )
        return Verdict(
            message_id=message.message_id,
            verdict=kind,
            decided_by=DecidedBy.RULES if self.rules_only else DecidedBy.MODEL,
            checks=(CheckResult(check="scripted", passed=kind is VerdictKind.ALLOW),),
            model_checks="not_run" if self.rules_only else "run",
            at=self.now(),
        )

    async def reviews(
        self, principal: Principal, cursor: str | None, limit: int
    ) -> Page[ReviewItem]:
        return Page(items=[])

    async def decide_review(
        self,
        principal: Principal,
        review_id: ResourceId,
        decision: Literal["send", "edit", "discard"],
        edited_text: str | None,
    ) -> Verdict:
        raise NotFound()


# ----- Record -----------------------------------------------------------------------------------


@dataclass
class InMemoryRecordService:
    """Stores resources and filters every read through the AccessPolicy (SEC-11)."""

    policy: AccessPolicy
    resources: dict[ResourceId, PatientResource] = field(default_factory=dict)
    patients_by_user: dict[UserId, PatientId] = field(default_factory=dict)

    def put(self, resource: PatientResource) -> None:
        self.resources[resource.id] = resource

    async def _allowed(
        self, principal: Principal, resource: PatientResource, action: Literal["read", "write"]
    ) -> bool:
        category = type(resource).category
        if category is None:
            return False
        decision = await self.policy.decide(
            AccessRequest(
                principal=principal,
                action=action,
                patient_id=resource.patient_id,
                category=category,
                resource_type=type(resource).resource_type,
                purpose=principal.purpose,
            )
        )
        return decision.allowed

    async def patient_for_user(self, user_id: UserId) -> PatientId | None:
        return self.patients_by_user.get(user_id)

    async def summary(
        self, principal: Principal, patient_id: PatientId, purpose: Purpose
    ) -> RecordSummary:
        visible = [
            r
            for r in self.resources.values()
            if r.patient_id == patient_id and await self._allowed(principal, r, "read")
        ]
        hidden = sum(1 for r in self.resources.values() if r.patient_id == patient_id) - len(
            visible
        )

        def of(kind: type) -> tuple[Any, ...]:
            return tuple(r for r in visible if isinstance(r, kind))

        patients = of(Patient)
        return RecordSummary(
            patient=patients[0] if patients else None,
            conditions=of(Condition),
            allergies=of(AllergyIntolerance),
            medications=of(MedicationStatement),
            recent_observations=of(Observation),
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
        items = [
            r
            for r in self.resources.values()
            if r.patient_id == patient_id
            and type(r).resource_type == resource_type
            and await self._allowed(principal, r, "read")
        ]
        return Page(items=items[:limit])

    async def get_resource(
        self, principal: Principal, resource_type: str, resource_id: ResourceId
    ) -> PatientResource:
        resource = self.resources.get(resource_id)
        if (
            resource is None
            or type(resource).resource_type != resource_type
            or not await self._allowed(principal, resource, "read")
        ):
            raise NotFound()
        return resource

    async def write_resource(
        self, principal: Principal, resource: PatientResource
    ) -> PatientResource:
        if not await self._allowed(principal, resource, "write"):
            raise AccessDenied()
        self.resources[resource.id] = resource
        return resource

    async def get_provenance(self, principal: Principal, provenance_id: ResourceId) -> Provenance:
        resource = self.resources.get(provenance_id)
        if not isinstance(resource, Provenance):
            raise NotFound()
        return resource

    async def export_fhir(self, principal: Principal, patient_id: PatientId) -> dict[str, Any]:
        page = [
            r
            for r in self.resources.values()
            if r.patient_id == patient_id and await self._allowed(principal, r, "read")
        ]
        return {
            "resourceType": "Bundle",
            "type": "collection",
            "entry": [{"resource": r.model_dump(mode="json")} for r in page],
        }

    async def destroy_patient_key(self, principal: Principal, patient_id: PatientId) -> None:
        self.resources = {k: v for k, v in self.resources.items() if v.patient_id != patient_id}


# ----- Knowledge and intent ---------------------------------------------------------------------


@dataclass
class KeywordIntentClassifier:
    keywords: dict[Intent, tuple[str, ...]] = field(
        default_factory=lambda: {
            Intent.BOOK_OR_FIND_DOCTOR: ("channel", "book", "appointment", "doctor today"),
            Intent.MEDICINE_TAKEN_OR_SCHEDULE: ("took my", "tablet", "medicine"),
            Intent.REPORT_EXPLANATION: ("report", "result"),
            Intent.GENERAL_HEALTH_QUESTION: ("what is", "what does"),
            Intent.EMERGENCY_OR_SYMPTOM: ("pain", "dizzy", "fever", "cough"),
        }
    )

    async def classify(self, text: UntrustedText, language: Language) -> IntentResult:
        lowered = text.value.lower()
        for intent, words in self.keywords.items():
            if any(w in lowered for w in words):
                return IntentResult(
                    intent=intent, confidence=0.8, slots=IntentSlots(), classifier="keywords"
                )
        return IntentResult(intent=Intent.UNSUPPORTED, confidence=0.0, classifier="keywords")


@dataclass
class InMemoryKnowledgeService:
    chunks: list[RetrievedChunk] = field(default_factory=list)

    async def retrieve(self, query: str, language: Language, k: int = 5) -> list[RetrievedChunk]:
        words = set(query.lower().split())
        scored = sorted(self.chunks, key=lambda c: -len(words & set(c.text.lower().split())))
        return [c for c in scored if c.language is language or c.language is Language.EN][:k]

    async def shared_lookup(
        self, question: str, language: Language, intent: Intent
    ) -> CachedAnswer | None:
        return None

    async def shared_store(
        self,
        question: str,
        language: Language,
        intent: Intent,
        intent_confidence: float,
        answer: str,
        cited_chunk_ids: list[str],
        verified: bool,
    ) -> bool:
        return False

    async def user_lookup(
        self, principal: Principal, patient_id: PatientId, question: str, language: Language
    ) -> CachedAnswer | None:
        return None

    async def user_store(
        self,
        principal: Principal,
        patient_id: PatientId,
        question: str,
        language: Language,
        answer: str,
        source_resource_versions: dict[str, int],
    ) -> None:
        return None


def medication(patient_id: PatientId, display: str, now: datetime) -> MedicationStatement:
    """Test helper: an active medication statement."""
    from suwapath.contracts.fhir.base import CodeableConcept
    from suwapath.contracts.fhir.resources import Dosage, DoseTiming

    return MedicationStatement(
        id=ResourceId(uuid4()),
        meta=Meta(last_updated=now, source="test"),
        patient_id=patient_id,
        medication=CodeableConcept(text=display),
        dosage=Dosage(timing=DoseTiming()),
        status="active",
    )


SCOPE_ALL_CLINICAL = frozenset(DataCategory) - {
    DataCategory.SENSITIVE_SEXUAL_HEALTH,
    DataCategory.SENSITIVE_MENTAL_HEALTH,
}
