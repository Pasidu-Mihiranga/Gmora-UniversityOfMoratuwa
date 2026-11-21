"""HTTP routes for the `private` module. Stubs until issue PC1 implements them (W0.8)."""

from suwapath.contracts.enums import Role
from suwapath.platform.http.stubs import StubRoute, stub_router

ROUTES = [
    StubRoute("POST", "/private/sessions", (Role.PATIENT,), "Start a private consult", "PC1"),
    StubRoute("POST", "/private/sessions/{session_id}/turns", (Role.PATIENT,), "One turn", "PC1"),
    StubRoute(
        "PUT",
        "/private/sessions/{session_id}/messages",
        (Role.PATIENT,),
        "Store encrypted history",
        "PC1",
    ),
    StubRoute(
        "GET",
        "/private/sessions/{session_id}/messages",
        (Role.PATIENT,),
        "Encrypted history",
        "PC1",
    ),
    StubRoute("DELETE", "/private/sessions/{session_id}", (Role.PATIENT,), "Delete now", "PC1"),
]

router = stub_router("private", ROUTES)
