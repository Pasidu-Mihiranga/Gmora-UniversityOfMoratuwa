"""Storage for doctor briefs and doctor notes (API-23, A05). Contents are encrypted with the
patient key once M04 lands; until then they are stored as bytes, like agent messages."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select

from suwapath.contracts.briefs import Brief
from suwapath.contracts.ids import CaseId, MessageId, PatientId, ResourceId, UserId
from suwapath.modules.agents.adapters.models import BriefRow, DoctorNoteRow
from suwapath.platform.ids import new_id
from suwapath.platform.uow import UnitOfWork


async def next_brief_version(uow: UnitOfWork, case_id: CaseId) -> int:
    current = await uow.session.execute(
        select(func.max(BriefRow.version)).where(BriefRow.case_id == case_id)
    )
    return int(current.scalar() or 0) + 1


async def save_brief(uow: UnitOfWork, brief: Brief, built_for: UserId) -> None:
    uow.session.add(
        BriefRow(
            id=brief.id,
            case_id=brief.case_id,
            patient_id=brief.patient_id,
            built_for=built_for,
            version=brief.version,
            content_enc=brief.model_dump_json().encode(),
            built_at=brief.built_at,
        )
    )
    await uow.session.flush()


async def latest_brief(uow: UnitOfWork, case_id: CaseId, built_for: UserId) -> Brief | None:
    row = (
        await uow.session.execute(
            select(BriefRow.content_enc)
            .where(BriefRow.case_id == case_id, BriefRow.built_for == built_for)
            .order_by(BriefRow.version.desc())
            .limit(1)
        )
    ).scalar()
    return Brief.model_validate_json(row.decode()) if row else None


async def add_note(
    uow: UnitOfWork,
    *,
    case_id: CaseId,
    patient_id: PatientId,
    author_id: UserId,
    text: str,
    at: datetime,
    draft_id: MessageId | None = None,
) -> ResourceId:
    note_id = ResourceId(new_id())
    uow.session.add(
        DoctorNoteRow(
            id=note_id,
            case_id=case_id,
            patient_id=patient_id,
            author_id=author_id,
            draft_id=draft_id,
            text_enc=text.encode(),
            created_at=at,
        )
    )
    await uow.session.flush()
    return note_id


async def note_text(uow: UnitOfWork, note_id: UUID) -> str | None:
    row = (
        await uow.session.execute(select(DoctorNoteRow.text_enc).where(DoctorNoteRow.id == note_id))
    ).scalar()
    return row.decode() if row else None


async def edited_text(uow: UnitOfWork, draft_id: MessageId) -> str | None:
    """The doctor's edited wording for a draft, if they edited it before approving."""
    row = (
        await uow.session.execute(
            select(DoctorNoteRow.text_enc)
            .where(DoctorNoteRow.draft_id == draft_id)
            .order_by(DoctorNoteRow.created_at.desc())
            .limit(1)
        )
    ).scalar()
    return row.decode() if row else None
