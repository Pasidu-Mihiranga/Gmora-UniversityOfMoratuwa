"""HTTP routes for the `agents` module. Stubs until issue A05 implements them (W0.8)."""

from suwapath.contracts.enums import Role
from suwapath.platform.http.stubs import StubRoute, stub_router

ROUTES = [
    StubRoute("GET", "/doctor/queue", (Role.DOCTOR,), "Today's patients", "A05"),
    StubRoute("GET", "/doctor/cases/{case_id}/brief", (Role.DOCTOR,), "Sourced brief", "A05"),
    StubRoute(
        "POST", "/doctor/cases/{case_id}/notes", (Role.DOCTOR,), "Notes for the reply", "A05"
    ),
    StubRoute(
        "POST",
        "/doctor/cases/{case_id}/request-fact",
        (Role.DOCTOR,),
        "Ask for a missing fact",
        "A05",
    ),
    StubRoute("GET", "/doctor/approvals", (Role.DOCTOR,), "Drafts waiting for approval", "A05"),
    StubRoute(
        "POST", "/doctor/approvals/{draft_id}", (Role.DOCTOR,), "Approve, edit or reject", "A05"
    ),
]

router = stub_router("agents", ROUTES)
