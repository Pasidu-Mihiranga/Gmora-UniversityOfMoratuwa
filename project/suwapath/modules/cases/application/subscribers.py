"""How the case reacts to other modules (EVT-2) and to its own approval timer (spec 03 section 6).

Each handler runs inside the dispatcher's unit of work. A refused transition is logged by the
aggregate and skipped: the event is still consumed, because retrying cannot make it valid.
"""

from __future__ import annotations

from uuid import UUID

from suwapath.contracts.cases import CaseEventDraft, CaseEventKind
from suwapath.contracts.enums import CaseStatus
from suwapath.contracts.errors import InvalidTransition, NotFound
from suwapath.contracts.events import EventEnvelope
from suwapath.contracts.events.catalog import AppointmentBooked, BudgetExceeded, TimerFired
from suwapath.contracts.ids import CaseId
from suwapath.modules.cases.application.service import APPROVAL_TIMER, SqlCaseService
from suwapath.platform.events.subscribers import SubscriberRegistry
from suwapath.platform.uow import UnitOfWork, UnitOfWorkFactory

K = CaseEventKind


class CaseSubscribers:
    def __init__(self, service: SqlCaseService, uow_factory: UnitOfWorkFactory) -> None:
        self._service = service
        self._uow = uow_factory

    async def record(self, case_id: CaseId, draft: CaseEventDraft) -> bool:
        """Append one system event at the current version. False when the case refuses it."""
        try:
            async with self._uow.join() as uow:
                case = await self._service.load(uow, case_id)
                await self._service.append_events(uow, case, [draft], None)
        except (InvalidTransition, NotFound):
            return False
        return True

    async def on_timer(self, envelope: EventEnvelope, uow: UnitOfWork) -> None:
        payload = envelope.payload
        assert isinstance(payload, TimerFired)
        if payload.kind != APPROVAL_TIMER:
            return
        case_id = CaseId(UUID(payload.ref_id))
        try:
            case = await self._service.load(uow, case_id)
        except NotFound:
            return
        pending = case.state.pending_approval
        if case.state.status is not CaseStatus.AWAITING_DOCTOR or pending is None:
            return  # the doctor answered in time
        await self.record(
            case_id,
            CaseEventDraft(
                kind=K.APPROVAL_TIMED_OUT,
                data={"draft_id": str(pending.draft_id)},
                actor="system",
            ),
        )

    async def on_appointment_booked(self, envelope: EventEnvelope, uow: UnitOfWork) -> None:
        payload = envelope.payload
        assert isinstance(payload, AppointmentBooked)
        if payload.case_id is None:
            return
        await self.record(
            payload.case_id,
            CaseEventDraft(
                kind=K.APPOINTMENT_BOOKED,
                data={"appointment_id": str(payload.appointment_id)},
                actor="booking",
            ),
        )

    async def on_budget_exceeded(self, envelope: EventEnvelope, uow: UnitOfWork) -> None:
        payload = envelope.payload
        assert isinstance(payload, BudgetExceeded)
        if payload.scope != "case" or payload.case_id is None:
            return
        await self.record(
            payload.case_id,
            CaseEventDraft(kind=K.HANDED_TO_HUMAN, data={"reason": "budget"}, actor="ops"),
        )

    def register(self, subscribers: SubscriberRegistry) -> None:
        subscribers.add("platform.timer_fired", self.on_timer, name="cases.approval_timeout")
        subscribers.add(
            "booking.appointment_booked",
            self.on_appointment_booked,
            name="cases.on_appointment_booked",
        )
        subscribers.add(
            "ops.budget_exceeded", self.on_budget_exceeded, name="cases.on_budget_exceeded"
        )
