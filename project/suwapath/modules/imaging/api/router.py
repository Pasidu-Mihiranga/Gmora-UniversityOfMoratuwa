"""HTTP routes for the `imaging` module. Stubs until issue M14 implements them (W0.8)."""

from suwapath.contracts.enums import Role
from suwapath.platform.http.stubs import StubRoute, stub_router

ROUTES = [
    StubRoute(
        "POST",
        "/patients/{patient_id}/imaging",
        (Role.PATIENT, Role.DOCTOR),
        "Upload an X-ray",
        "M14",
    ),
    StubRoute(
        "GET",
        "/imaging/{study_id}",
        (Role.PATIENT, Role.DOCTOR),
        "Screening status or result",
        "M14",
    ),
]

router = stub_router("imaging", ROUTES)
