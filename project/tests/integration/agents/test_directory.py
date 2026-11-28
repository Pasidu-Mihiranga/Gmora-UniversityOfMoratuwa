"""The agent directory on the seeded world: who is who for the agents, from the real modules."""

from __future__ import annotations

import base64
import os
from collections.abc import AsyncIterator
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from suwapath.bootstrap import Container, build_container, seed_world
from suwapath.contracts.common import UntrustedText
from suwapath.contracts.enums import Language, Purpose
from suwapath.contracts.errors import NotFound
from suwapath.contracts.ids import CaseId, PatientId, PractitionerRoleId, UserId
from suwapath.contracts.principal import Principal
from suwapath.modules.booking.adapters.models import PractitionerRoleRow
from suwapath.platform.config import Settings

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


async def _sign_in(world: Container, login: str) -> Principal:
    identity = world.ports["identity"]
    tokens = await identity.authenticate(login, PASSWORD, "127.0.0.1")
    user = await identity.verify_access_token(tokens.access_token)
    return Principal(
        kind="user",
        user_id=user.id,
        role=user.role,
        acting_for=user.id,
        patient_scope=user.patient_id,
        purpose=Purpose.SELF_ACCESS,
        trace_id="1" * 32,
    )


async def test_amma_her_family_and_her_profile(world: Container) -> None:
    directory = world.ports["agent_directory"]
    amma = await _sign_in(world, "amma@demo.suwapath.lk")
    nimal = await _sign_in(world, "nimal@demo.suwapath.lk")
    patient = PatientId(amma.patient_scope)
    assert await directory.patient_user(patient) == amma.user_id
    assert nimal.user_id in await directory.guardians(patient)
    assert await directory.language(patient) in (Language.SI, Language.EN)
    profile = await directory.profile(patient)
    assert profile is not None and profile.first_name == "Kamala"
    assert profile.age_years and profile.age_years > 60 and profile.chronic_codes
    assert await directory.practitioner_of(amma.user_id) is None  # a patient is not a doctor
    with pytest.raises(NotFound):
        await directory.patient_user(PatientId(uuid4()))
    assert await directory.profile(PatientId(uuid4())) is None


async def test_the_booked_doctor_is_found_for_the_case(world: Container) -> None:
    directory, identity, booking = (
        world.ports["agent_directory"],
        world.ports["identity"],
        world.ports["booking"],
    )
    amma = await _sign_in(world, "amma@demo.suwapath.lk")
    patient = PatientId(amma.patient_scope)
    assert await directory.doctor_case(patient) is None  # nothing booked yet
    case = await world.ports["cases"].open(
        amma, patient, UntrustedText(value="My knee hurts", source="patient_message"), Language.EN
    )
    assert await directory.case_patient(case.id) == patient
    async with world.uow_factory() as uow:
        roles = list(
            await uow.session.execute(
                select(PractitionerRoleRow.id, PractitionerRoleRow.practitioner_id)
            )
        )
    role, doctor = None, None
    for role_id, practitioner_id in roles:  # a doctor who has a demo account
        doctor = await identity.user_for_practitioner(practitioner_id)
        if doctor is not None:
            role = PractitionerRoleId(role_id)
            break
    assert role is not None and doctor is not None
    assert await directory.practitioner_user(role) == UserId(doctor.id)
    assert await directory.practitioner_of(UserId(doctor.id)) == doctor.practitioner_id
    today = world.clock.now().date()
    slot = next(s for s in await booking.slots(role, today, today + timedelta(days=14)) if s.free)
    hold = await booking.hold(amma, slot.slot_id, case.id)
    await booking.confirm(amma, hold.id, "directory-test")
    assert await directory.doctor_case(patient) == (CaseId(case.id), UserId(doctor.id))
