"""HTTP routes for the `identity` module. Stubs until issue M01 implements them (W0.8)."""

from suwapath.platform.http.stubs import ALL_ROLES, StubRoute, stub_router

ROUTES = [
    StubRoute("POST", "/auth/login", (), "Sign in", "M01"),
    StubRoute(
        "POST", "/auth/refresh", (), "Rotate the refresh token (cookie, CSRF checked)", "M01"
    ),
    StubRoute("POST", "/auth/logout", ALL_ROLES, "Sign out", "M01"),
    StubRoute("GET", "/me", ALL_ROLES, "My profile", "M01"),
    StubRoute("PATCH", "/me", ALL_ROLES, "Update my profile", "M01"),
    StubRoute("POST", "/stream-tickets", ALL_ROLES, "One-time SSE ticket", "M01"),
]

router = stub_router("identity", ROUTES)
