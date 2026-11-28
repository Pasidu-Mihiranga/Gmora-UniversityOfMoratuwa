"""Navigation routes (spec 10, M17): the doctor-type catalog, doctor search and trip planning.
The engine itself is used through its port by the companion, not over HTTP."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from suwapath.contracts.common import Page
from suwapath.contracts.enums import Language, Purpose, Role, Specialty
from suwapath.contracts.fhir.base import GeoPoint
from suwapath.contracts.ports.navigation import DoctorOption, DoctorQuery, Itinerary
from suwapath.contracts.principal import Principal
from suwapath.modules.navigation.application.service import KbNavigationService
from suwapath.platform.http.auth import require
from suwapath.platform.http.stubs import ALL_ROLES

Anyone = Annotated[Principal, Depends(require(*ALL_ROLES))]
Searcher = Annotated[
    Principal,
    Depends(
        require(
            Role.PATIENT,
            Role.GUARDIAN,
            purposes={Role.PATIENT: Purpose.BOOKING, Role.GUARDIAN: Purpose.GUARDIAN_MONITORING},
        )
    ),
]
Patient = Annotated[Principal, Depends(require(Role.PATIENT, purpose=Purpose.BOOKING))]


class TripRequest(BaseModel):
    specialties: list[Specialty] = Field(min_length=2, max_length=3)
    near: GeoPoint
    date_from: date
    date_to: date


def build_router(service: KbNavigationService) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["navigation"])

    @router.get(
        "/specialties", summary="Specialty catalog", operation_id="navigation_get_specialties"
    )
    async def specialties(principal: Anyone) -> list[Specialty]:
        return list(Specialty)

    @router.get("/doctors", summary="Search doctors", operation_id="navigation_get_doctors")
    async def doctors(
        principal: Searcher,
        specialty: Specialty,
        lat: Annotated[float, Query(ge=5.5, le=10.0)],
        lng: Annotated[float, Query(ge=79.0, le=82.5)],
        date_from: date,
        date_to: date,
        language: Language | None = None,
        time_of_day: Literal["morning", "afternoon", "evening", "any"] = "any",
        max_km: Annotated[float, Query(gt=0, le=100)] = 25,
        name: Annotated[str | None, Query(max_length=80)] = None,
        cursor: Annotated[str | None, Query(max_length=10)] = None,
        limit: Annotated[int, Query(ge=1, le=20)] = 10,
    ) -> Page[DoctorOption]:
        query = DoctorQuery(
            specialty=specialty,
            near=GeoPoint(lat=lat, lng=lng),
            date_from=date_from,
            date_to=date_to,
            language=language,
            time_of_day=time_of_day,
            max_km=max_km,
            name=name,
        )
        return await service.find_doctors(query, cursor, limit)

    @router.post(
        "/trips/plan",
        summary="Plan several doctors in one trip",
        operation_id="navigation_post_trips_plan",
    )
    async def plan(body: TripRequest, principal: Patient) -> list[Itinerary]:
        return await service.plan_trip(body.specialties, body.near, body.date_from, body.date_to)

    return router
