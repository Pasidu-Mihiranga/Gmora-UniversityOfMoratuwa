"""HTTP routes for the `cases` module. Stubs until issue M10 implements them (W0.8)."""

from suwapath.contracts.enums import Role
from suwapath.platform.http.stubs import StubRoute, stub_router

ROUTES = [
    StubRoute("POST", "/cases", (Role.PATIENT,), "Open a case with the first message", "M10"),
    StubRoute("GET", "/cases", (Role.PATIENT, Role.GUARDIAN, Role.DOCTOR), "My cases", "M10"),
    StubRoute(
        "GET", "/cases/{case_id}", (Role.PATIENT, Role.GUARDIAN, Role.DOCTOR), "Case state", "M10"
    ),
    StubRoute(
        "GET",
        "/cases/{case_id}/timeline",
        (Role.PATIENT, Role.GUARDIAN, Role.DOCTOR),
        "Case timeline",
        "M10",
    ),
    StubRoute("POST", "/cases/{case_id}/messages", (Role.PATIENT,), "Send a message", "M10"),
    StubRoute(
        "GET",
        "/cases/{case_id}/stream",
        (Role.PATIENT, Role.GUARDIAN, Role.DOCTOR),
        "Case events (SSE)",
        "M10",
    ),
]

router = stub_router("cases", ROUTES)
