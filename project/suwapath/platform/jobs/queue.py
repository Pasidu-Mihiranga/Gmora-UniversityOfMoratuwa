"""Agent job queue (EVT-10 to EVT-12): SKIP LOCKED claims, leases, one running job per case."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal

from sqlalchemy import and_, exists, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import aliased

from suwapath.contracts.agents import AgentJob
from suwapath.contracts.enums import AgentKind, Purpose
from suwapath.contracts.ids import CaseId, EventId, JobId, PatientId, TraceId, UserId
from suwapath.platform.models import AgentJobRow
from suwapath.platform.uow import UnitOfWork, UnitOfWorkFactory

LEASE = timedelta(seconds=120)


def enqueue(uow: UnitOfWork, job: AgentJob, available_at: datetime | None = None) -> JobId:
    """Adds the job inside the caller's transaction (EVT-1)."""
    uow.session.add(
        AgentJobRow(
            id=job.id,
            agent_kind=job.agent_kind.value,
            case_id=job.case_id,
            patient_id=job.patient_id,
            trigger_event_id=job.trigger_event_id,
            acting_for=job.acting_for,
            purpose=job.purpose.value,
            input=job.input,
            available_at=available_at or uow.clock.now(),
            trace_id=job.trace_id,
        )
    )
    return job.id


async def claim(
    uow_factory: UnitOfWorkFactory, worker_id: str, kinds: set[AgentKind] | None = None
) -> AgentJob | None:
    """Claim the next runnable job or one with an expired lease; None when nothing is due."""
    async with uow_factory() as uow:
        now = uow.clock.now()
        running = aliased(AgentJobRow)
        sibling_running = exists().where(
            running.case_id == AgentJobRow.case_id,
            running.agent_kind == AgentJobRow.agent_kind,
            running.status == "running",
            running.id != AgentJobRow.id,
            running.lease_until >= now,
        )
        query = (
            select(AgentJobRow)
            .where(
                or_(
                    and_(AgentJobRow.status == "queued", AgentJobRow.available_at <= now),
                    and_(AgentJobRow.status == "running", AgentJobRow.lease_until < now),
                ),
                ~sibling_running,
            )
            .order_by(AgentJobRow.available_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if kinds:
            query = query.where(AgentJobRow.agent_kind.in_([k.value for k in kinds]))
        row = (await uow.session.execute(query)).scalars().first()
        if row is None:
            return None
        row.status = "running"
        row.locked_by = worker_id
        row.lease_until = now + LEASE
        row.attempts += 1
        row.updated_at = now
        try:
            await uow.commit()
        except IntegrityError:
            return None  # another worker started a job for the same case and agent first
        return _to_job(row)


async def renew_lease(uow_factory: UnitOfWorkFactory, job_id: JobId, worker_id: str) -> bool:
    async with uow_factory() as uow:
        now = uow.clock.now()
        result = await uow.session.execute(
            update(AgentJobRow)
            .where(
                AgentJobRow.id == job_id,
                AgentJobRow.locked_by == worker_id,
                AgentJobRow.status == "running",
            )
            .values(lease_until=now + LEASE, updated_at=now)
            .returning(AgentJobRow.id)
        )
        renewed = result.scalar_one_or_none() is not None
        await uow.commit()
        return renewed


async def finish(
    uow: UnitOfWork,
    job_id: JobId,
    status: Literal["done", "waiting", "failed", "queued"],
    *,
    error_code: str | None = None,
    retry_at: datetime | None = None,
) -> None:
    """Called inside the transaction that records the job's results."""
    values: dict[str, object] = {
        "status": status,
        "error_code": error_code,
        "lease_until": None,
        "updated_at": uow.clock.now(),
    }
    if retry_at is not None:
        values["available_at"] = retry_at
    await uow.session.execute(update(AgentJobRow).where(AgentJobRow.id == job_id).values(**values))


def _to_job(row: AgentJobRow) -> AgentJob:
    return AgentJob(
        id=JobId(row.id),
        agent_kind=AgentKind(row.agent_kind),
        case_id=CaseId(row.case_id) if row.case_id else None,
        patient_id=PatientId(row.patient_id) if row.patient_id else None,
        trigger_event_id=EventId(row.trigger_event_id) if row.trigger_event_id else None,
        acting_for=UserId(row.acting_for) if row.acting_for else None,
        purpose=Purpose(row.purpose),
        input=row.input,
        attempts=row.attempts,
        trace_id=TraceId(row.trace_id),
    )
