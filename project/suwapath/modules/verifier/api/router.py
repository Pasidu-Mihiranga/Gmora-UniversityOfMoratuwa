"""HTTP routes for the `verifier` module. Stubs until issue M16 implements them (W0.8)."""

from suwapath.contracts.enums import Role
from suwapath.platform.http.stubs import StubRoute, stub_router

ROUTES = [
    StubRoute(
        "GET", "/reviews", (Role.PATIENT, Role.DOCTOR), "Messages waiting for my review", "M16"
    ),
    StubRoute(
        "POST", "/reviews/{review_id}", (Role.PATIENT, Role.DOCTOR), "Send, edit or discard", "M16"
    ),
]

router = stub_router("verifier", ROUTES)
