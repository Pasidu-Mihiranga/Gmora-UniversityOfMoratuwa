"""The agent directory: who is who for the agents (spec 05 section 2), built from other modules'
ports. It returns IDs and the few patient basics the patient's own agents need, never record
contents. Ports are resolved on each call because modules register in any order."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from suwapath.contracts.enums import Language
from suwapath.contracts.errors import NotFound
from suwapath.contracts.ids import CaseId, PatientId, PractitionerId, PractitionerRoleId, UserId
from suwapath.modules.agents.application.context import PatientProfile


class PortDirectory:
    def __init__(self, ports: Callable[[], Mapping[str, Any]]) -> None:
        self._ports = ports

    def _port(self, name: str) -> Any:
        return self._ports()[name]

    async def patient_user(self, patient_id: PatientId) -> UserId:
        account = await self._port("identity").user_for_patient(patient_id)
        if account is None:
            raise NotFound()
        return UserId(account.id)

    async def guardians(self, patient_id: PatientId) -> list[UserId]:
        return list(await self._port("consent").guardians_of(patient_id))

    async def case_patient(self, case_id: CaseId) -> PatientId:
        return PatientId(await self._port("cases").patient_of(case_id))

    async def language(self, patient_id: PatientId) -> Language:
        # The patient's own setting first, then the record, then Sinhala (the default audience).
        account = await self._port("identity").user_for_patient(patient_id)
        if account is not None:
            return Language(account.language)
        basics = await self._port("record").patient_basics(patient_id)
        return Language(basics.language) if basics is not None else Language.SI

    async def practitioner_user(self, practitioner_role_id: PractitionerRoleId) -> UserId | None:
        practitioner = await self._port("booking").practitioner_for_role(practitioner_role_id)
        return await self._user_of(practitioner) if practitioner is not None else None

    async def doctor_case(self, patient_id: PatientId) -> tuple[CaseId, UserId] | None:
        booked = await self._port("booking").booked_case(patient_id)
        if booked is None:
            return None
        case_id, practitioner = booked
        doctor = await self._user_of(practitioner)
        return (case_id, doctor) if doctor is not None else None

    async def profile(self, patient_id: PatientId) -> PatientProfile | None:
        basics = await self._port("record").patient_basics(patient_id)
        if basics is None:
            return None
        return PatientProfile(
            first_name=basics.first_name,
            age_years=basics.age_years,
            sex=basics.sex,
            chronic_codes=basics.condition_codes,
            home=basics.home,
        )

    async def practitioner_of(self, user_id: UserId) -> PractitionerId | None:
        try:
            account = await self._port("identity").get_user(user_id)
        except NotFound:
            return None
        return PractitionerId(account.practitioner_id) if account.practitioner_id else None

    async def _user_of(self, practitioner: PractitionerId) -> UserId | None:
        account = await self._port("identity").user_for_practitioner(practitioner)
        return UserId(account.id) if account is not None else None
