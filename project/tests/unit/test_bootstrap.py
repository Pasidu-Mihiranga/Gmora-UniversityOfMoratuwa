"""The composition root builds the full API with a role on every route (W0.8, SG-1)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from suwapath.bootstrap import create_api
from suwapath.platform.config import Settings
from suwapath.platform.http.auth import route_role_table

DEV = Settings(_env_file=None, suwapath_env="development")


def test_full_api_builds_and_every_route_has_roles() -> None:
    table = route_role_table(create_api(DEV))
    assert len(table) > 80
    assert all(table.values())  # startup would already have refused otherwise
    assert table["POST /api/v1/auth/login"] == "public"
    assert table["GET /api/v1/audit"] == "system_admin"
    assert table["POST /api/v1/bookings/holds/{hold_id}/confirm"] == "patient"


def test_stubs_still_require_authentication_and_roles() -> None:
    client = TestClient(create_api(DEV))
    assert client.get("/api/v1/audit").status_code == 401
    assert client.get("/api/v1/doctor/queue").status_code == 401  # a stub still needs sign in
    # Login is implemented (M01): an empty body is refused by validation, not by a stub.
    assert client.post("/api/v1/auth/login").status_code == 422


def test_dev_routes_are_not_mounted_in_production() -> None:
    prod = Settings(
        _env_file=None,
        suwapath_env="production",
        jwt_secret="test-only-secret-not-used-anywhere-0123456789",
        suwapath_master_key="k",
    )
    table = route_role_table(create_api(prod))
    assert not any("/dev/" in route for route in table)
    assert any("/dev/clock" in route for route in route_role_table(create_api(DEV)))


def test_openapi_lists_path_parameters() -> None:
    schema = create_api(DEV).openapi()
    params = schema["paths"]["/api/v1/cases/{case_id}"]["get"]["parameters"]
    assert any(p["name"] == "case_id" and p["in"] == "path" for p in params)
