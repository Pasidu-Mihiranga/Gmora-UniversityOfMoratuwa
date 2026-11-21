"""HTTP routes for the `notifications` module. Stubs until issue M11 implements them (W0.8)."""

from suwapath.platform.http.stubs import ALL_ROLES, StubRoute, stub_router

ROUTES = [
    StubRoute("GET", "/notifications", ALL_ROLES, "My notifications", "M11"),
    StubRoute("POST", "/notifications/{notification_id}/read", ALL_ROLES, "Mark read", "M11"),
    StubRoute("POST", "/notifications/read-all", ALL_ROLES, "Mark all read", "M11"),
    StubRoute("GET", "/me/stream", ALL_ROLES, "My events (SSE)", "M11"),
]

router = stub_router("notifications", ROUTES)
