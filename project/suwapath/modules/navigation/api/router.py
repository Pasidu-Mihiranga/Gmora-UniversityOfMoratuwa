"""HTTP routes for the `navigation` module. Stubs until issue M17 implements them (W0.8)."""

from suwapath.contracts.enums import Role
from suwapath.platform.http.stubs import ALL_ROLES, StubRoute, stub_router

ROUTES = [
    StubRoute("GET", "/specialties", ALL_ROLES, "Specialty catalog", "M17"),
    StubRoute("GET", "/doctors", (Role.PATIENT, Role.GUARDIAN), "Search doctors", "M17"),
    StubRoute("POST", "/trips/plan", (Role.PATIENT,), "Plan several doctors in one trip", "M17"),
]

router = stub_router("navigation", ROUTES)
