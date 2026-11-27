from typing import Annotated
from uuid import uuid4

import pytest
from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field

from suwapath.contracts.enums import Language, Purpose, Role
from suwapath.contracts.ids import PatientId, UserId
from suwapath.contracts.ports.identity import UserAccount
from suwapath.contracts.principal import Principal
from suwapath.platform.config import Settings
from suwapath.platform.http.app import create_app
from suwapath.platform.http.auth import public, require, require_csrf, route_role_table
from suwapath.platform.http.ratelimit import RateLimiter

PATIENT = UserAccount(
    id=UserId(uuid4()),
    role=Role.PATIENT,
    display_name="P",
    language=Language.SI,
    patient_id=PatientId(uuid4()),
)
DOCTOR = UserAccount(id=UserId(uuid4()), role=Role.DOCTOR, display_name="D", language=Language.EN)
TOKENS = {"patient-token": PATIENT, "doctor-token": DOCTOR}


async def verifier(token: str) -> UserAccount:
    return TOKENS[token]


PatientOnly = Annotated[Principal, Depends(require(Role.PATIENT))]
Shared = Annotated[
    Principal,
    Depends(require(Role.PATIENT, Role.DOCTOR, purposes={Role.DOCTOR: Purpose.TREATMENT})),
]


class Note(BaseModel):
    text: str = Field(max_length=20)


def _router() -> APIRouter:
    router = APIRouter(prefix="/api/v1/test")

    @router.get("/mine")
    async def mine(principal: PatientOnly) -> dict[str, str]:
        return {"scope": str(principal.patient_scope)}

    @router.get("/purpose")
    async def purpose(principal: Shared) -> dict[str, str]:
        return {"purpose": principal.purpose.value}

    @router.post("/note")
    async def note(body: Note, _: PatientOnly) -> dict[str, int]:
        return {"length": len(body.text)}

    @router.post("/refresh", dependencies=[Depends(public()), Depends(require_csrf)])
    async def refresh() -> dict[str, bool]:
        return {"ok": True}

    return router


SETTINGS = Settings(_env_file=None, web_origins="http://localhost:5173")


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(SETTINGS, token_verifier=verifier, routers=[_router()]))


def test_startup_refuses_routes_without_roles() -> None:
    bare = APIRouter(prefix="/api/v1/bare")

    @bare.get("/open")
    async def open_route() -> dict[str, bool]:
        return {"ok": True}

    with pytest.raises(RuntimeError, match="GET /api/v1/bare/open"):
        create_app(SETTINGS, token_verifier=verifier, routers=[bare])


def test_route_role_table_lists_every_route() -> None:
    table = route_role_table(create_app(SETTINGS, token_verifier=verifier, routers=[_router()]))
    assert table["GET /api/v1/test/mine"] == "patient"
    assert table["POST /api/v1/test/refresh"] == "public"
    assert table["GET /api/v1/health/live"] == "public"


def test_missing_token_is_401_and_wrong_role_is_403(client: TestClient) -> None:
    assert client.get("/api/v1/test/mine").status_code == 401
    response = client.get("/api/v1/test/mine", headers={"Authorization": "Bearer doctor-token"})
    assert response.status_code == 403
    assert response.headers["content-type"] == "application/problem+json"


def test_principal_is_built_from_the_token(client: TestClient) -> None:
    response = client.get("/api/v1/test/mine", headers={"Authorization": "Bearer patient-token"})
    assert response.json() == {"scope": str(PATIENT.patient_id)}


def test_security_headers_and_no_store(client: TestClient) -> None:
    response = client.get("/api/v1/test/mine", headers={"Authorization": "Bearer patient-token"})
    for header in (
        "x-trace-id",
        "content-security-policy",
        "x-content-type-options",
        "referrer-policy",
        "x-frame-options",
    ):
        assert header in response.headers
    assert response.headers["cache-control"] == "no-store"
    assert "no-store" not in client.get("/api/v1/health/live").headers.get("cache-control", "")


def test_validation_errors_never_echo_input(client: TestClient) -> None:
    secret = "my HIV result is positive and long"
    response = client.post(
        "/api/v1/test/note",
        json={"text": secret},
        headers={"Authorization": "Bearer patient-token"},
    )
    assert response.status_code == 422
    assert "HIV" not in response.text
    assert response.json()["code"] == "validation_failed"


def test_oversized_body_is_rejected(client: TestClient) -> None:
    response = client.post(
        "/api/v1/test/note",
        content=b"x" * (300 * 1024),
        headers={"Authorization": "Bearer patient-token", "content-type": "application/json"},
    )
    assert response.status_code == 413


def test_refresh_requires_csrf_header_and_known_origin(client: TestClient) -> None:
    assert client.post("/api/v1/test/refresh").status_code == 403
    ok = client.post(
        "/api/v1/test/refresh",
        headers={"x-suwapath-request": "1", "origin": "http://localhost:5173"},
    )
    assert ok.status_code == 200
    bad_origin = client.post(
        "/api/v1/test/refresh",
        headers={"x-suwapath-request": "1", "origin": "https://evil.example"},
    )
    assert bad_origin.status_code == 403


def test_rate_limiter_refills_over_time() -> None:
    limiter = RateLimiter({"auth": 2, "chat": 1, "search": 1, "upload": 1, "default": 1})
    assert limiter.allow("auth", "ip", now=0.0)
    assert limiter.allow("auth", "ip", now=0.0)
    assert not limiter.allow("auth", "ip", now=0.0)
    assert limiter.allow("auth", "ip", now=30.0)


def test_a_shared_route_sets_the_purpose_per_role(client: TestClient) -> None:
    def purpose(token: str) -> str:
        response = client.get("/api/v1/test/purpose", headers={"Authorization": f"Bearer {token}"})
        return str(response.json()["purpose"])

    assert purpose("patient-token") == "self_access"
    assert purpose("doctor-token") == "treatment"
