"""ADR-0029: a service joins the caller's unit of work, or commits its own when none is open."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import func, select

from suwapath.contracts.events.catalog import TimerFired
from suwapath.contracts.ids import TimerId
from suwapath.platform.models import OutboxRow
from suwapath.platform.uow import UnitOfWorkFactory

pytestmark = pytest.mark.integration


def _event() -> TimerFired:
    return TimerFired(timer_id=TimerId(uuid4()), kind="test", ref_type="test", ref_id="r1")


async def _outbox_count(uow_factory: UnitOfWorkFactory) -> int:
    async with uow_factory() as uow:
        return int(
            (await uow.session.execute(select(func.count()).select_from(OutboxRow))).scalar_one()
        )


async def _service_write(uow_factory: UnitOfWorkFactory, fail: bool = False) -> None:
    async with uow_factory.join() as uow:
        uow.publish(_event(), aggregate_id="service")
        await uow.session.flush()
        if fail:
            raise RuntimeError("service failed")


async def test_without_a_caller_the_service_commits_its_own(
    uow_factory: UnitOfWorkFactory,
) -> None:
    await _service_write(uow_factory)
    assert await _outbox_count(uow_factory) == 1


async def test_joined_writes_commit_with_the_caller(uow_factory: UnitOfWorkFactory) -> None:
    async with uow_factory() as uow:
        uow.publish(_event(), aggregate_id="caller")
        await _service_write(uow_factory)
        await uow.commit()
    assert await _outbox_count(uow_factory) == 2


async def test_joined_writes_roll_back_with_the_caller(uow_factory: UnitOfWorkFactory) -> None:
    async with uow_factory() as uow:
        uow.publish(_event(), aggregate_id="caller")
        await _service_write(uow_factory)
        # leaving without commit
        assert uow.joinable
    assert await _outbox_count(uow_factory) == 0


async def test_a_failed_joined_block_rolls_back_only_itself(
    uow_factory: UnitOfWorkFactory,
) -> None:
    async with uow_factory() as uow:
        uow.publish(_event(), aggregate_id="caller")
        await uow.session.flush()
        with pytest.raises(RuntimeError):
            await _service_write(uow_factory, fail=True)
        await uow.commit()
    assert await _outbox_count(uow_factory) == 1


async def test_after_commit_a_new_call_does_not_join(uow_factory: UnitOfWorkFactory) -> None:
    async with uow_factory() as uow:
        await uow.commit()
        assert not uow.joinable
        await _service_write(uow_factory)
    assert await _outbox_count(uow_factory) == 1
