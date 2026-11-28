"""SEC-5 on the identity double: one use, 60 seconds, bound to one stream."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from suwapath.contracts.enums import Language, Role
from suwapath.contracts.errors import AccessDenied
from suwapath.contracts.ids import UserId
from suwapath.contracts.ports.identity import UserAccount
from suwapath.contracts.testing.doubles_core import InMemoryIdentityService

NOW = datetime(2026, 11, 28, 9, 0, tzinfo=UTC)


def _service() -> tuple[InMemoryIdentityService, UserAccount, list[datetime]]:
    clock = [NOW]
    service = InMemoryIdentityService(now=lambda: clock[0])
    user = UserAccount(
        id=UserId(uuid4()), role=Role.PATIENT, display_name="A", language=Language.SI
    )
    service.add_user(user, "a@example.test", "pw")
    return service, user, clock


async def test_a_ticket_opens_its_stream_once() -> None:
    service, user, _ = _service()
    ticket = await service.issue_stream_ticket(user.id, "case:1")
    assert (await service.redeem_stream_ticket(ticket.ticket, "case:1")).id == user.id
    with pytest.raises(AccessDenied):
        await service.redeem_stream_ticket(ticket.ticket, "case:1")


async def test_a_ticket_is_bound_to_its_stream_and_spent_by_a_wrong_try() -> None:
    service, user, _ = _service()
    ticket = await service.issue_stream_ticket(user.id, "case:1")
    with pytest.raises(AccessDenied):
        await service.redeem_stream_ticket(ticket.ticket, "case:2")
    with pytest.raises(AccessDenied):
        await service.redeem_stream_ticket(ticket.ticket, "case:1")


async def test_a_ticket_expires_after_a_minute() -> None:
    service, user, clock = _service()
    ticket = await service.issue_stream_ticket(user.id, "me")
    clock[0] = NOW + timedelta(seconds=61)
    with pytest.raises(AccessDenied):
        await service.redeem_stream_ticket(ticket.ticket, "me")
