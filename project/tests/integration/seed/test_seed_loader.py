"""I01: the synthetic world loads through every module, is idempotent, and works end to end:
Amma signs in, her record and her son's grant are there, and the reset brings it all back."""

from __future__ import annotations

import base64
import os
from collections.abc import AsyncIterator

import httpx
import pytest

from suwapath.bootstrap import Container, build_container, reset_world, seed_world
from suwapath.contracts.enums import Language, Purpose, Role
from suwapath.contracts.errors import AccessDenied
from suwapath.contracts.principal import Principal
from suwapath.platform.config import Settings
from suwapath.platform.http.app import create_app

pytestmark = pytest.mark.integration
PASSWORD = "synthetic demo password"


@pytest.fixture
async def container(database_url: str, db: object) -> AsyncIterator[Container]:
    settings = Settings(
        _env_file=None,
        suwapath_env="test",
        database_url=database_url,
        demo_password=PASSWORD,
        jwt_secret="test-only-secret-not-used-anywhere-0123456789",
        suwapath_master_key=base64.b64encode(os.urandom(32)).decode(),
    )
    built = build_container(settings)
    yield built
    await built.db.dispose()


async def test_the_world_loads_once_with_amma_and_her_sons_grant(container: Container) -> None:
    first = await seed_world(container)
    assert first["doctors"] == 24 and first["record entries"] > 200 and first["past doses"] > 1000
    again = await seed_world(container)
    assert {k: v for k, v in again.items() if k != "accounts"} == {
        "doctors": 0,
        "sessions": 0,
        "sources": 0,
        "record entries": 0,
        "grants": 0,
        "past doses": 0,
    }
    identity, record, care = (
        container.ports["identity"],
        container.ports["record"],
        container.ports["care"],
    )
    tokens = await identity.authenticate("amma@demo.suwapath.lk", PASSWORD, "127.0.0.1")
    amma = await identity.verify_access_token(tokens.access_token)
    assert amma.display_name.startswith("Kamala") and amma.patient_id is not None
    me = Principal(
        kind="user",
        user_id=amma.id,
        role=amma.role,
        acting_for=amma.id,
        patient_scope=amma.patient_id,
        purpose=Purpose.SELF_ACCESS,
        trace_id="1" * 32,
    )
    summary = await record.summary(me, amma.patient_id, Purpose.SELF_ACCESS)
    names = {c.code.text or "" for c in summary.conditions}
    assert any("diabetes" in n.lower() for n in names)
    assert any("penicillin" in (a.code.text or "").lower() for a in summary.allergies)
    assert all(c.provenance_ids for c in summary.conditions)  # every fact has a source (CON-9)
    week = await care.adherence(me, amma.patient_id, 7)
    assert week.days  # her seeded dose history is visible to her
    # Her son's grant follows spec 16 section 2.
    consent = container.ports["consent"]
    tokens = await identity.authenticate("nimal@demo.suwapath.lk", PASSWORD, "127.0.0.1")
    nimal = await identity.verify_access_token(tokens.access_token)
    amma = await identity.verify_access_token(
        (await identity.authenticate("amma@demo.suwapath.lk", PASSWORD, "127.0.0.1")).access_token
    )
    assert nimal.id in await consent.guardians_of(amma.patient_id)
    me = Principal(
        kind="user",
        user_id=amma.id,
        role=amma.role,
        acting_for=amma.id,
        patient_scope=amma.patient_id,
        purpose=Purpose.SELF_ACCESS,
        trace_id="1" * 32,
    )
    grant = next(
        g for g in await consent.grants_for_patient(me, amma.patient_id) if g.grantee_id == nimal.id
    )
    assert {c.value for c in grant.categories} == {
        "medications",
        "adherence",
        "appointments",
    }  # not conversations


async def test_the_dev_reset_brings_the_world_back(container: Container) -> None:
    await seed_world(container)
    identity = container.ports["identity"]
    await identity.create_user(
        login="extra@example.test",
        password="long enough",
        role=Role.PATIENT,
        display_name="X",
        language=Language.EN,
    )
    counts = await reset_world(container)
    assert counts["doctors"] == 24
    with pytest.raises(AccessDenied):  # the extra account is gone
        await identity.authenticate("extra@example.test", "long enough", "127.0.0.1")
    app = create_app(
        container.settings, token_verifier=identity.verify_access_token, routers=container.routers
    )
    app.state.reset_world = lambda: reset_world(container)
    tokens = await identity.authenticate("ops@demo.suwapath.lk", PASSWORD, "127.0.0.1")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        response = await client.post(
            "/api/v1/dev/scenario/reset", headers={"Authorization": f"Bearer {tokens.access_token}"}
        )
    assert response.status_code == 200 and response.json()["sessions"] == 196
