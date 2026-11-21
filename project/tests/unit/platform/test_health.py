from fastapi.testclient import TestClient

from suwapath import __version__
from suwapath.platform.http.app import create_app


def test_live_reports_ok_and_version() -> None:
    client = TestClient(create_app())

    response = client.get("/api/v1/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__}


def test_openapi_is_served_under_api_prefix() -> None:
    client = TestClient(create_app())

    response = client.get("/api/v1/openapi.json")

    assert response.status_code == 200
    assert response.json()["info"]["title"] == "SuwaPath API"
