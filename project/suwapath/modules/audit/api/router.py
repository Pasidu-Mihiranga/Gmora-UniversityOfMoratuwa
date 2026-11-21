"""HTTP routes for the `audit` module. Stubs until issue M02 implements them (W0.8)."""

from suwapath.contracts.enums import Role
from suwapath.platform.http.stubs import StubRoute, stub_router

ROUTES = [
    StubRoute("GET", "/audit", (Role.SYSTEM_ADMIN,), "Search the audit log", "M02"),
]

router = stub_router("audit", ROUTES)
