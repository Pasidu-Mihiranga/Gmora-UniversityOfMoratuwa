"""Persisting and sending agent messages (AGT-10 to AGT-13).

Every message is stored once under a deterministic dedupe key, so a job retried after a crash
cannot send the same message twice. Requests schedule their deadline timer; an answer cancels it.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert

from suwapath.contracts.agents import INTENTS, AgentMessage, AgentMessageDraft, MessageIntent
from suwapath.contracts.enums import AgentKind, DataCategory, MessageKind, MessageStatus
from suwapath.contracts.events.catalog import AgentMessageSent
from suwapath.contracts.ids import CaseId, MessageId, TraceId, UserId
from suwapath.modules.agents.adapters.models import AgentMessageRow
from suwapath.platform.uow import UnitOfWork

_NAMESPACE = uuid.UUID("5b0c1f8e-6d8e-4c5a-9c55-2f6d1f4a7e10")
DEADLINE_TIMER = "agent_request_deadline"


def message_id_for(dedupe_key: str) -> MessageId:
    """Deterministic id: the same logical message always gets the same id."""
    return MessageId(uuid.uuid5(_NAMESPACE, dedupe_key))


def to_message(row: AgentMessageRow) -> AgentMessage:
    return AgentMessage(
        message_id=MessageId(row.id),
        conversation_id=MessageId(row.conversation_id),
        in_reply_to=MessageId(row.in_reply_to) if row.in_reply_to else None,
        case_id=CaseId(row.case_id),
        kind=MessageKind(row.kind),
        intent=MessageIntent(row.intent),
        sender=AgentKind(row.sender),
        recipient=row.recipient,
        acting_for=UserId(row.acting_for) if row.acting_for else None,
        consent_scope=frozenset(DataCategory(c) for c in row.consent_scope),
        body=json.loads(row.body_enc.decode()),  # encrypted with the patient key once M04 lands
        hop=row.hop,
        revision=row.revision,
        deadline=row.deadline,
        trace_id=TraceId(row.trace_id),
        status=MessageStatus(row.status),
        simulated=row.simulated,
    )


async def load(uow: UnitOfWork, message_id: MessageId) -> AgentMessage | None:
    row = (
        await uow.session.execute(select(AgentMessageRow).where(AgentMessageRow.id == message_id))
    ).scalar()
    return to_message(row) if row else None


async def pending_request(
    uow: UnitOfWork, case_id: CaseId, intent: MessageIntent
) -> AgentMessage | None:
    """The latest delivered request of this intent in the case that has no answer yet."""
    answered = select(AgentMessageRow.in_reply_to).where(AgentMessageRow.in_reply_to.is_not(None))
    row = (
        await uow.session.execute(
            select(AgentMessageRow)
            .where(
                AgentMessageRow.case_id == case_id,
                AgentMessageRow.intent == intent.value,
                AgentMessageRow.status == MessageStatus.DELIVERED.value,
                AgentMessageRow.id.not_in(answered),
            )
            .order_by(AgentMessageRow.created_at.desc())
            .limit(1)
        )
    ).scalar()
    return to_message(row) if row else None


async def has_answer(uow: UnitOfWork, request_id: MessageId) -> bool:
    found = await uow.session.execute(
        select(AgentMessageRow.id).where(AgentMessageRow.in_reply_to == request_id)
    )
    return found.first() is not None


async def send(
    uow: UnitOfWork,
    draft: AgentMessageDraft,
    *,
    sender: AgentKind,
    acting_for: UserId | None,
    case_id: CaseId,
    dedupe_key: str,
    trace_id: TraceId,
    now: datetime,
    simulated: bool = False,
) -> AgentMessage | None:
    """Store, publish and (for requests) schedule the deadline. None when it was already sent."""
    spec = INTENTS[draft.intent]
    parent = await load(uow, draft.in_reply_to) if draft.in_reply_to else None
    message_id = message_id_for(dedupe_key)
    message = AgentMessage(
        message_id=message_id,
        conversation_id=parent.conversation_id if parent else message_id,
        in_reply_to=draft.in_reply_to,
        case_id=case_id,
        kind=spec.kind,
        intent=draft.intent,
        sender=sender,
        recipient=draft.recipient,
        acting_for=acting_for,
        consent_scope=draft.consent_scope,
        body=draft.body,
        quoted=draft.quoted,
        hop=(parent.hop + 1) if parent else 0,
        deadline=now + spec.default_deadline if spec.default_deadline else None,
        trace_id=trace_id,
        simulated=simulated,
    )
    inserted = await uow.session.execute(
        insert(AgentMessageRow)
        .values(
            id=message.message_id,
            conversation_id=message.conversation_id,
            in_reply_to=message.in_reply_to,
            case_id=case_id,
            intent=message.intent.value,
            kind=message.kind.value,
            sender=sender.value,
            recipient=message.recipient,
            acting_for=acting_for,
            consent_scope=sorted(c.value for c in message.consent_scope),
            body_enc=json.dumps(message.body, sort_keys=True).encode(),
            status=MessageStatus.DRAFTED.value,
            dedupe_key=dedupe_key,
            hop=message.hop,
            revision=message.revision,
            deadline=message.deadline,
            trace_id=trace_id,
            simulated=simulated,
        )
        .on_conflict_do_nothing(index_elements=["dedupe_key"])
        .returning(AgentMessageRow.id)
    )
    if inserted.scalar_one_or_none() is None:
        return None
    uow.publish(
        AgentMessageSent(
            message_id=message.message_id,
            kind=message.kind,
            from_agent=sender,
            to=message.recipient,
            case_id=case_id,
        ),
        aggregate_id=str(message.message_id),
        correlation_id=case_id,
    )
    if message.deadline is not None:
        await uow.schedule_timer(
            kind=DEADLINE_TIMER,
            due_at=message.deadline,
            ref_type="agent_message",
            ref_id=str(message.message_id),
            dedupe_key=f"deadline:{message.message_id}",
            payload={"trace_id": trace_id, "sender": sender.value},
        )
    if parent is not None and parent.kind is MessageKind.REQUEST:
        await uow.cancel_timer(f"deadline:{parent.message_id}")
    return message


async def mark(
    uow: UnitOfWork,
    message_id: MessageId,
    status: MessageStatus,
    only_from: tuple[MessageStatus, ...],
) -> bool:
    """Move a message's status only from the given states; True when this call made the change."""
    changed = await uow.session.execute(
        update(AgentMessageRow)
        .where(
            AgentMessageRow.id == message_id,
            AgentMessageRow.status.in_([s.value for s in only_from]),
        )
        .values(status=status.value)
        .returning(AgentMessageRow.id)
    )
    return changed.scalar_one_or_none() is not None


async def of_intent(uow: UnitOfWork, case_id: CaseId, intent: MessageIntent) -> list[AgentMessage]:
    rows = (
        await uow.session.execute(
            select(AgentMessageRow)
            .where(AgentMessageRow.case_id == case_id, AgentMessageRow.intent == intent.value)
            .order_by(AgentMessageRow.created_at)
        )
    ).scalars()
    return [to_message(r) for r in rows]
