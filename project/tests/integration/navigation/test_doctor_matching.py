"""Doctor matching on the seeded world (M17): the real booking and record directory, ranked by
navigation, and the `/doctors` route."""

from __future__ import annotations

import base64
import os
from collections.abc import AsyncIterator
from datetime import timedelta

import httpx
import pytest

from suwapath.bootstrap import Container, build_container, seed_world
from suwapath.contracts.enums import Specialty
from suwapath.contracts.ids import PatientId
from suwapath.contracts.ports.navigation import DoctorQuery
from suwapath.platform.config import Settings
from suwapath.platform.http.app import create_app

pytestmark = pytest.mark.integration
PASSWORD = "synthetic demo password"


@pytest.fixture
async def world(database_url: str, db: object) -> AsyncIterator[Container]:
    settings = Settings(
        _env_file=None,
        suwapath_env="test",
        database_url=database_url,
        demo_password=PASSWORD,
        jwt_secret="test-only-secret-not-used-anywhere-0123456789",
        suwapath_master_key=base64.b64encode(os.urandom(32)).decode(),
    )
    built = build_container(settings)
    await seed_world(built)
    yield built
    await built.db.dispose()


async def _amma_home(world: Container) -> object:
    identity = world.ports["identity"]
    tokens = await identity.authenticate("amma@demo.suwapath.lk", PASSWORD, "127.0.0.1")
    amma = await identity.verify_access_token(tokens.access_token)
    profile = await world.ports["agent_directory"].profile(PatientId(amma.patient_id))
    assert profile is not None and profile.home is not None
    return profile.home


async def test_general_physicians_near_amma_ranked_by_the_earliest_slot(world: Container) -> None:
    home = await _amma_home(world)
    today = world.clock.now().date()
    query = DoctorQuery(
        specialty=Specialty.GENERAL_MEDICINE,
        near=home,  # type: ignore[arg-type]
        date_from=today,
        date_to=today + timedelta(days=13),
    )
    page = await world.ports["navigation"].find_doctors(query, None, 10)
    assert page.items, "the seed has general physicians near Amma"
    assert all(d.specialty is Specialty.GENERAL_MEDICINE for d in page.items)
    assert all(d.display_name and d.next_slot_id and d.distance_km <= 25 for d in page.items)
    starts = [d.next_slot_at for d in page.items]
    assert starts == sorted(starts)  # no continuity here: the earliest slot first (NAV-10)
    first = page.items[0]
    named = await world.ports["navigation"].find_doctors(
        query.model_copy(update={"name": first.display_name}), None, 10
    )
    assert named.items and named.items[0].practitioner_id == first.practitioner_id  # NAV-13


async def test_the_doctors_route_answers_a_signed_in_patient(world: Container) -> None:
    home = await _amma_home(world)
    identity = world.ports["identity"]
    tokens = await identity.authenticate("amma@demo.suwapath.lk", PASSWORD, "127.0.0.1")
    app = create_app(
        world.settings, token_verifier=identity.verify_access_token, routers=world.routers
    )
    today = world.clock.now().date()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        response = await client.get(
            "/api/v1/doctors",
            params={
                "specialty": "general_medicine",
                "lat": home.lat,  # type: ignore[attr-defined]
                "lng": home.lng,  # type: ignore[attr-defined]
                "date_from": today.isoformat(),
                "date_to": (today + timedelta(days=13)).isoformat(),
                "limit": 3,
            },
            headers={"Authorization": f"Bearer {tokens.access_token}"},
        )
        catalog = await client.get(
            "/api/v1/specialties", headers={"Authorization": f"Bearer {tokens.access_token}"}
        )
    assert response.status_code == 200 and 1 <= len(response.json()["items"]) <= 3
    assert "commission" not in response.text  # no commercial field reaches the patient
    assert "general_medicine" in catalog.json()
