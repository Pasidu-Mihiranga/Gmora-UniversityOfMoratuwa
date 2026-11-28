"""The case stream (API-20, API-21, SEC-5, SEC-12): wakes on commit only, replays missed items,
filters by access on every wake, closes when access ends, and opens only with a valid ticket."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

import httpx
import pytest

from suwapath.contracts.cases import CaseEventDraft, CaseEventKind
from suwapath.contracts.common import UntrustedText
from suwapath.contracts.enums import AgentKind, DataCategory, Language, PrincipalKind, Purpose, Role
from suwapath.contracts.errors import QuotaExceeded
from suwapath.contracts.ids import CaseId, PatientId, TraceId, UserId
from suwapath.contracts.ports.identity import UserAccount
from suwapath.contracts.principal import Principal
from suwapath.contracts.testing.doubles_core import InMemoryAccessPolicy, InMemoryIdentityService
from suwapath.modules.cases.api.router import build_router
from suwapath.modules.cases.application.service import SqlCaseService
from suwapath.modules.cases.application.stream import case_events
from suwapath.platform.config import Settings
from suwapath.platform.http.app import create_app
from suwapath.platform.http.streams import MAX_STREAMS_PER_USER, StreamHub, notify_stream
from suwapath.platform.uow import UnitOfWorkFactory

pytestmark = pytest.mark.integration
TRACE = TraceId("5" * 32)


class Family:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self.patient, self.amma, self.son = PatientId(uuid4()), UserId(uuid4()), UserId(uuid4())
        self.policy = InMemoryAccessPolicy(
            patients_by_user={self.amma: self.patient},
            guardian_grants={(self.son, self.patient): frozenset({DataCategory.ADHERENCE})},
            user_roles={self.amma: Role.PATIENT, self.son: Role.GUARDIAN},
            agent_scopes={AgentKind.GUARDIAN_AGENT.value: frozenset({DataCategory.ADHERENCE})},
        )
        self.cases = SqlCaseService(uow_factory, lambda: self.policy)

    def user(self, user_id: UserId, role: Role, purpose: Purpose) -> Principal:
        return Principal(
            kind=PrincipalKind.USER,
            user_id=user_id,
            role=role,
            acting_for=user_id,
            patient_scope=self.patient if role is Role.PATIENT else None,
            purpose=purpose,
            trace_id=TRACE,
        )

    @property
    def amma_view(self) -> Principal:
        return self.user(self.amma, Role.PATIENT, Purpose.CARE_NAVIGATION)

    @property
    def son_view(self) -> Principal:
        return self.user(self.son, Role.GUARDIAN, Purpose.GUARDIAN_MONITORING)

    async def open(self, text: str = "I feel dizzy") -> CaseId:
        state = await self.cases.open(
            self.amma_view,
            self.patient,
            UntrustedText(value=text, source="patient_message"),
            Language.EN,
        )
        return state.id

    async def says(self, case_id: CaseId, text: str) -> None:
        await self.cases.add_patient_message(
            self.amma_view, case_id, UntrustedText(value=text, source="patient_message")
        )


@pytest.fixture
async def hub(database_url: str) -> AsyncIterator[StreamHub]:
    hub = StreamHub(database_url, heartbeat_seconds=0.3)
    yield hub
    await hub.close()


@pytest.fixture
def family(uow_factory: UnitOfWorkFactory) -> Family:
    return Family(uow_factory)


async def _never() -> bool:
    return False


def _parse(chunk: str) -> tuple[str | None, str | None, Any]:
    if chunk.startswith(":"):
        return None, "heartbeat", None
    fields = dict(line.split(": ", 1) for line in chunk.strip().split("\n"))
    return fields.get("id"), fields["event"], json.loads(fields["data"])


async def _next(events: AsyncIterator[str]) -> tuple[str | None, str | None, Any]:
    return _parse(await asyncio.wait_for(anext(events), timeout=5))


# ----- the hub --------------------------------------------------------------------------------


async def test_a_write_wakes_streams_only_when_it_commits(
    hub: StreamHub, uow_factory: UnitOfWorkFactory
) -> None:
    sub = await hub.subscribe("case:x", UserId(uuid4()))
    async with uow_factory() as uow:
        await notify_stream(uow, "case:x")  # rolled back: nobody is woken
    assert not await sub.wait()
    async with uow_factory() as uow:
        await notify_stream(uow, "case:x")
        await uow.commit()
    assert await sub.wait()
    sub.close()


async def test_five_open_streams_per_user(hub: StreamHub) -> None:
    user = UserId(uuid4())
    subs = [await hub.subscribe(f"case:{n}", user) for n in range(MAX_STREAMS_PER_USER)]
    with pytest.raises(QuotaExceeded):
        await hub.subscribe("case:extra", user)
    subs[0].close()
    (await hub.subscribe("case:again", user)).close()


# ----- one viewer's case stream -----------------------------------------------------------------


async def test_new_messages_arrive_live_with_their_ids(hub: StreamHub, family: Family) -> None:
    case_id = await family.open()
    sub = await hub.subscribe(f"case:{case_id}", family.amma)
    events = case_events(family.cases, family.amma_view, case_id, sub, None, _never)
    assert (await _next(events))[1] == "case.updated"  # no replay without Last-Event-ID
    await family.says(case_id, "since this morning")
    event_id, kind, data = await _next(events)
    assert kind == "case.timeline_appended"
    assert data["params"]["text"] == "since this morning"
    assert event_id is not None and int(event_id) > 0
    kind, data = (await _next(events))[1:]
    assert kind == "case.updated" and data["version"] == 2
    assert (await _next(events))[1] == "heartbeat"  # quiet: keep the connection alive
    await events.aclose()
    assert sub not in hub._subs[f"case:{case_id}"]  # closing frees the slot


async def test_reconnecting_with_last_event_id_replays_what_was_missed(
    hub: StreamHub, family: Family
) -> None:
    case_id = await family.open("one")
    first = (await family.cases.timeline(family.amma_view, case_id, None, 10)).items
    assert len(first) == 1
    _, _, position = await family.cases.timeline_since(family.amma_view, case_id, None)
    await family.says(case_id, "two")
    await family.says(case_id, "three")
    sub = await hub.subscribe(f"case:{case_id}", family.amma)
    events = case_events(family.cases, family.amma_view, case_id, sub, position, _never)
    texts = [(await _next(events))[2]["params"]["text"] for _ in range(2)]
    assert texts == ["two", "three"]
    assert (await _next(events))[1] == "case.updated"
    await events.aclose()


async def test_the_guardian_sees_only_his_items_and_loses_the_stream_on_revocation(
    hub: StreamHub, family: Family
) -> None:
    state = await family.cases.open(
        Principal(
            kind=PrincipalKind.AGENT,
            agent_kind=AgentKind.GUARDIAN_AGENT,
            acting_for=family.son,
            patient_scope=family.patient,
            purpose=Purpose.GUARDIAN_MONITORING,
            trace_id=TRACE,
        ),
        family.patient,
        UntrustedText(value="missed_doses", source="system"),
        Language.EN,
    )
    case_id = state.id
    sub = await hub.subscribe(f"case:{case_id}", family.son)
    events = case_events(family.cases, family.son_view, case_id, sub, None, _never)
    assert (await _next(events))[1] == "case.updated"
    await family.says(case_id, "I am fine")  # chat: not for the guardian
    assert (await _next(events))[1] == "case.updated"  # woken, but nothing he may see
    current = await family.cases.get(family.amma_view, case_id)
    await family.cases.append(
        family.amma_view,
        case_id,
        [
            CaseEventDraft(
                kind=CaseEventKind.GUARDIAN_UPDATED,
                data={"status": "alerted", "last_alert_level": "info"},
                actor="guardian_agent",
            )
        ],
        current.version,
    )
    kind, data = (await _next(events))[1:]
    assert kind == "case.timeline_appended" and data["summary_key"] == "case.guardian_updated"
    assert (await _next(events))[1] == "case.updated"
    family.policy.guardian_grants[(family.son, family.patient)] = frozenset()  # Amma revokes
    await family.says(case_id, "one more")
    kind, data = (await _next(events))[1:]
    assert (kind, data) == ("stream.closed", {"reason": "access_ended"})
    with pytest.raises(StopAsyncIteration):
        await anext(events)


# ----- the route: tickets (SEC-5) ---------------------------------------------------------------


async def test_the_stream_opens_only_with_a_ticket_for_it(hub: StreamHub, family: Family) -> None:
    case_id = await family.open()
    identity = InMemoryIdentityService()
    account = UserAccount(
        id=family.amma,
        role=Role.PATIENT,
        display_name="A",
        language=Language.EN,
        patient_id=family.patient,
    )
    identity.add_user(account, "amma@example.test", "pw")
    app = create_app(
        Settings(_env_file=None),
        ticket_redeemer=identity.redeem_stream_ticket,
        stream_hub=hub,
        routers=[build_router(family.cases)],
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        path = f"/api/v1/cases/{case_id}/stream"
        assert (await client.get(path)).status_code == 401  # no ticket
        assert (await client.get(f"{path}?ticket=made-up")).status_code == 401
        other = await identity.issue_stream_ticket(family.amma, f"case:{uuid4()}")
        assert (
            await client.get(f"{path}?ticket={other.ticket}")
        ).status_code == 401  # other stream
        stranger_case = CaseId(uuid4())
        ticket = await identity.issue_stream_ticket(family.amma, f"case:{stranger_case}")
        response = await client.get(f"/api/v1/cases/{stranger_case}/stream?ticket={ticket.ticket}")
        assert response.status_code == 404  # unknown case: 404 before any stream opens
