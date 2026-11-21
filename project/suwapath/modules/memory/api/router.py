"""HTTP routes for the `memory` module. Stubs until issue ME01 implements them (W0.8)."""

from suwapath.contracts.enums import Role
from suwapath.platform.http.stubs import StubRoute, stub_router

ROUTES = [
    StubRoute(
        "GET",
        "/memory",
        (Role.PATIENT, Role.GUARDIAN, Role.DOCTOR),
        "What is remembered for me",
        "ME01",
    ),
    StubRoute(
        "POST",
        "/memory/{item_id}/decision",
        (Role.PATIENT, Role.GUARDIAN, Role.DOCTOR),
        "Confirm or decline",
        "ME01",
    ),
    StubRoute(
        "DELETE",
        "/memory/{item_id}",
        (Role.PATIENT, Role.GUARDIAN, Role.DOCTOR),
        "Forget an item",
        "ME01",
    ),
]

router = stub_router("memory", ROUTES)
