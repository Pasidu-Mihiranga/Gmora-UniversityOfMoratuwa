"""Dose checks (CARE-3 to CARE-5): a timer per dose; a missed dose is recorded and announced."""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from suwapath.contracts.enums import DoseSlot
from suwapath.contracts.events import EventEnvelope
from suwapath.contracts.events.catalog import DoseMissed, TimerFired
from suwapath.contracts.ids import PatientId, ResourceId
from suwapath.modules.care.adapters.models import MedicationAdministrationRow
from suwapath.platform.ids import new_id
from suwapath.platform.models import TimerRow
from suwapath.platform.tracing import current_trace_id
from suwapath.platform.uow import UnitOfWork

DOSE_TIMER = "dose_check"
GRACE = timedelta(minutes=60)


async def schedule_dose_check(
    uow: UnitOfWork,
    *,
    patient_id: PatientId,
    statement_id: ResourceId,
    scheduled_at: datetime,
    slot: DoseSlot | None,
) -> bool:
    return await uow.schedule_timer(
        kind=DOSE_TIMER,
        due_at=scheduled_at + GRACE,
        ref_type="dose",
        ref_id=str(statement_id),
        dedupe_key=f"dose:{statement_id}:{scheduled_at.isoformat()}",
        payload={
            "patient_id": str(patient_id),
            "scheduled_at": scheduled_at.isoformat(),
            "slot": slot.value if slot else None,
            "trace_id": current_trace_id(),
        },
    )


async def on_timer(envelope: EventEnvelope, uow: UnitOfWork) -> None:
    payload = envelope.payload
    assert isinstance(payload, TimerFired)
    if payload.kind != DOSE_TIMER:
        return
    timer = (
        await uow.session.execute(select(TimerRow).where(TimerRow.id == payload.timer_id))
    ).scalar_one()
    patient_id = PatientId(UUID(timer.payload["patient_id"]))
    statement_id = ResourceId(UUID(payload.ref_id))
    scheduled_at = datetime.fromisoformat(timer.payload["scheduled_at"])
    slot = DoseSlot(timer.payload["slot"]) if timer.payload.get("slot") else None
    administration_id = ResourceId(new_id())
    recorded = await uow.session.execute(
        insert(MedicationAdministrationRow)
        .values(
            id=administration_id,
            patient_id=patient_id,
            medication_statement_id=statement_id,
            scheduled_at=scheduled_at,
            slot=slot.value if slot else None,
            status="not-done",
            status_reason="missed",
            recorded_at=uow.clock.now(),
            recorded_by="timer",
        )
        .on_conflict_do_nothing(index_elements=["medication_statement_id", "scheduled_at"])
        .returning(MedicationAdministrationRow.id)
    )
    if recorded.scalar_one_or_none() is None:
        return  # taken or already recorded (CARE-4): nothing missed
    uow.publish(
        DoseMissed(
            administration_id=administration_id,
            statement_id=statement_id,
            slot=slot,
            scheduled_at=scheduled_at,
            patient_id=patient_id,
        ),
        aggregate_id=str(administration_id),
        patient_id=patient_id,
    )


async def missed_doses(uow: UnitOfWork, patient_id: PatientId, window: timedelta) -> int:
    since = uow.clock.now() - window
    count = await uow.session.execute(
        select(func.count())
        .select_from(MedicationAdministrationRow)
        .where(
            MedicationAdministrationRow.patient_id == patient_id,
            MedicationAdministrationRow.status == "not-done",
            MedicationAdministrationRow.scheduled_at >= since,
        )
    )
    return int(count.scalar_one())
